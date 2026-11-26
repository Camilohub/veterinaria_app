from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
import mysql.connector
from db import get_db_connection
from validadores import (
    VACUNAS_MASCOTAS,
    UNIDADES_DOSIS, validar_receta_medica,
    ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN,
    validar_fecha_no_futura, validar_transicion_estado,
    cita_saldada, registrar_auditoria
)

veterinario_bp = Blueprint("veterinario", __name__, url_prefix="/veterinario")


def _resolver_veterinario(cursor, id_usuario, id_vet_sesion, id_vet_query=None):
    rol = session.get("idRol")
    if rol == ROL_VETERINARIO:
        cursor.execute(
            """
            SELECT v.idveterinario, v.nombre_veterinario, v.especialidad, v.idSede, s.nombre_sede,
                   COALESCE(v.activo, 1) AS activo
            FROM veterinario v
            JOIN sede s ON v.idSede = s.idSede
            JOIN usuario u ON u.idveterinario = v.idveterinario
            WHERE u.idusuario = %s
            """,
            (id_usuario,),
        )
        vet = cursor.fetchone()
        if vet and int(vet["activo"]) == 1:
            return vet["idveterinario"], vet
        return None, None

    if rol in (ROL_ADMIN, ROL_SUPERADMIN):
        if id_vet_query:
            cursor.execute(
                """
                SELECT v.idveterinario, v.nombre_veterinario, v.especialidad, v.idSede, s.nombre_sede,
                       COALESCE(v.activo, 1) AS activo
                FROM veterinario v JOIN sede s ON v.idSede = s.idSede
                WHERE v.idveterinario = %s
                """,
                (id_vet_query,),
            )
            vet = cursor.fetchone()
            if vet:
                return vet["idveterinario"], vet
        cursor.execute(
            """
            SELECT v.idveterinario, v.nombre_veterinario, v.especialidad, v.idSede, s.nombre_sede,
                   COALESCE(v.activo, 1) AS activo
            FROM veterinario v JOIN sede s ON v.idSede = s.idSede
            ORDER BY v.idveterinario ASC LIMIT 1
            """
        )
        vet = cursor.fetchone()
        return (vet["idveterinario"], vet) if vet else (None, None)

    return id_vet_sesion, None


