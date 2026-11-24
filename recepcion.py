from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
import mysql.connector
from db import get_db_connection
from validadores import (
    ROL_RECEPCION, ESTADOS_CERRADOS, ESPECIES_COMUNES,
    validar_especie, validar_cc_colombia, validar_telefono_colombia,
    validar_correo, validar_fecha_no_futura, validar_fecha_hora_futura,
    validar_transicion_estado, calcular_balance_cita, cita_saldada,
    url_whatsapp, registrar_auditoria
)

recepcion_bp = Blueprint("recepcion", __name__, url_prefix="/recepcion")


@recepcion_bp.route("/")
def panel():
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))

    hoy = date.today().isoformat()
    raw_fecha = request.args.get("fecha")
    fecha_filtro = "todas" if raw_fecha == "" else (raw_fecha or hoy)
    sede_id = request.args.get("sede_id", type=int)
    fecha_detalle = request.args.get("fecha_detalle") or hoy
    sede_detalle = request.args.get("sede_detalle", type=int)
    buscar = (request.args.get("buscar") or "").strip().lower()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM dueño ORDER BY id_dueño DESC")
    duenos = cursor.fetchall()

    cursor.execute(
        """
        SELECT m.*, d.nombre_dueño, d.documento_dueño
        FROM mascota m JOIN dueño d ON m.id_dueño = d.id_dueño
        ORDER BY m.idmascota DESC
        """
    )
    mascotas = cursor.fetchall()

    cursor.execute(
        """
        SELECT v.*, s.nombre_sede
        FROM veterinario v JOIN sede s ON v.idSede = s.idSede
        WHERE COALESCE(v.activo, 1) = 1
        ORDER BY v.nombre_veterinario
        """
    )
    veterinarios = cursor.fetchall()

    cursor.execute("SELECT * FROM sede ORDER BY nombre_sede")
    sedes = cursor.fetchall()

    cursor.execute("SELECT * FROM estado_cita ORDER BY idestado_cita")
    estados = cursor.fetchall()

    condiciones, params = [], []
    if fecha_filtro != "todas":
        condiciones.append("DATE(c.fecha_hora) = %s")
        params.append(fecha_filtro)
    if sede_id:
        condiciones.append("c.idSede = %s")
        params.append(sede_id)
    where_citas = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""

    cursor.execute(
        f"""
        SELECT c.idcita, c.fecha_hora, c.motivo, c.idestado_cita, c.idveterinario, c.idSede,
               m.nombre_mascota, d.nombre_dueño, d.documento_dueño, d.telefono_dueño,
               v.nombre_veterinario, s.nombre_sede, e.nombre_estado
        FROM cita c
        JOIN mascota m ON c.idmascota = m.idmascota
        JOIN dueño d ON m.id_dueño = d.id_dueño
        JOIN veterinario v ON c.idveterinario = v.idveterinario
        JOIN sede s ON c.idSede = s.idSede
        JOIN estado_cita e ON c.idestado_cita = e.idestado_cita
        {where_citas}
        ORDER BY c.fecha_hora ASC
        """,
        tuple(params),
    )
    citas = cursor.fetchall()
    for c in citas:
        tc, tp, sp, saldada = calcular_balance_cita(cursor, c["idcita"])
        c["total_cita"] = tc
        c["total_pagado"] = tp
        c["saldo_pendiente"] = sp
        c["esta_pagada"] = saldada
        if saldada and c["idestado_cita"] not in (8, 9, 10):
            cursor.execute("UPDATE cita SET idestado_cita = 8 WHERE idcita = %s", (c["idcita"],))
            conn.commit()
            c["idestado_cita"] = 8
            c["nombre_estado"] = "Completada"
        msg = (
            f"Hola {c['nombre_dueño']}, te recordamos la cita de {c['nombre_mascota']} "
            f"el {c['fecha_hora']} en {c['nombre_sede']} con {c['nombre_veterinario']}."
        )
        c["wa_link"] = url_whatsapp(c["telefono_dueño"], msg)

    if buscar:
        citas = [
            c for c in citas
            if buscar in (c["nombre_mascota"] or "").lower()
            or buscar in (c["nombre_dueño"] or "").lower()
            or buscar in (c["documento_dueño"] or "").lower()
            or buscar in (c["nombre_veterinario"] or "").lower()
        ]
        duenos = [
            d for d in duenos
            if buscar in (d["nombre_dueño"] or "").lower()
            or buscar in (d["documento_dueño"] or "").lower()
            or buscar in (d["telefono_dueño"] or "").lower()
        ]
        mascotas = [
            m for m in mascotas
            if buscar in (m["nombre_mascota"] or "").lower()
            or buscar in (m["nombre_dueño"] or "").lower()
            or buscar in (m["documento_dueño"] or "").lower()
        ]

    cursor.execute(
        """
        SELECT t.*, c.fecha_hora, m.nombre_mascota, d.nombre_dueño, d.documento_dueño, v.nombre_veterinario
        FROM tratamiento t
        JOIN cita c ON t.idcita = c.idcita
        JOIN mascota m ON c.idmascota = m.idmascota
        JOIN dueño d ON m.id_dueño = d.id_dueño
        JOIN veterinario v ON c.idveterinario = v.idveterinario
        ORDER BY t.idtratamiento DESC
        """
    )
    tratamientos = cursor.fetchall()

    where_rec = "WHERE c.idSede = %s" if sede_id else ""
    params_rec = (sede_id,) if sede_id else ()
    cursor.execute(
        f"""
        SELECT tm.*, tm.cantidad AS cantidad_prescrita, m.nombre_Medicamento, ma.nombre_mascota, s.nombre_sede,
               COALESCE(inv.stock, 0) AS stock_sede, inv.fecha_vencimiento,
               DATEDIFF(inv.fecha_vencimiento, CURDATE()) AS dias_vence,
               COALESCE((
                   SELECT SUM(em.cantidad_entregada)
                   FROM entrega_medicamento em
                   WHERE em.idtratamiento_medicamento = tm.idtratamiento_medicamento
                     AND em.estado_entrega = 'Entregada'
               ), 0) AS entregado
        FROM tratamiento_medicamento tm
        JOIN medicamento m ON tm.idMedicamento = m.idMedicamento
        JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
        JOIN cita c ON t.idcita = c.idcita
        JOIN mascota ma ON c.idmascota = ma.idmascota
        JOIN sede s ON c.idSede = s.idSede
        LEFT JOIN inventario_sede inv ON inv.idSede = c.idSede AND inv.idMedicamento = tm.idMedicamento
        {where_rec}
        ORDER BY tm.idtratamiento_medicamento DESC
        """,
        params_rec,
    )
    recetas = cursor.fetchall()
    for r in recetas:
        vencido = r["dias_vence"] is not None and r["dias_vence"] < 0
        sin_stock = (r["stock_sede"] or 0) <= 0
        r["bloqueada_entrega"] = vencido or sin_stock
        r["motivo_bloqueo"] = "Vencido" if vencido else ("Sin stock" if sin_stock else "")

    cursor.execute(
        """
        SELECT em.*, m.nombre_Medicamento, ma.nombre_mascota, c.idcita
        FROM entrega_medicamento em
        JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento
        JOIN medicamento m ON tm.idMedicamento = m.idMedicamento
        JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
        JOIN cita c ON t.idcita = c.idcita
        JOIN mascota ma ON c.idmascota = ma.idmascota
        ORDER BY em.id_entrega DESC LIMIT 80
        """
    )
    entregas = cursor.fetchall()

    cond_pagos = ["DATE(p.fecha_pago) = %s"]
    params_pagos = [fecha_detalle]
    if sede_detalle:
        cond_pagos.append("c.idSede = %s")
        params_pagos.append(sede_detalle)
    where_pagos = "WHERE " + " AND ".join(cond_pagos)

    cursor.execute(
        f"""
        SELECT p.*, ma.nombre_mascota, d.nombre_dueño, s.nombre_sede
        FROM pago p
        JOIN cita c ON p.idcita = c.idcita
        JOIN mascota ma ON c.idmascota = ma.idmascota
        JOIN dueño d ON ma.id_dueño = d.id_dueño
        JOIN sede s ON c.idSede = s.idSede
        {where_pagos}
        ORDER BY p.idpago DESC
        """,
        tuple(params_pagos),
    )
    pagos = cursor.fetchall()

    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN p.estado_pago='Aprobado' AND p.metodo_pago='Efectivo Caja' THEN p.monto_total ELSE 0 END), 0) AS efectivo,
            COALESCE(SUM(CASE WHEN p.estado_pago='Aprobado' AND p.metodo_pago<>'Efectivo Caja' THEN p.monto_total ELSE 0 END), 0) AS electronico,
            COALESCE(SUM(CASE WHEN p.estado_pago='Reembolsado' THEN p.monto_total ELSE 0 END), 0) AS reembolsado,
            COUNT(CASE WHEN p.estado_pago='Aprobado' THEN 1 END) AS num_aprobados
        FROM pago p JOIN cita c ON p.idcita = c.idcita {where_pagos}
        """,
        tuple(params_pagos),
    )
    resumen_dia = cursor.fetchone()

    sede_vendido_sql = "AND c.idSede = %s" if sede_detalle else ""
    params_vendido = [fecha_detalle] + ([sede_detalle] if sede_detalle else [])
    cursor.execute(
        f"""
        SELECT 'Procedimiento' AS categoria, pr.nombre_Procedimiento AS concepto, 1 AS cantidad,
               cp.precio_cobrado AS subtotal, c.idcita, ma.nombre_mascota, s.nombre_sede
        FROM cita_procedimiento cp
        JOIN procedimiento pr ON cp.idProcedimiento = pr.idProcedimiento
        JOIN cita c ON cp.idcita = c.idcita
        JOIN mascota ma ON c.idmascota = ma.idmascota
        JOIN sede s ON c.idSede = s.idSede
        WHERE c.idcita IN (SELECT DISTINCT idcita FROM pago WHERE DATE(fecha_pago) = %s AND estado_pago = 'Aprobado')
        {sede_vendido_sql}
        UNION ALL
        SELECT 'Medicamento' AS categoria, m.nombre_Medicamento AS concepto, em.cantidad_entregada AS cantidad,
               (em.cantidad_entregada * em.precio_unitario) AS subtotal, c.idcita, ma.nombre_mascota, s.nombre_sede
        FROM entrega_medicamento em
        JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento
        JOIN medicamento m ON tm.idMedicamento = m.idMedicamento
        JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
        JOIN cita c ON t.idcita = c.idcita
        JOIN mascota ma ON c.idmascota = ma.idmascota
        JOIN sede s ON c.idSede = s.idSede
        WHERE DATE(em.fecha_entrega) = %s AND em.estado_entrega = 'Entregada'
        {sede_vendido_sql}
        """,
        tuple(params_vendido + params_vendido),
    )
    vendidos_dia = cursor.fetchall()

    cursor.close()
    conn.close()

    pendientes_cobro = sum(
        1 for c in citas if not c["esta_pagada"] and c["idestado_cita"] not in ESTADOS_CERRADOS
    )
    ingresos_dia = float(resumen_dia["efectivo"]) + float(resumen_dia["electronico"])

    return render_template(
        "recepcion.html",
        duenos=duenos, mascotas=mascotas, veterinarios=veterinarios, sedes=sedes,
        estados=estados, citas=citas, tratamientos=tratamientos, recetas=recetas,
        entregas=entregas, pagos=pagos, especies=ESPECIES_COMUNES,
        pendientes_cobro=pendientes_cobro, ingresos_dia=ingresos_dia, hoy=hoy,
        fecha_filtro=fecha_filtro, sede_id=sede_id, fecha_detalle=fecha_detalle,
        sede_detalle=sede_detalle, resumen_dia=resumen_dia, vendidos_dia=vendidos_dia,
        buscar=buscar,
    )


@recepcion_bp.route("/dueno", methods=["POST"])
def crear_dueno():
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    cc = request.form.get("documento_dueño", "").strip()
    tel = request.form.get("telefono_dueño", "").strip()
    email = (request.form.get("email_dueño") or "").strip() or None
    if not validar_cc_colombia(cc) or not validar_telefono_colombia(tel) or not validar_correo(email):
        flash("Verifica cédula colombiana (6-10 dígitos), teléfono celular (10 dígitos iniciando en 3) y correo válido.", "danger")
        return redirect(url_for("recepcion.panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO dueño (nombre_dueño, documento_dueño, telefono_dueño, email_dueño) VALUES (%s, %s, %s, %s)",
            (request.form["nombre_dueño"].strip(), cc, tel, email),
        )
        conn.commit()
        flash("Propietario registrado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel"))


@recepcion_bp.route("/dueno/editar/<int:id_dueno>", methods=["POST"])
def editar_dueno(id_dueno):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    cc = request.form.get("documento_dueño", "").strip()
    tel = request.form.get("telefono_dueño", "").strip()
    email = (request.form.get("email_dueño") or "").strip() or None
    if cc and not validar_cc_colombia(cc):
        flash("Cédula colombiana inválida (6-10 dígitos).", "danger")
        return redirect(url_for("recepcion.panel"))
    if not validar_telefono_colombia(tel) or not validar_correo(email):
        flash("Teléfono colombiano (10 dígitos iniciando en 3) o correo inválido.", "danger")
        return redirect(url_for("recepcion.panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if cc:
            cursor.execute(
                "UPDATE dueño SET documento_dueño = %s, telefono_dueño = %s, email_dueño = %s WHERE id_dueño = %s",
                (cc, tel, email, id_dueno),
            )
        else:
            cursor.execute(
                "UPDATE dueño SET telefono_dueño = %s, email_dueño = %s WHERE id_dueño = %s",
                (tel, email, id_dueno),
            )
        conn.commit()
        flash("Datos del propietario actualizados.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel"))


@recepcion_bp.route("/mascota", methods=["POST"])
def crear_mascota():
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    especie = request.form.get("especie", "").strip()
    fecha_nac = request.form.get("fecha_Nacimiento") or None
    if not validar_especie(especie) or not validar_fecha_no_futura(fecha_nac):
        flash("Verifica que la especie sea válida y la fecha de nacimiento no sea futura.", "danger")
        return redirect(url_for("recepcion.panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO mascota (nombre_mascota, especie, raza, fecha_Nacimiento, id_dueño, sexo, peso, alergias)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.form["nombre_mascota"].strip(),
                especie,
                (request.form.get("raza") or "").strip() or None,
                fecha_nac,
                request.form["id_dueño"],
                request.form["sexo"],
                request.form.get("peso") or None,
                (request.form.get("alergias") or "").strip() or None,
            ),
        )
        conn.commit()
        flash("Paciente registrado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel"))


@recepcion_bp.route("/mascota/peso/<int:idmascota>", methods=["POST"])
def editar_peso_mascota(idmascota):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE mascota SET peso = %s WHERE idmascota = %s", (request.form["peso"], idmascota))
        conn.commit()
        flash("Peso actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel"))


@recepcion_bp.route("/cita", methods=["POST"])
def crear_cita():
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    fecha_hora = request.form["fecha_hora"].replace("T", " ") + ":00"
    if not validar_fecha_hora_futura(fecha_hora):
        flash("No puedes agendar citas en fechas u horas pasadas.", "danger")
        return redirect(url_for("recepcion.panel"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO cita (fecha_hora, motivo, idmascota, idveterinario, idSede, idusuario)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                fecha_hora,
                (request.form.get("motivo") or "").strip(),
                request.form["idmascota"],
                request.form["idveterinario"],
                request.form["idSede"],
                session["idusuario"],
            ),
        )
        conn.commit()
        flash("Cita agendada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"No se pudo agendar: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel", fecha=fecha_hora[:10]))


@recepcion_bp.route("/cita/reprogramar/<int:idcita>", methods=["POST"])
def reprogramar_cita(idcita):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    fecha_hora = request.form["fecha_hora"].replace("T", " ") + ":00"
    if not validar_fecha_hora_futura(fecha_hora):
        flash("La nueva fecha y hora no puede estar en el pasado.", "danger")
        return redirect(url_for("recepcion.panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if cita_saldada(cursor, idcita):
            flash("Esta cita ya está saldada y no puede modificarse.", "warning")
            return redirect(url_for("recepcion.panel"))
        cursor.execute(
            "UPDATE cita SET fecha_hora = %s, idestado_cita = 1 WHERE idcita = %s",
            (fecha_hora, idcita),
        )
        conn.commit()
        flash("Cita reprogramada.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"No se pudo reprogramar: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel", fecha=fecha_hora[:10]))


@recepcion_bp.route("/cita/estado/<int:idcita>", methods=["POST"])
def cambiar_estado_cita(idcita):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    nuevo_estado = int(request.form["idestado_cita"])
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if cita_saldada(cursor, idcita):
            flash("Cita ya saldada; no puede modificarse.", "warning")
            return redirect(url_for("recepcion.panel"))
        cursor.execute("SELECT idestado_cita FROM cita WHERE idcita = %s", (idcita,))
        actual = cursor.fetchone()
        if not actual or not validar_transicion_estado(actual["idestado_cita"], nuevo_estado):
            flash("Transición de estado no permitida para esta cita.", "danger")
            return redirect(request.referrer or url_for("recepcion.panel"))
        cursor.execute(
            "UPDATE cita SET idestado_cita = %s WHERE idcita = %s",
            (nuevo_estado, idcita),
        )
        conn.commit()
        flash("Estado actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(request.referrer or url_for("recepcion.panel"))


@recepcion_bp.route("/entrega", methods=["POST"])
def registrar_entrega():
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    id_tm = int(request.form["idtratamiento_medicamento"])
    cant = int(request.form["cantidad_entregada"])

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT t.idcita, c.idSede, tm.idMedicamento,
                   COALESCE(inv.stock, 0) AS stock_sede,
                   inv.fecha_vencimiento,
                   DATEDIFF(inv.fecha_vencimiento, CURDATE()) AS dias_vence
            FROM tratamiento_medicamento tm
            JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
            JOIN cita c ON t.idcita = c.idcita
            LEFT JOIN inventario_sede inv ON inv.idSede = c.idSede AND inv.idMedicamento = tm.idMedicamento
            WHERE tm.idtratamiento_medicamento = %s
            """,
            (id_tm,),
        )
        row = cursor.fetchone()
        if not row:
            flash("Receta no encontrada.", "warning")
            return redirect(url_for("recepcion.panel"))
        if cita_saldada(cursor, row["idcita"]):
            flash("Cita ya saldada; no se pueden entregar medicamentos.", "warning")
            return redirect(url_for("recepcion.panel"))
        if row["dias_vence"] is not None and row["dias_vence"] < 0:
            flash("No es posible entregar este medicamento porque el lote está vencido.", "danger")
            return redirect(url_for("recepcion.panel"))
        if cant <= 0 or (row["stock_sede"] or 0) < cant:
            flash("Stock insuficiente en la sede para realizar la entrega.", "danger")
            return redirect(url_for("recepcion.panel"))

        cursor.execute(
            """
            INSERT INTO entrega_medicamento (idtratamiento_medicamento, cantidad_entregada, idusuario)
            VALUES (%s, %s, %s)
            """,
            (id_tm, cant, session["idusuario"]),
        )
        conn.commit()
        flash("Medicamento entregado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel"))


@recepcion_bp.route("/entrega/anular/<int:id_entrega>", methods=["POST"])
def anular_entrega(id_entrega):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT t.idcita FROM entrega_medicamento em
            JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento
            JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
            WHERE em.id_entrega = %s
            """,
            (id_entrega,),
        )
        row = cursor.fetchone()
        if row and cita_saldada(cursor, row["idcita"]):
            flash("Cita ya saldada; no se puede anular la entrega.", "warning")
            return redirect(url_for("recepcion.panel"))
        cursor.execute(
            "UPDATE entrega_medicamento SET estado_entrega = 'Anulada' WHERE id_entrega = %s",
            (id_entrega,),
        )
        registrar_auditoria(
            cursor, session["idusuario"], "ANULAR_ENTREGA", f"Anuló entrega de farmacia #{id_entrega}"
        )
        conn.commit()
        flash("Entrega anulada y registrada en auditoría.", "warning")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("recepcion.panel"))
