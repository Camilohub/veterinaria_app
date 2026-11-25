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



def redirigir_admin(tab_defecto="tab-personal"):
    tab = (request.form.get("tab") or request.args.get("tab") or tab_defecto).strip()
    if not tab.startswith("tab-"):
        tab = f"tab-{tab}"
    return redirect(url_for("admin_panel", tab=tab))


def _formatear_celda_sql(val):
    if val is None:
        return "NULL"
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="replace")
    return str(val)


@app.route("/admin")
def admin_panel():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))

    from datetime import date, timedelta
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

    cursor.execute(
        """
        SELECT m.idmascota AS idmascota, m.nombre_mascota AS nombre, m.especie AS especie,
               m.raza AS raza, sx.descripcion AS sexo, COALESCE(p.nombre_dueño, 'Sin propietario') AS propietario
        FROM mascota m
        LEFT JOIN sexo sx ON m.idsexo = sx.idsexo
        LEFT JOIN `dueño` p ON m.id_dueño = p.id_dueño
        ORDER BY m.idmascota DESC
        """
    )
    mascotas = cursor.fetchall()

    esquema_tablas = {}
    if session.get("idRol") == ROL_SUPERADMIN:
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """
        )
        for col in cursor.fetchall():
            esquema_tablas.setdefault(col["TABLE_NAME"], []).append(col)

    cursor.close()
    conn.close()

    stock_bajo = sum(1 for i in inventario if (i["stock"] or 0) < 5)
    proximos_vencer = sum(1 for i in inventario if i["dias_vence"] is not None and i["dias_vence"] <= 30)
    min_fecha_venc = (date.today() + timedelta(days=3)).isoformat()
    max_fecha_venc = (date.today() + timedelta(days=3650)).isoformat()
    sql_resultado = session.pop("sql_consola_resultado", None)

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
        min_fecha_venc=min_fecha_venc,
        max_fecha_venc=max_fecha_venc,
        esquema_tablas=esquema_tablas,
        sql_resultado=sql_resultado,
    )


@app.route("/admin/consola-sql", methods=["POST"])
def admin_consola_sql():
    if session.get("idRol") != ROL_SUPERADMIN:
        flash("Acceso denegado: la Consola SQL es exclusiva del Super Admin.", "danger")
        return redirect(url_for("panel"))

    from validadores import validar_sql_consola
    sql_raw = request.form.get("sql_query") or ""
    ok, res = validar_sql_consola(sql_raw)
    if not ok:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            registrar_auditoria(cur, session["idusuario"], "SQL_CONSOLA_BLOQUEADA", f"Bloqueado: {res} | {sql_raw[:140]}")
            conn.commit()
        finally:
            cur.close()
            conn.close()
        session["sql_consola_resultado"] = {"ok": False, "sql": sql_raw, "error": res}
        return redirigir_admin("tab-consola")

    sql_limpio = res
    tipo_op = sql_limpio.split()[0].upper()
    t0 = time.time()
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql_limpio)
        duracion_ms = round((time.time() - t0) * 1000, 1)
        if tipo_op in ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"):
            columnas = [d[0] for d in (cur.description or [])]
            filas_raw = cur.fetchmany(250)
            extra_rows = cur.fetchall()
            filas = [[_formatear_celda_sql(c) for c in fila] for fila in filas_raw]
            registrar_auditoria(cur, session["idusuario"], "SQL_CONSOLA_SELECT", sql_limpio[:240])
            conn.commit()
            session["sql_consola_resultado"] = {
                "ok": True,
                "tipo": "LECTURA",
                "sql": sql_limpio,
                "columnas": columnas,
                "filas": filas,
                "total_mostradas": len(filas),
                "truncado": len(extra_rows) > 0,
                "ms": duracion_ms,
            }
        else:
            filas_afectadas = cur.rowcount
            ultimo_id = cur.lastrowid
            registrar_auditoria(cur, session["idusuario"], "SQL_CONSOLA_INSERT", sql_limpio[:240])
            conn.commit()
            session["sql_consola_resultado"] = {
                "ok": True,
                "tipo": "INSERCION",
                "sql": sql_limpio,
                "filas_afectadas": filas_afectadas,
                "ultimo_id": ultimo_id,
                "ms": duracion_ms,
            }
    except mysql.connector.Error as err:
        conn.rollback()
        session["sql_consola_resultado"] = {"ok": False, "sql": sql_limpio, "error": f"MySQL ({err.errno}): {err.msg}"}
    finally:
        cur.close()
        conn.close()
    return redirigir_admin("tab-consola")


@app.route("/admin/veterinario", methods=["POST"])
def admin_crear_veterinario():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))

    import re
    from validadores import validar_nombre_persona, validar_telefono_co, _parece_teclazo
    nombre_raw = request.form.get("nombre_veterinario") or ""
    especialidad = " ".join((request.form.get("especialidad") or "").strip().split())
    tel_raw = request.form.get("telefono") or ""
    id_sede = request.form.get("idSede")
    crear_cuenta = request.form.get("crear_cuenta_usuario") == "1"
    nombre_usuario = (request.form.get("nombre_usuario") or "").strip()
    clave = request.form.get("contraseña") or ""

    ok_nom, res_nom = validar_nombre_persona(nombre_raw, "El nombre del veterinario")
    if not ok_nom:
        flash(res_nom, "warning")
        return redirigir_admin("tab-personal")

    if len(especialidad) < 4 or len(especialidad) > 60 or not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s.-]+", especialidad) or _parece_teclazo(especialidad):
        flash("Ingresa una especialidad veterinaria válida (ej. Medicina General, Cirugía).", "warning")
        return redirigir_admin("tab-personal")

    ok_tel, res_tel = validar_telefono_co(tel_raw)
    if not ok_tel:
        flash(res_tel, "warning")
        return redirigir_admin("tab-personal")

    if not id_sede:
        flash("Selecciona una sede para el veterinario.", "warning")
        return redirigir_admin("tab-personal")

    if crear_cuenta:
        if not re.fullmatch(r"[A-Za-z0-9._-]{4,35}", nombre_usuario):
            flash("El usuario de acceso debe tener entre 4 y 35 caracteres (letras, números, punto o guion).", "warning")
            return redirigir_admin("tab-personal")
        if len(clave) < 4:
            flash("La contraseña debe tener al menos 4 caracteres.", "warning")
            return redirigir_admin("tab-personal")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "INSERT INTO veterinario (nombre_veterinario, especialidad, telefono, idSede, activo) VALUES (%s, %s, %s, %s, 1)",
            (res_nom, especialidad, res_tel, id_sede),
        )
        id_vet_nuevo = cursor.lastrowid
        if crear_cuenta:
            cursor.execute(
                "INSERT INTO usuario (nombre_usuario, contraseña, idRol, idveterinario) VALUES (%s, %s, %s, %s)",
                (nombre_usuario, generate_password_hash(clave), ROL_VETERINARIO, id_vet_nuevo),
            )
            registrar_auditoria(cursor, session["idusuario"], "CREAR_VETERINARIO_ACCESO", f"Registró veterinario '{res_nom}' (#{id_vet_nuevo}) y cuenta '{nombre_usuario}'")
        else:
            registrar_auditoria(cursor, session["idusuario"], "CREAR_VETERINARIO", f"Registró veterinario '{res_nom}' (#{id_vet_nuevo})")
        conn.commit()
        flash("Veterinario registrado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error al registrar veterinario: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-personal")


@app.route("/admin/veterinario/vincular/<int:idusuario>", methods=["POST"])
def admin_vincular_veterinario(idusuario):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    id_vet = request.form.get("idveterinario") or None
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE usuario SET idveterinario = %s WHERE idusuario = %s", (id_vet, idusuario))
        registrar_auditoria(cursor, session["idusuario"], "VINCULAR_VETERINARIO", f"Vinculó usuario #{idusuario} a veterinario #{id_vet}")
        conn.commit()
        flash("Vinculación de veterinario actualizada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-personal")


@app.route("/admin/veterinario/toggle/<int:idveterinario>", methods=["POST"])
def admin_toggle_veterinario(idveterinario):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT COALESCE(activo, 1) AS activo, nombre_veterinario FROM veterinario WHERE idveterinario = %s", (idveterinario,))
        vet = cursor.fetchone()
        if vet and vet["activo"] == 1:
            cursor.execute(
                """
                SELECT COUNT(*) AS pendientes FROM cita
                WHERE idveterinario = %s AND fecha_hora >= NOW() AND idEstado_cita NOT IN (8, 9, 10)
                """,
                (idveterinario,),
            )
            pend = (cursor.fetchone() or {}).get("pendientes", 0)
            if pend > 0:
                flash(f"No se puede desactivar a {vet['nombre_veterinario']} porque tiene {pend} cita(s) futura(s) pendiente(s). Reprográmalas o cancélalas primero.", "warning")
                return redirigir_admin("tab-personal")

        cursor.execute("UPDATE veterinario SET activo = IF(COALESCE(activo, 1) = 1, 0, 1) WHERE idveterinario = %s", (idveterinario,))
        cursor.execute(
            "UPDATE usuario SET idveterinario = NULL WHERE idveterinario = %s AND EXISTS (SELECT 1 FROM veterinario WHERE idveterinario = %s AND activo = 0)",
            (idveterinario, idveterinario),
        )
        registrar_auditoria(cursor, session["idusuario"], "TOGGLE_VETERINARIO", f"Cambió estado activo de veterinario #{idveterinario}")
        conn.commit()
        flash("Estado del veterinario actualizado.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-personal")


@app.route("/admin/usuario", methods=["POST"])
def admin_crear_usuario():
    import re
    from validadores import _parece_teclazo
    rol_actual = session.get("idRol")
    if rol_actual not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    nuevo_rol = int(request.form["idRol"])
    if rol_actual == ROL_ADMIN and nuevo_rol in (ROL_ADMIN, ROL_SUPERADMIN):
        flash("Solo el Super Admin puede crear cuentas de Administrador.", "warning")
        return redirigir_admin("tab-personal")
    if nuevo_rol == ROL_VETERINARIO:
        flash("Los veterinarios y su cuenta se crean desde el formulario 'Registrar Veterinario'.", "warning")
        return redirigir_admin("tab-personal")

    usuario_nuevo = (request.form.get("nombre_usuario") or "").strip()
    clave_nueva = request.form.get("contraseña") or ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{4,35}", usuario_nuevo) or _parece_teclazo(usuario_nuevo):
        flash("Ingresa un nombre de usuario válido (4 a 35 caracteres, sin cadenas aleatorias).", "warning")
        return redirigir_admin("tab-personal")
    if len(clave_nueva) < 4:
        flash("La contraseña debe tener al menos 4 caracteres.", "warning")
        return redirigir_admin("tab-personal")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO usuario (nombre_usuario, contraseña, idRol, idveterinario) VALUES (%s, %s, %s, NULL)",
            (usuario_nuevo, generate_password_hash(clave_nueva), nuevo_rol),
        )
        registrar_auditoria(cursor, session["idusuario"], "CREAR_USUARIO", f"Creó usuario '{usuario_nuevo}' (Rol ID {nuevo_rol})")
        conn.commit()
        flash("Usuario creado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-personal")


@app.route("/admin/usuario/clave/<int:idusuario>", methods=["POST"])
def admin_cambiar_clave(idusuario):
    if session.get("idRol") != ROL_SUPERADMIN:
        flash("Solo el Super Admin puede modificar contraseñas.", "danger")
        return redirigir_admin("tab-personal")
    nueva_clave = (request.form.get("nueva_contraseña") or "").strip()
    clave_super = request.form.get("clave_superadmin") or ""
    if len(nueva_clave) < 4:
        flash("La nueva contraseña debe tener al menos 4 caracteres.", "warning")
        return redirigir_admin("tab-personal")

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
            return redirigir_admin("tab-personal")
        cursor.execute("SELECT nombre_usuario FROM usuario WHERE idusuario = %s", (idusuario,))
        objetivo = cursor.fetchone()
        cursor.execute("UPDATE usuario SET contraseña = %s WHERE idusuario = %s", (generate_password_hash(nueva_clave), idusuario))
        registrar_auditoria(cursor, session["idusuario"], "CAMBIO_CLAVE", f"Modificó contraseña de '{objetivo['nombre_usuario']}' (ID #{idusuario})")
        conn.commit()
        flash("Contraseña actualizada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-personal")


@app.route("/admin/usuario/eliminar/<int:idusuario>", methods=["POST"])
def admin_eliminar_usuario(idusuario):
    rol_actual = session.get("idRol")
    if rol_actual not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    if idusuario == session.get("idusuario"):
        flash("No puedes eliminar tu propio usuario en sesión.", "warning")
        return redirigir_admin("tab-personal")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT idRol, nombre_usuario FROM usuario WHERE idusuario = %s", (idusuario,))
        objetivo = cursor.fetchone()
        if objetivo and rol_actual == ROL_ADMIN and objetivo["idRol"] in (ROL_ADMIN, ROL_SUPERADMIN):
            flash("Un Administrador no puede eliminar a otro Administrador ni al Super Admin.", "danger")
            return redirigir_admin("tab-personal")
        cursor.execute("DELETE FROM usuario WHERE idusuario = %s", (idusuario,))
        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_USUARIO", f"Eliminó usuario '{objetivo['nombre_usuario']}' (ID #{idusuario})")
        conn.commit()
        flash("Usuario eliminado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash("No se puede eliminar este usuario porque tiene historial registrado." if err.errno == 1451 else f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-personal")


@app.route("/admin/stock", methods=["POST"])
def admin_stock():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    from datetime import date, timedelta
    try:
        id_sede = int(request.form["idSede"])
        id_med = int(request.form["idMedicamento"])
        cantidad = int(request.form["cantidad"])
    except (KeyError, ValueError):
        flash("Datos de abastecimiento inválidos.", "warning")
        return redirigir_admin("tab-inventario")

    fecha_venc = (request.form.get("fecha_vencimiento") or "").strip()
    if not (1 <= cantidad <= 1000):
        flash("La cantidad a ingresar debe estar entre 1 y 1.000 unidades.", "warning")
        return redirigir_admin("tab-inventario")
    if not fecha_venc:
        flash("Debes indicar la fecha de vencimiento del lote.", "warning")
        return redirigir_admin("tab-inventario")
    try:
        fecha_dt = date.fromisoformat(fecha_venc)
    except ValueError:
        flash("Fecha de vencimiento inválida.", "danger")
        return redirigir_admin("tab-inventario")
    if fecha_dt < date.today() + timedelta(days=3):
        flash("No se puede ingresar stock con fecha pasada, vencida o con menos de 3 días de vigencia.", "danger")
        return redirigir_admin("tab-inventario")
    if fecha_dt > date.today() + timedelta(days=3650):
        flash("La fecha de vencimiento no puede superar los 10 años.", "warning")
        return redirigir_admin("tab-inventario")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT stock FROM inventario_sede WHERE idSede = %s AND idMedicamento = %s", (id_sede, id_med))
        actual = cursor.fetchone()
        stock_ant = int(actual["stock"]) if actual else 0
        stock_nuevo = stock_ant + cantidad
        if stock_nuevo > 1000:
            flash(f"El stock total en sede no puede superar 1.000 unidades (stock actual: {stock_ant}).", "warning")
            return redirigir_admin("tab-inventario")
        cursor.execute(
            """
            INSERT INTO inventario_sede (idSede, idMedicamento, stock, fecha_vencimiento)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stock = stock + VALUES(stock),
                fecha_vencimiento = CASE
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
    return redirigir_admin("tab-inventario")