@veterinario_bp.route("/")
def panel():
    rol = session.get("idRol")
    if rol not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))

    id_vet_query = request.args.get("idveterinario", type=int)
    buscar_mascota = (request.args.get("buscar") or "").strip().lower()
    mascota_ficha_id = request.args.get("mascota_id", type=int)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    id_vet, vet_info = _resolver_veterinario(
        cursor, session.get("idusuario"), session.get("idveterinario"), id_vet_query
    )

    if rol == ROL_VETERINARIO and not id_vet:
        cursor.close()
        conn.close()
        flash(
            "Tu cuenta de usuario aún no está vinculada a un perfil de veterinario activo. Solicita a Administración que revise tu asignación.",
            "warning",
        )
        return render_template(
        "veterinario.html",
        vacunas_catalogo=VACUNAS_MASCOTAS,
            vet_info=None,
            todos_veterinarios=[],
            estados=[],
            citas=[],
            tratamientos=[],
            recetas=[],
            procedimientos=[],
            medicamentos=[],
            vacunas=[],
            mascotas_ficha=[],
            ficha_mascota=None,
            ficha_historial=[],
            ficha_vacunas=[],
            ficha_procedimientos=[],
            ficha_recetas=[],
            buscar_mascota="", hoy=date.today().isoformat(), unidades_dosis=UNIDADES_DOSIS,
        )

    cursor.execute("SELECT * FROM veterinario ORDER BY nombre_veterinario")
    todos_veterinarios = cursor.fetchall()

    cursor.execute("SELECT * FROM estado_cita ORDER BY idestado_cita")
    estados = cursor.fetchall()

    cursor.execute(
        """
        SELECT c.*, m.idmascota, m.nombre_mascota, m.especie, m.raza, sx.descripcion AS sexo, m.peso, m.alergias,
               d.nombre_dueño, d.documento_dueño, d.telefono_dueño,
               e.nombre_estado, s.nombre_sede
        FROM cita c
        JOIN mascota m ON c.idmascota = m.idmascota
        LEFT JOIN sexo sx ON m.idsexo = sx.idsexo
        JOIN dueño d ON m.id_dueño = d.id_dueño
        JOIN estado_cita e ON c.idestado_cita = e.idestado_cita
        JOIN sede s ON c.idSede = s.idSede
        WHERE c.idveterinario = %s
        ORDER BY c.fecha_hora DESC
        """,
        (id_vet,),
    )
    citas = cursor.fetchall()
    for c in citas:
        c["esta_saldada"] = cita_saldada(cursor, c["idcita"])

    if buscar_mascota:
        citas = [
            c for c in citas
            if buscar_mascota in (c["nombre_mascota"] or "").lower()
            or buscar_mascota in (c["nombre_dueño"] or "").lower()
            or buscar_mascota in (c["documento_dueño"] or "").lower()
            or buscar_mascota in (c["especie"] or "").lower()
        ]

    cursor.execute(
        """
        SELECT t.*, c.fecha_hora, m.nombre_mascota, d.nombre_dueño
        FROM tratamiento t
        JOIN cita c ON t.idcita = c.idcita
        JOIN mascota m ON c.idmascota = m.idmascota
        JOIN dueño d ON m.id_dueño = d.id_dueño
        WHERE c.idveterinario = %s
        ORDER BY t.idtratamiento DESC
        """,
        (id_vet,),
    )
    tratamientos = cursor.fetchall()
    for t in tratamientos:
        t["cita_saldada"] = cita_saldada(cursor, t["idcita"])

    cursor.execute(
        """
        SELECT tm.*, tm.cantidad AS cantidad_prescrita, m.nombre_Medicamento, ma.nombre_mascota, t.idcita
        FROM tratamiento_medicamento tm
        JOIN medicamento m ON tm.idMedicamento = m.idMedicamento
        JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
        JOIN cita c ON t.idcita = c.idcita
        JOIN mascota ma ON c.idmascota = ma.idmascota
        WHERE c.idveterinario = %s
        ORDER BY tm.idtratamiento_medicamento DESC
        """,
        (id_vet,),
    )
    recetas = cursor.fetchall()
    for r in recetas:
        r["cita_saldada"] = cita_saldada(cursor, r["idcita"])

    cursor.execute(
        "SELECT * FROM procedimiento WHERE COALESCE(activo, 1) = 1 ORDER BY nombre_Procedimiento"
    )
    procedimientos = cursor.fetchall()

    cursor.execute(
        "SELECT * FROM medicamento WHERE COALESCE(activo, 1) = 1 ORDER BY nombre_Medicamento"
    )
    medicamentos = cursor.fetchall()

    cursor.execute(
        """
        SELECT v.*, m.nombre_mascota, d.nombre_dueño
        FROM vacuna v
        JOIN mascota m ON v.idmascota = m.idmascota
        JOIN dueño d ON m.id_dueño = d.id_dueño
        ORDER BY v.fecha_aplicacion DESC, v.idvacuna DESC
        """
    )
    vacunas = cursor.fetchall()

    cursor.execute(
        """
        SELECT m.idmascota, m.nombre_mascota, m.especie, m.raza, m.peso, m.alergias, d.nombre_dueño
        FROM mascota m JOIN dueño d ON m.id_dueño = d.id_dueño
        ORDER BY m.nombre_mascota ASC
        """
    )
    mascotas_ficha = cursor.fetchall()

    ficha_mascota = None
    ficha_historial = []
    ficha_vacunas = []
    ficha_procedimientos = []
    ficha_recetas = []

    target_mascota_id = mascota_ficha_id or (citas[0]["idmascota"] if citas else (mascotas_ficha[0]["idmascota"] if mascotas_ficha else None))
    if target_mascota_id:
        cursor.execute(
            """
            SELECT m.*, sx.descripcion AS sexo, d.nombre_dueño, d.telefono_dueño, d.documento_dueño
            FROM mascota m LEFT JOIN sexo sx ON m.idsexo = sx.idsexo JOIN dueño d ON m.id_dueño = d.id_dueño
            WHERE m.idmascota = %s
            """,
            (target_mascota_id,),
        )
        ficha_mascota = cursor.fetchone()

        cursor.execute(
            """
            SELECT c.idcita, c.fecha_hora, c.motivo, c.signos_vitales, e.nombre_estado, s.nombre_sede,
                   t.diagnostico, t.indicaciones
            FROM cita c
            JOIN estado_cita e ON c.idestado_cita = e.idestado_cita
            JOIN sede s ON c.idSede = s.idSede
            LEFT JOIN tratamiento t ON t.idcita = c.idcita
            WHERE c.idmascota = %s
            ORDER BY c.fecha_hora DESC
            """,
            (target_mascota_id,),
        )
        ficha_historial = cursor.fetchall()

        cursor.execute(
            "SELECT * FROM vacuna WHERE idmascota = %s ORDER BY fecha_aplicacion DESC",
            (target_mascota_id,),
        )
        ficha_vacunas = cursor.fetchall()

        cursor.execute(
            """
            SELECT cp.*, pr.nombre_Procedimiento, c.fecha_hora
            FROM cita_procedimiento cp
            JOIN procedimiento pr ON cp.idProcedimiento = pr.idProcedimiento
            JOIN cita c ON cp.idcita = c.idcita
            WHERE c.idmascota = %s
            ORDER BY c.fecha_hora DESC
            """,
            (target_mascota_id,),
        )
        ficha_procedimientos = cursor.fetchall()

        cursor.execute(
            """
            SELECT tm.*, tm.cantidad AS cantidad_prescrita, m.nombre_Medicamento, c.fecha_hora
            FROM tratamiento_medicamento tm
            JOIN medicamento m ON tm.idMedicamento = m.idMedicamento
            JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
            JOIN cita c ON t.idcita = c.idcita
            WHERE c.idmascota = %s
            ORDER BY c.fecha_hora DESC
            """,
            (target_mascota_id,),
        )
        ficha_recetas = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "veterinario.html",
        vacunas_catalogo=VACUNAS_MASCOTAS,
        vet_info=vet_info,
        todos_veterinarios=todos_veterinarios,
        estados=estados,
        citas=citas,
        tratamientos=tratamientos,
        recetas=recetas,
        procedimientos=procedimientos,
        medicamentos=medicamentos,
        vacunas=vacunas,
        mascotas_ficha=mascotas_ficha,
        ficha_mascota=ficha_mascota,
        ficha_historial=ficha_historial,
        ficha_vacunas=ficha_vacunas,
        ficha_procedimientos=ficha_procedimientos,
        ficha_recetas=ficha_recetas,
        buscar_mascota=buscar_mascota, hoy=date.today().isoformat(), unidades_dosis=UNIDADES_DOSIS,
    )


