import os
import time
import secrets
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from db import get_db_connection
from validadores import (
    ROL_ADMIN, ROL_RECEPCION, ROL_VETERINARIO, ROL_SUPERADMIN,
    registrar_auditoria, registrar_movimiento_inventario
)
from recepcion import recepcion_bp
from veterinario import veterinario_bp
from pagos import pagos_bp

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(24)
app.register_blueprint(recepcion_bp)
app.register_blueprint(veterinario_bp)
app.register_blueprint(pagos_bp)

INTENTOS_LOGIN = {}


@app.before_request
def proteger_csrf():
    if request.method == "POST":
        if not session.get("csrf_token") or session.get("csrf_token") != request.form.get("csrf_token"):
            flash("Sesión de formulario expirada. Intenta nuevamente.", "danger")
            return redirect(request.referrer or url_for("login"))


@app.context_processor
def inyectar_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return {"csrf_token": lambda: session["csrf_token"]}


@app.errorhandler(404)
def error_404(e):
    return render_template("error.html", codigo=404, titulo="Página no encontrada",
                           mensaje="La ruta solicitada no existe dentro de veterinaria_app."), 404


@app.errorhandler(500)
def error_500(e):
    return render_template("error.html", codigo=500, titulo="Error interno del servidor",
                           mensaje="Ocurrió un problema inesperado. Revisa la conexión o inténtalo de nuevo."), 500