@app.route("/admin/medicamento", methods=["POST"])
def admin_medicamento():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    from validadores import validar_item_catalogo
    ok_nom, res_nom = validar_item_catalogo(request.form.get("nombre"), "El nombre del medicamento")
    if not ok_nom:
        flash(res_nom, "warning")
        return redirigir_admin("tab-inventario")
    try:
        precio = float(request.form.get("precio") or 0)
    except ValueError:
        precio = 0
    if not (100 <= precio <= 500000):
        flash("El precio del medicamento debe estar entre $100 y $500.000 COP.", "warning")
        return redirigir_admin("tab-inventario")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO medicamento (nombre_Medicamento, precio, activo) VALUES (%s, %s, 1)", (res_nom, precio))
        registrar_auditoria(cursor, session["idusuario"], "CREAR_MEDICAMENTO", f"Registró medicamento '{res_nom}' (${precio:,.0f})")
        conn.commit()
        flash("Medicamento registrado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


@app.route("/admin/medicamento/precio/<int:idmed>", methods=["POST"])
def admin_precio_medicamento(idmed):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    try:
        precio = float(request.form.get("precio") or 0)
    except ValueError:
        precio = 0
    if not (100 <= precio <= 500000):
        flash("El precio del medicamento debe estar entre $100 y $500.000 COP.", "warning")
        return redirigir_admin("tab-inventario")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE medicamento SET precio = %s WHERE idMedicamento = %s", (precio, idmed))
        conn.commit()
        flash("Precio actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


@app.route("/admin/medicamento/toggle/<int:idmed>", methods=["POST"])
def admin_toggle_medicamento(idmed):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE medicamento SET activo = IF(COALESCE(activo, 1) = 1, 0, 1) WHERE idMedicamento = %s", (idmed,))
        conn.commit()
        flash("Estado del medicamento actualizado.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


@app.route("/admin/procedimiento", methods=["POST"])
def admin_procedimiento():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    from validadores import validar_item_catalogo, validar_descripcion_opcional
    ok_nom, res_nom = validar_item_catalogo(request.form.get("nombre"), "El nombre del procedimiento")
    if not ok_nom:
        flash(res_nom, "warning")
        return redirigir_admin("tab-inventario")
    ok_desc, res_desc = validar_descripcion_opcional(request.form.get("descripcion"), "La descripción")
    if not ok_desc:
        flash(res_desc, "warning")
        return redirigir_admin("tab-inventario")
    try:
        costo = float(request.form.get("costo") or 0)
    except ValueError:
        costo = 0
    if not (1000 <= costo <= 800000):
        flash("El costo del procedimiento debe estar entre $1.000 y $800.000 COP.", "warning")
        return redirigir_admin("tab-inventario")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO procedimiento (nombre_Procedimiento, descripcion_procedimiento, costo, activo) VALUES (%s, %s, %s, 1)",
            (res_nom, res_desc, costo),
        )
        registrar_auditoria(cursor, session["idusuario"], "CREAR_PROCEDIMIENTO", f"Registró procedimiento '{res_nom}' (${costo:,.0f})")
        conn.commit()
        flash("Procedimiento registrado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


@app.route("/admin/procedimiento/precio/<int:idproc>", methods=["POST"])
def admin_precio_procedimiento(idproc):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    try:
        costo = float(request.form.get("costo") or 0)
    except ValueError:
        costo = 0
    if not (1000 <= costo <= 800000):
        flash("El costo del procedimiento debe estar entre $1.000 y $800.000 COP.", "warning")
        return redirigir_admin("tab-inventario")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE procedimiento SET costo = %s WHERE idProcedimiento = %s", (costo, idproc))
        conn.commit()
        flash("Costo actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


@app.route("/admin/procedimiento/toggle/<int:idproc>", methods=["POST"])
def admin_toggle_procedimiento(idproc):
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE procedimiento SET activo = IF(COALESCE(activo, 1) = 1, 0, 1) WHERE idProcedimiento = %s", (idproc,))
        conn.commit()
        flash("Estado del procedimiento actualizado.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


@app.route("/admin/procedimiento/eliminar/<int:idproc>", methods=["POST"])
def admin_eliminar_procedimiento(idproc):
    if session.get("idRol") != ROL_SUPERADMIN:
        flash("Solo el Super Admin puede eliminar procedimientos.", "danger")
        return redirigir_admin("tab-inventario")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT nombre_Procedimiento FROM procedimiento WHERE idProcedimiento = %s", (idproc,))
        pr = cursor.fetchone()
        cursor.execute("DELETE FROM cita_procedimiento WHERE idProcedimiento = %s", (idproc,))
        cursor.execute("DELETE FROM procedimiento WHERE idProcedimiento = %s", (idproc,))
        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_PROCEDIMIENTO", f"Eliminó procedimiento '{pr['nombre_Procedimiento'] if pr else idproc}'")
        conn.commit()
        flash("Procedimiento eliminado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error al eliminar procedimiento: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


@app.route("/admin/mascota/eliminar/<int:idmascota>", methods=["POST"])
def admin_eliminar_mascota(idmascota):
    rol_actual = session.get("idRol")
    if rol_actual not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT nombre_mascota AS nombre FROM mascota WHERE idmascota = %s", (idmascota,))
        masc = cursor.fetchone()
        if not masc:
            flash("La mascota no existe.", "warning")
            return redirigir_admin("tab-sedes")

        if rol_actual == ROL_SUPERADMIN:
            cursor.execute("DELETE FROM vacunacion WHERE idmascota = %s", (idmascota,))
            cursor.execute("DELETE FROM vacuna WHERE idmascota = %s", (idmascota,))
            cursor.execute("DELETE FROM tratamiento_medicamento WHERE idtratamiento IN (SELECT idtratamiento FROM tratamiento WHERE idcita IN (SELECT idcita FROM cita WHERE idmascota = %s))", (idmascota,))
            cursor.execute("DELETE FROM tratamiento WHERE idcita IN (SELECT idcita FROM cita WHERE idmascota = %s)", (idmascota,))
            cursor.execute("DELETE FROM signos_vitales WHERE idcita IN (SELECT idcita FROM cita WHERE idmascota = %s)", (idmascota,))
            cursor.execute("DELETE FROM pago WHERE idcita IN (SELECT idcita FROM cita WHERE idmascota = %s)", (idmascota,))
            cursor.execute("DELETE FROM cita_procedimiento WHERE idcita IN (SELECT idcita FROM cita WHERE idmascota = %s)", (idmascota,))
            cursor.execute("DELETE FROM cita WHERE idmascota = %s", (idmascota,))
            cursor.execute("DELETE FROM mascota WHERE idmascota = %s", (idmascota,))
        else:
            cursor.execute("DELETE FROM mascota WHERE idmascota = %s", (idmascota,))

        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_MASCOTA", f"Eliminó mascota '{masc['nombre']}' (ID #{idmascota})")
        conn.commit()
        flash("Mascota eliminada correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash("No se puede eliminar porque tiene citas o vacunas registradas; solo el Super Admin puede forzar su borrado." if err.errno == 1451 else f"Error: {err.msg}", "warning")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-sedes")


@app.route("/admin/sede", methods=["POST"])
def admin_sede():
    if session.get("idRol") not in (ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    import re
    from validadores import construir_direccion_co, validar_telefono_co, _parece_teclazo
    nombre = " ".join((request.form.get("nombre_sede") or "").strip().split())
    ciudad = "Bogotá D.C."
    tel_raw = re.sub(r"[\s\-()+]", "", request.form.get("telefono_sede") or "")

    if len(nombre) < 3 or len(nombre) > 60 or _parece_teclazo(nombre):
        flash("Ingresa un nombre de sede válido.", "warning")
        return redirigir_admin("tab-sedes")

    ok_dir, res_dir = construir_direccion_co(
        request.form.get("via_tipo"),
        request.form.get("via_num"),
        request.form.get("via_letra"),
        request.form.get("num_sec"),
        request.form.get("sec_letra"),
        request.form.get("placa"),
        request.form.get("complemento"),
    )
    if not ok_dir:
        flash(res_dir, "warning")
        return redirigir_admin("tab-sedes")

    ok_tel, res_tel = validar_telefono_co(tel_raw)
    if not ok_tel and not (re.fullmatch(r"601[0-9]{7}", tel_raw) and len(set(tel_raw[3:])) > 1):
        flash("Ingresa un celular (3XXXXXXXXX) o teléfono fijo de Bogotá válido (601XXXXXXX).", "warning")
        return redirigir_admin("tab-sedes")
    tel_final = res_tel if ok_tel else tel_raw

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO sede (nombre_sede, direccion_sede, ciudad, telefono_sede) VALUES (%s, %s, %s, %s)",
            (nombre, res_dir, ciudad, tel_final),
        )
        registrar_auditoria(cursor, session["idusuario"], "CREAR_SEDE", f"Creó sede '{nombre}' en {ciudad} ({res_dir})")
        conn.commit()
        flash("Sede creada correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-sedes")


@app.route("/admin/sede/eliminar/<int:idsede>", methods=["POST"])
def admin_eliminar_sede(idsede):
    if session.get("idRol") != ROL_SUPERADMIN:
        flash("Solo el Super Admin puede eliminar sedes.", "danger")
        return redirigir_admin("tab-sedes")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT nombre_sede FROM sede WHERE idSede = %s", (idsede,))
        sede = cursor.fetchone()
        if not sede:
            flash("La sede seleccionada no existe.", "warning")
            return redirigir_admin("tab-sedes")

        cursor.execute("DROP TRIGGER IF EXISTS trg_impedir_eliminar_entrega")
        try:
            cursor.execute("DELETE em FROM entrega_medicamento em JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento JOIN cita c ON t.idcita = c.idcita WHERE c.idSede = %s", (idsede,))
        finally:
            cursor.execute("""
                CREATE TRIGGER trg_impedir_eliminar_entrega BEFORE DELETE ON entrega_medicamento FOR EACH ROW
                BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No se permite eliminar entregas. Debe anularlas.'; END
            """)

        cursor.execute("DELETE tm FROM tratamiento_medicamento tm JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento JOIN cita c ON t.idcita = c.idcita WHERE c.idSede = %s", (idsede,))
        cursor.execute("DELETE t FROM tratamiento t JOIN cita c ON t.idcita = c.idcita WHERE c.idSede = %s", (idsede,))
        cursor.execute("DELETE sv FROM signos_vitales sv JOIN cita c ON sv.idcita = c.idcita WHERE c.idSede = %s", (idsede,))
        cursor.execute("DELETE p FROM pago p JOIN cita c ON p.idcita = c.idcita WHERE c.idSede = %s", (idsede,))
        cursor.execute("DELETE cp FROM cita_procedimiento cp JOIN cita c ON cp.idcita = c.idcita WHERE c.idSede = %s", (idsede,))
        cursor.execute("DELETE FROM cita WHERE idSede = %s", (idsede,))
        cursor.execute("DELETE FROM movimiento_inventario WHERE idSede = %s", (idsede,))
        cursor.execute("DELETE FROM inventario_sede WHERE idSede = %s", (idsede,))
        cursor.execute("UPDATE usuario u JOIN veterinario v ON u.idveterinario = v.idveterinario SET u.idveterinario = NULL WHERE v.idSede = %s", (idsede,))
        cursor.execute("DELETE FROM veterinario WHERE idSede = %s", (idsede,))
        cursor.execute("DELETE FROM sede WHERE idSede = %s", (idsede,))
        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_SEDE", f"Eliminó sede '{sede['nombre_sede']}' (ID #{idsede})")
        conn.commit()
        flash(f"Sede '{sede['nombre_sede']}' eliminada correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error al eliminar sede: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-sedes")


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
    return redirigir_admin("tab-personal")


@app.route("/admin/medicamento/eliminar/<int:idmed>", methods=["POST"])
def admin_eliminar_medicamento(idmed):
    if session.get("idRol") != ROL_SUPERADMIN:
        flash("Solo el Super Admin puede eliminar medicamentos.", "danger")
        return redirigir_admin("tab-inventario")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT nombre_Medicamento FROM medicamento WHERE idMedicamento = %s", (idmed,))
        med = cursor.fetchone()
        cursor.execute("DROP TRIGGER IF EXISTS trg_impedir_eliminar_entrega")
        try:
            cursor.execute("DELETE em FROM entrega_medicamento em JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento WHERE tm.idMedicamento = %s", (idmed,))
        finally:
            cursor.execute("""
                CREATE TRIGGER trg_impedir_eliminar_entrega BEFORE DELETE ON entrega_medicamento FOR EACH ROW
                BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No se permite eliminar entregas. Debe anularlas.'; END
            """)
        cursor.execute("DELETE FROM tratamiento_medicamento WHERE idMedicamento = %s", (idmed,))
        cursor.execute("DELETE FROM inventario_sede WHERE idMedicamento = %s", (idmed,))
        cursor.execute("DELETE FROM movimiento_inventario WHERE idMedicamento = %s", (idmed,))
        cursor.execute("DELETE FROM medicamento WHERE idMedicamento = %s", (idmed,))
        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_MEDICAMENTO", f"Eliminó medicamento '{med['nombre_Medicamento'] if med else idmed}' y todo su historial")
        conn.commit()
        flash("Medicamento y todo su historial eliminados correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error al eliminar medicamento: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_admin("tab-inventario")


if __name__ == "__main__":
    app.run(debug=True)