@veterinario_bp.route("/cita/estado/<int:idcita>", methods=["POST"])
def cambiar_estado(idcita):
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    nuevo_estado = int(request.form["idestado_cita"])
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if cita_saldada(cursor, idcita):
            flash("Esta cita ya está saldada; no puede alterarse su estado.", "warning")
            return redirect(url_for("veterinario.panel"))
        cursor.execute("SELECT idestado_cita FROM cita WHERE idcita = %s", (idcita,))
        actual = cursor.fetchone()
        if not actual or not validar_transicion_estado(actual["idestado_cita"], nuevo_estado):
            flash("Transición de estado de cita no permitida.", "danger")
            return redirect(url_for("veterinario.panel"))
        cursor.execute("UPDATE cita SET idestado_cita = %s WHERE idcita = %s", (nuevo_estado, idcita))
        conn.commit()
        flash("Estado de cita actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel"))

@veterinario_bp.route("/tratamiento", methods=["POST"])
def crear_tratamiento():
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    idcita = int(request.form["idcita"])
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if cita_saldada(cursor, idcita):
            flash("Esta cita ya está saldada; no se pueden modificar tratamientos.", "warning")
            return redirect(url_for("veterinario.panel"))
        cursor.execute(
            "INSERT INTO tratamiento (idcita, diagnostico, indicaciones) VALUES (%s, %s, %s)",
            (idcita, request.form["diagnostico"].strip(), request.form["indicaciones"].strip()),
        )
        registrar_auditoria(cursor, session["idusuario"], "CREAR_TRATAMIENTO", f"Registró tratamiento en cita #{idcita}")
        conn.commit()
        flash("Tratamiento guardado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel"))


@veterinario_bp.route("/tratamiento/eliminar/<int:idtratamiento>", methods=["POST"])
def eliminar_tratamiento(idtratamiento):
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT idcita FROM tratamiento WHERE idtratamiento = %s", (idtratamiento,))
        row = cursor.fetchone()
        if not row:
            flash("Tratamiento no encontrado.", "warning")
            return redirect(url_for("veterinario.panel"))
        if cita_saldada(cursor, row["idcita"]):
            flash("Cita ya saldada; no se puede eliminar el tratamiento.", "warning")
            return redirect(url_for("veterinario.panel"))
        cursor.execute("DELETE FROM tratamiento WHERE idtratamiento = %s", (idtratamiento,))
        registrar_auditoria(cursor, session["idusuario"], "ELIMINAR_TRATAMIENTO", f"Eliminó tratamiento #{idtratamiento}")
        conn.commit()
        flash("Tratamiento eliminado.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel"))