@app.route("/")
def index():
    return redirect(url_for("panel") if "idusuario" in session else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario_form = (request.form.get("nombre_usuario") or "").strip()
        clave_form = request.form.get("contraseña") or ""
        ahora = time.time()
        reg = INTENTOS_LOGIN.get(usuario_form, {"count": 0, "until": 0})
        if reg["until"] > ahora:
            restante = int(reg["until"] - ahora)
            flash(f"Demasiados intentos fallidos. Espera {restante} segundos.", "warning")
            return render_template("login.html")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT u.idusuario, u.nombre_usuario, u.contraseña, u.idRol, u.idveterinario, r.nombre_rol
            FROM usuario u JOIN rol r ON u.idRol = r.idRol
            WHERE u.nombre_usuario = %s
            """,
            (usuario_form,),
        )
        user = cursor.fetchone()
        valido = False
        if user:
            guardada = user["contraseña"]
            if guardada.startswith(("scrypt:", "pbkdf2:")):
                valido = check_password_hash(guardada, clave_form)
            elif guardada == clave_form:
                valido = True
                cursor.execute(
                    "UPDATE usuario SET contraseña = %s WHERE idusuario = %s",
                    (generate_password_hash(clave_form), user["idusuario"]),
                )
                conn.commit()

        if valido and user["idRol"] == ROL_VETERINARIO and not user.get("idveterinario"):
            cursor.execute(
                "SELECT idveterinario FROM veterinario WHERE LOWER(nombre_veterinario) = LOWER(%s) LIMIT 1",
                (user["nombre_usuario"],),
            )
            vet_match = cursor.fetchone()
            if vet_match:
                cursor.execute(
                    "UPDATE usuario SET idveterinario = %s WHERE idusuario = %s",
                    (vet_match["idveterinario"], user["idusuario"]),
                )
                conn.commit()
                user["idveterinario"] = vet_match["idveterinario"]

        cursor.close()
        conn.close()

        if valido:
            INTENTOS_LOGIN.pop(usuario_form, None)
            session.clear()
            session["csrf_token"] = secrets.token_hex(16)
            user.pop("contraseña", None)
            session.update(user)
            return redirect(url_for("panel"))

        reg["count"] += 1
        if reg["count"] >= 5:
            reg["until"] = ahora + 60
            reg["count"] = 0
        INTENTOS_LOGIN[usuario_form] = reg
        flash("Usuario o contraseña incorrectos.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/panel")
def panel():
    if "idusuario" not in session:
        return redirect(url_for("login"))
    rol = session["idRol"]
    if rol in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("admin_panel"))
    if rol == ROL_RECEPCION:
        return redirect(url_for("recepcion.panel"))
    return redirect(url_for("veterinario.panel"))


@app.route("/admin")
def admin_panel():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT u.idusuario, u.nombre_usuario, u.idRol, u.idveterinario, r.nombre_rol, v.nombre_veterinario
        FROM usuario u
        JOIN rol r ON u.idRol = r.idRol
        LEFT JOIN veterinario v ON u.idveterinario = v.idveterinario
        ORDER BY u.idRol DESC, u.idusuario
        """
    )
    usuarios = cursor.fetchall()

    cursor.execute("SELECT * FROM rol ORDER BY idRol")
    roles = cursor.fetchall()

    cursor.execute(
        """
        SELECT v.*, s.nombre_sede
        FROM veterinario v JOIN sede s ON v.idSede = s.idSede
        ORDER BY v.idveterinario DESC
        """
    )
    veterinarios = cursor.fetchall()

    cursor.execute("SELECT * FROM sede ORDER BY nombre_sede")
    sedes = cursor.fetchall()

    cursor.execute("SELECT * FROM medicamento ORDER BY COALESCE(activo, 1) DESC, nombre_Medicamento")
    medicamentos = cursor.fetchall()

    cursor.execute("SELECT * FROM procedimiento ORDER BY COALESCE(activo, 1) DESC, nombre_Procedimiento")
    procedimientos = cursor.fetchall()

    cursor.execute(
        """
        SELECT s.nombre_sede, m.nombre_Medicamento, inv.stock, inv.fecha_vencimiento,
               DATEDIFF(inv.fecha_vencimiento, CURDATE()) AS dias_vence
        FROM inventario_sede inv
        JOIN sede s ON inv.idSede = s.idSede
        JOIN medicamento m ON inv.idMedicamento = m.idMedicamento
        ORDER BY inv.stock ASC, dias_vence ASC
        """
    )
    inventario = cursor.fetchall()

    cursor.execute(
        """
        SELECT mi.*, s.nombre_sede, m.nombre_Medicamento, u.nombre_usuario
        FROM movimiento_inventario mi
        JOIN sede s ON mi.idSede = s.idSede
        JOIN medicamento m ON mi.idMedicamento = m.idMedicamento
        LEFT JOIN usuario u ON mi.idusuario = u.idusuario
        ORDER BY mi.idmovimiento DESC LIMIT 60
        """
    )
    movimientos = cursor.fetchall()

    cursor.execute(
        """
        SELECT a.fecha_hora, u.nombre_usuario, a.accion, a.detalle
        FROM auditoria_log a
        JOIN usuario u ON a.idusuario = u.idusuario
        ORDER BY a.idauditoria DESC LIMIT 80
        """
    )
    auditorias = cursor.fetchall()

    cursor.close()
    conn.close()

    stock_bajo = sum(1 for i in inventario if (i["stock"] or 0) < 5)
    proximos_vencer = sum(
        1 for i in inventario if i["dias_vence"] is not None and i["dias_vence"] <= 30
    )

    cursor.execute(
        """
        SELECT m.`idmascota` AS idmascota, m.`nombre_mascota` AS nombre, m.`especie` AS especie, m.`raza` AS raza, COALESCE(p.`nombre_dueño`, 'Sin propietario') AS propietario
        FROM mascota m
        LEFT JOIN `dueño` p ON m.`id_dueño` = p.`id_dueño`
        ORDER BY m.`idmascota` DESC
        """
    )
    mascotas = cursor.fetchall()

    return render_template(
        "admin.html",
        mascotas=mascotas,
        usuarios=usuarios,
        roles=roles,
        veterinarios=veterinarios,
        sedes=sedes,
        medicamentos=medicamentos,
        procedimientos=procedimientos,
        inventario=inventario,
        movimientos=movimientos,
        stock_bajo=stock_bajo,
        proximos_vencer=proximos_vencer,
        auditorias=auditorias,
    )


@app.route("/admin/veterinario", methods=["POST"])
def admin_crear_veterinario():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))

    nombre_vet = (request.form.get("nombre_veterinario") or "").strip()
    especialidad = (request.form.get("especialidad") or "").strip()
    id_sede = request.form.get("idSede")
    crear_cuenta = request.form.get("crear_cuenta_usuario") == "1"
    nombre_usuario = (request.form.get("nombre_usuario") or "").strip()
    clave = request.form.get("contraseña") or ""

    if not nombre_vet or not especialidad or not id_sede:
        flash("Completa nombre del veterinario, especialidad y sede.", "warning")
        return redirect(url_for("admin_panel"))

    if crear_cuenta and (not nombre_usuario or len(clave) < 4):
        flash("Para crear la cuenta de acceso del veterinario, indica usuario y clave (mínimo 4 caracteres).", "warning")
        return redirect(url_for("admin_panel"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "INSERT INTO veterinario (nombre_veterinario, especialidad, idSede, activo) VALUES (%s, %s, %s, 1)",
            (nombre_vet, especialidad, id_sede),
        )
        id_vet_nuevo = cursor.lastrowid

        if crear_cuenta:
            cursor.execute(
                "INSERT INTO usuario (nombre_usuario, contraseña, idRol, idveterinario) VALUES (%s, %s, %s, %s)",
                (nombre_usuario, generate_password_hash(clave), ROL_VETERINARIO, id_vet_nuevo),
            )
            registrar_auditoria(
                cursor,
                session["idusuario"],
                "CREAR_VETERINARIO_ACCESO",
                f"Registró veterinario '{nombre_vet}' (#{id_vet_nuevo}) y cuenta '{nombre_usuario}'",
            )
        else:
            registrar_auditoria(
                cursor,
                session["idusuario"],
                "CREAR_VETERINARIO",
                f"Registró veterinario '{nombre_vet}' (#{id_vet_nuevo}) sin cuenta de usuario",
            )

        conn.commit()
        flash(
            "Veterinario registrado y vinculado con su usuario de acceso." if crear_cuenta
            else "Veterinario registrado. Recuerda vincular o crear su usuario para que pueda iniciar sesión.",
            "success",
        )
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error al registrar veterinario: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/veterinario/vincular/<int:idusuario>", methods=["POST"])
def admin_vincular_veterinario(idusuario):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    id_vet = request.form.get("idveterinario") or None
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE usuario SET idveterinario = %s WHERE idusuario = %s",
            (id_vet, idusuario),
        )
        registrar_auditoria(
            cursor,
            session["idusuario"],
            "VINCULAR_VETERINARIO",
            f"Vinculó usuario #{idusuario} a veterinario #{id_vet}",
        )
        conn.commit()
        flash("Vinculación de veterinario actualizada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/veterinario/toggle/<int:idveterinario>", methods=["POST"])
def admin_toggle_veterinario(idveterinario):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE veterinario SET activo = IF(COALESCE(activo, 1) = 1, 0, 1) WHERE idveterinario = %s",
            (idveterinario,),
        )
        cursor.execute(
            "UPDATE usuario SET idveterinario = NULL WHERE idveterinario = %s AND EXISTS (SELECT 1 FROM veterinario WHERE idveterinario = %s AND activo = 0)",
            (idveterinario, idveterinario),
        )
        registrar_auditoria(
            cursor,
            session["idusuario"],
            "TOGGLE_VETERINARIO",
            f"Cambió estado activo de veterinario #{idveterinario}",
        )
        conn.commit()
        flash("Estado del veterinario actualizado.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/usuario", methods=["POST"])
def admin_crear_usuario():
    rol_actual = session.get("idRol")
    if rol_actual not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    nuevo_rol = int(request.form["idRol"])
    if rol_actual == ROL_ADMIN and nuevo_rol in (ROL_ADMIN, ROL_SUPERADMIN):
        flash("Solo el Super Admin puede crear cuentas de Administrador.", "warning")
        return redirect(url_for("admin_panel"))

    if nuevo_rol == ROL_VETERINARIO:
        flash("Los veterinarios y su cuenta se crean desde el formulario 'Nuevo Veterinario'.", "warning")
        return redirect(url_for("admin_panel"))
    id_vet = None

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO usuario (nombre_usuario, contraseña, idRol, idveterinario) VALUES (%s, %s, %s, %s)",
            (
                request.form["nombre_usuario"].strip(),
                generate_password_hash(request.form["contraseña"]),
                nuevo_rol,
                id_vet if nuevo_rol == ROL_VETERINARIO else None,
            ),
        )
        registrar_auditoria(
            cursor,
            session["idusuario"],
            "CREAR_USUARIO",
            f"Creó usuario '{request.form['nombre_usuario']}' (Rol ID {nuevo_rol}, Vet ID {id_vet})",
        )
        conn.commit()
        flash("Usuario creado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/usuario/clave/<int:idusuario>", methods=["POST"])