@veterinario_bp.route("/receta", methods=["POST"])
def crear_receta():
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    idtrat = int(request.form["idtratamiento"])
    idmed = int(request.form["idMedicamento"])
    cant = int(request.form["cantidad_prescrita"])
    ok_rec, res_rec = validar_receta_medica(
        request.form.get("cantidad_prescrita"),
        request.form.get("dosis_num"),
        request.form.get("dosis_unidad"),
        request.form.get("frecuencia_horas"),
        request.form.get("duracion_dias"),
    )
    if not ok_rec:
        flash(res_rec, "warning")
        return redirect(url_for("veterinario.panel", tab="tab-consulta"))
    cant = res_rec["cantidad"]
    dosis = res_rec["dosis"]
    frecuencia = res_rec["frecuencia"]
    duracion_dias = res_rec["duracion_dias"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT t.idcita, m.nombre_Medicamento, COALESCE(ma.alergias, '') AS alergias
            FROM tratamiento t
            JOIN cita c ON t.idcita = c.idcita
            JOIN mascota ma ON c.idmascota = ma.idmascota
            JOIN medicamento m ON m.idMedicamento = %s
            WHERE t.idtratamiento = %s
            """,
            (idmed, idtrat),
        )
        info = cursor.fetchone()
        if not info:
            flash("Tratamiento o medicamento no válido.", "warning")
            return redirect(url_for("veterinario.panel"))
        if cita_saldada(cursor, info["idcita"]):
            flash("Cita ya saldada; no se pueden agregar recetas.", "warning")
            return redirect(url_for("veterinario.panel"))

        cursor.execute(
            """
            INSERT INTO tratamiento_medicamento (idtratamiento, idMedicamento, cantidad, dosis, frecuencia, duracion_dias)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (idtrat, idmed, cant, dosis, frecuencia, duracion_dias),
        )
        conn.commit()

        alergias = info["alergias"].lower()
        med_nombre = info["nombre_Medicamento"].lower()
        if alergias and alergias != "ninguna" and any(pal for pal in med_nombre.split() if len(pal) > 3 and pal in alergias):
            flash(f"ALERTA DE ALERGIA: El paciente registra '{info['alergias']}' y se recetó '{info['nombre_Medicamento']}'.", "danger")
        else:
            flash("Receta agregada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel"))


@veterinario_bp.route("/receta/eliminar/<int:idtm>", methods=["POST"])
def eliminar_receta(idtm):
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT t.idcita FROM tratamiento_medicamento tm
            JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
            WHERE tm.idtratamiento_medicamento = %s
            """,
            (idtm,),
        )
        row = cursor.fetchone()
        if not row:
            flash("Receta no encontrada.", "warning")
            return redirect(url_for("veterinario.panel"))
        if cita_saldada(cursor, row["idcita"]):
            flash("Cita ya saldada; no se puede eliminar la receta.", "warning")
            return redirect(url_for("veterinario.panel"))
        cursor.execute("DELETE FROM tratamiento_medicamento WHERE idtratamiento_medicamento = %s", (idtm,))
        conn.commit()
        flash("Receta eliminada.", "info")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel"))


@veterinario_bp.route("/procedimiento", methods=["POST"])
def agregar_procedimiento():
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    idcita = int(request.form["idcita"])
    idproc = int(request.form["idProcedimiento"])
    obs = (request.form.get("observaciones") or "").strip() or None

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if cita_saldada(cursor, idcita):
            flash("Cita ya saldada; no se pueden agregar procedimientos.", "warning")
            return redirect(url_for("veterinario.panel"))
        cursor.execute("SELECT costo FROM procedimiento WHERE idProcedimiento = %s", (idproc,))
        proc = cursor.fetchone()
        if not proc:
            flash("Procedimiento no encontrado.", "warning")
            return redirect(url_for("veterinario.panel"))
        cursor.execute(
            """
            INSERT INTO cita_procedimiento (idcita, idProcedimiento, precio_cobrado)
            VALUES (%s, %s, %s)
            """,
            (idcita, idproc, proc["costo"]),
        )
        conn.commit()
        flash("Procedimiento agregado a la cita.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel"))


@veterinario_bp.route("/vacuna", methods=["POST"])
def registrar_vacuna():
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    fecha_ap = request.form.get("fecha_aplicacion") or None
    if not validar_fecha_no_futura(fecha_ap):
        flash("La fecha de aplicación de vacuna no puede ser futura.", "danger")
        return redirect(url_for("veterinario.panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO vacuna (idmascota, nombre_vacuna, lote, fecha_aplicacion, proxima_dosis)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                request.form["idmascota"],
                request.form["nombre_vacuna"].strip(),
                (request.form.get("lote") or "").strip() or None,
                fecha_ap,
                request.form.get("proxima_dosis") or None,
            ),
        )
        conn.commit()
        flash("Vacuna registrada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel"))


@veterinario_bp.route("/mascota/ficha/<int:idmascota>", methods=["POST"])
def actualizar_ficha_mascota(idmascota):
    if session.get("idRol") not in (ROL_VETERINARIO, ROL_ADMIN, ROL_SUPERADMIN):
        return redirect(url_for("panel"))
    peso = request.form.get("peso") or None
    alergias = (request.form.get("alergias") or "").strip() or None
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE mascota SET peso = %s, alergias = %s WHERE idmascota = %s",
            (peso, alergias, idmascota),
        )
        conn.commit()
        flash("Ficha de mascota actualizada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("veterinario.panel", mascota_id=idmascota))