def admin_cambiar_clave(idusuario):
    if session.get("idRol") != ROL_SUPERADMIN:
        flash("Solo el Super Admin puede modificar contraseñas.", "danger")
        return redirect(url_for("admin_panel"))
    nueva_clave = (request.form.get("nueva_contraseña") or "").strip()
    clave_super = request.form.get("clave_superadmin") or ""
    if len(nueva_clave) < 4:
        flash("La nueva contraseña debe tener al menos 4 caracteres.", "warning")
        return redirect(url_for("admin_panel"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT contraseña FROM usuario WHERE idusuario = %s", (session["idusuario"],))
        sa = cursor.fetchone()
        valido = sa and (
            check_password_hash(sa["contraseña"], clave_super)
            if sa["contraseña"].startswith(("scrypt:", "pbkdf2:"))
            else sa["contraseña"] == clave_super
        )
        if not valido:
            flash("Confirmación denegada: tu contraseña actual de Super Admin es incorrecta.", "danger")
            return redirect(url_for("admin_panel"))
        cursor.execute("SELECT nombre_usuario FROM usuario WHERE idusuario = %s", (idusuario,))
        objetivo = cursor.fetchone()
        cursor.execute(
            "UPDATE usuario SET contraseña = %s WHERE idusuario = %s",
            (generate_password_hash(nueva_clave), idusuario),
        )
        registrar_auditoria(
            cursor,
            session["idusuario"],
            "CAMBIO_CLAVE",
            f"Modificó contraseña de '{objetivo['nombre_usuario']}' (ID #{idusuario})",
        )
        conn.commit()
        flash("Contraseña actualizada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/usuario/eliminar/<int:idusuario>", methods=["POST"])
def admin_eliminar_usuario(idusuario):
    rol_actual = session.get("idRol")
    if rol_actual not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    if idusuario == session.get("idusuario"):
        flash("No puedes eliminar tu propio usuario en sesión.", "warning")
        return redirect(url_for("admin_panel"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT idRol, nombre_usuario FROM usuario WHERE idusuario = %s", (idusuario,))
        objetivo = cursor.fetchone()
        if objetivo and rol_actual == ROL_ADMIN and objetivo["idRol"] in (ROL_ADMIN, ROL_SUPERADMIN):
            flash("Un Administrador no puede eliminar a otro Administrador ni al Super Admin.", "danger")
            return redirect(url_for("admin_panel"))
        cursor.execute("DELETE FROM usuario WHERE idusuario = %s", (idusuario,))
        registrar_auditoria(
            cursor,
            session["idusuario"],
            "ELIMINAR_USUARIO",
            f"Eliminó usuario '{objetivo['nombre_usuario']}' (ID #{idusuario})",
        )
        conn.commit()
        flash("Usuario eliminado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(
            "No se puede eliminar este usuario porque tiene historial registrado."
            if err.errno == 1451
            else f"Error: {err.msg}",
            "danger",
        )
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/stock", methods=["POST"])
def admin_stock():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    id_sede = int(request.form["idSede"])
    id_med = int(request.form["idMedicamento"])
    cantidad = int(request.form["cantidad"])
    fecha_venc = request.form.get("fecha_vencimiento") or None

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT stock FROM inventario_sede WHERE idSede = %s AND idMedicamento = %s",
            (id_sede, id_med),
        )
        actual = cursor.fetchone()
        stock_ant = int(actual["stock"]) if actual else 0
        stock_nuevo = stock_ant + cantidad

        cursor.execute(
            """
            INSERT INTO inventario_sede (idSede, idMedicamento, stock, fecha_vencimiento)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stock = stock + VALUES(stock),
                fecha_vencimiento = CASE
                    WHEN VALUES(fecha_vencimiento) IS NULL THEN fecha_vencimiento
                    WHEN fecha_vencimiento IS NULL THEN VALUES(fecha_vencimiento)
                    ELSE LEAST(fecha_vencimiento, VALUES(fecha_vencimiento))
                END
            """,
            (id_sede, id_med, cantidad, fecha_venc),
        )
        registrar_movimiento_inventario(
            cursor, id_sede, id_med, "ENTRADA", cantidad,
            stock_ant, stock_nuevo, referencia="Abastecimiento admin",
            idusuario=session["idusuario"]
        )
        conn.commit()
        flash("Inventario actualizado y movimiento registrado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/medicamento", methods=["POST"])
def admin_medicamento():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO medicamento (nombre_Medicamento, precio, activo) VALUES (%s, %s, 1)",
            (request.form["nombre"].strip(), request.form["precio"]),
        )
        conn.commit()
        flash("Medicamento registrado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/medicamento/precio/<int:idmed>", methods=["POST"])
def admin_precio_medicamento(idmed):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE medicamento SET precio = %s WHERE idMedicamento = %s",
            (request.form["precio"], idmed),
        )
        conn.commit()
        flash("Precio actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/medicamento/toggle/<int:idmed>", methods=["POST"])
def admin_toggle_medicamento(idmed):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE medicamento SET activo = IF(COALESCE(activo, 1) = 1, 0, 1) WHERE idMedicamento = %s",
            (idmed,),
        )
        conn.commit()
        flash("Estado del medicamento actualizado.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/procedimiento", methods=["POST"])
def admin_procedimiento():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO procedimiento (nombre_Procedimiento, descripcion_procedimiento, costo, activo)
            VALUES (%s, %s, %s, 1)
            """,
            (
                request.form["nombre"].strip(),
                (request.form.get("descripcion") or "").strip(),
                request.form["costo"],
            ),
        )
        conn.commit()
        flash("Procedimiento registrado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/procedimiento/precio/<int:idproc>", methods=["POST"])
def admin_precio_procedimiento(idproc):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE procedimiento SET costo = %s WHERE idProcedimiento = %s",
            (request.form["costo"], idproc),
        )
        conn.commit()
        flash("Costo actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/procedimiento/toggle/<int:idproc>", methods=["POST"])
def admin_toggle_procedimiento(idproc):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE procedimiento SET activo = IF(COALESCE(activo, 1) = 1, 0, 1) WHERE idProcedimiento = %s",
            (idproc,),
        )
        conn.commit()
        flash("Estado del procedimiento actualizado.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/mascota/eliminar/<int:idmascota>", methods=["POST"])
def admin_eliminar_mascota(idmascota):
    rol_actual = session.get("idRol")
    if rol_actual not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT `nombre_mascota` AS nombre FROM mascota WHERE `idmascota` = %s", (idmascota,))
        masc = cursor.fetchone()
        if not masc:
            flash("La mascota no existe.", "warning")
            return redirect(url_for("admin_panel"))

        if rol_actual == ROL_SUPERADMIN:
                cursor.execute("DELETE FROM `vacunacion` WHERE `idmascota` = %s", (idmascota,))
                cursor.execute("DELETE FROM `vacuna` WHERE `idmascota` = %s", (idmascota,))
                cursor.execute("DELETE FROM `tratamiento_medicamento` WHERE `idtratamiento` IN (SELECT `idtratamiento` FROM `tratamiento` WHERE `idcita` IN (SELECT `idcita` FROM `cita` WHERE `idmascota` = %s))", (idmascota,))
                cursor.execute("DELETE FROM `tratamiento` WHERE `idcita` IN (SELECT `idcita` FROM `cita` WHERE `idmascota` = %s)", (idmascota,))
                cursor.execute("DELETE FROM `signos_vitales` WHERE `idcita` IN (SELECT `idcita` FROM `cita` WHERE `idmascota` = %s)", (idmascota,))
                cursor.execute("DELETE FROM `pago` WHERE `idcita` IN (SELECT `idcita` FROM `cita` WHERE `idmascota` = %s)", (idmascota,))
                cursor.execute("DELETE FROM `cita_procedimiento` WHERE `idcita` IN (SELECT `idcita` FROM `cita` WHERE `idmascota` = %s)", (idmascota,))
                cursor.execute("DELETE FROM `cita` WHERE `idmascota` = %s", (idmascota,))
                cursor.execute("DELETE FROM `mascota` WHERE `idmascota` = %s", (idmascota,))
        else:
            cursor.execute("DELETE FROM mascota WHERE `idmascota` = %s", (idmascota,))

        registrar_auditoria(
            cursor,
            session["idusuario"],
            "ELIMINAR_MASCOTA",
            f"Eliminó mascota '{masc['nombre']}' (ID #{idmascota})",
        )
        conn.commit()
        flash("Mascota eliminada correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        if err.errno == 1451:
            flash("No se puede eliminar porque tiene citas o vacunas registradas; solo el Super Admin puede forzar su borrado.", "warning")
        else:
            flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/sede", methods=["POST"])
def admin_sede():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    nombre = (request.form.get("nombre_sede") or "").strip()
    direccion = (request.form.get("direccion_sede") or "").strip()
    ciudad = (request.form.get("ciudad") or "").strip()
    telefono = (request.form.get("telefono_sede") or "").strip()
    if not nombre or not direccion or not ciudad or not telefono:
        flash("Completa nombre, dirección, ciudad y teléfono de la sede.", "warning")
        return redirect(url_for("admin_panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO sede (nombre_sede, direccion_sede, ciudad, telefono_sede) VALUES (%s, %s, %s, %s)",
            (nombre, direccion, ciudad, telefono),
        )
        registrar_auditoria(cursor, session["idusuario"], "CREAR_SEDE", f"Creó sede '{nombre}' en {ciudad}")
        conn.commit()
        flash("Sede creada correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))



@app.route("/admin/veterinario/eliminar/<int:idveterinario>", methods=["POST"])
def admin_eliminar_veterinario(idveterinario):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT nombre_veterinario FROM veterinario WHERE idveterinario = %s", (idveterinario,))
        vet = cursor.fetchone()
        cursor.execute("UPDATE usuario SET idveterinario = NULL WHERE idveterinario = %s", (idveterinario,))
        cursor.execute("DELETE FROM veterinario WHERE idveterinario = %s", (idveterinario,))
        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_VETERINARIO", f"Eliminó veterinario '{vet['nombre_veterinario'] if vet else idveterinario}'")
        conn.commit()
        flash("Veterinario eliminado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash("No se puede eliminar porque tiene citas registradas; usa Desactivar para ocultarlo." if err.errno == 1451 else f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


@app.route("/admin/medicamento/eliminar/<int:idmed>", methods=["POST"])
def admin_eliminar_medicamento(idmed):
    if session.get("idRol") != ROL_SUPERADMIN:
        flash("Solo el Super Admin puede eliminar medicamentos.", "danger")
        return redirect(url_for("admin_panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT nombre_Medicamento FROM medicamento WHERE idMedicamento = %s", (idmed,))
        med = cursor.fetchone()
        cursor.execute("DELETE FROM inventario_sede WHERE idMedicamento = %s", (idmed,))
        cursor.execute("DELETE FROM movimiento_inventario WHERE idMedicamento = %s", (idmed,))
        cursor.execute("DELETE FROM medicamento WHERE idMedicamento = %s", (idmed,))
        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_MEDICAMENTO", f"Eliminó medicamento '{med['nombre_Medicamento'] if med else idmed}' y su stock")
        conn.commit()
        flash("Medicamento y su stock en sedes eliminados correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash("No se puede eliminar porque ya fue recetado en consultas; usa Desactivar y ajusta stock a 0." if err.errno == 1451 else f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("admin_panel"))


if __name__ == "__main__":
    app.run(debug=True)
