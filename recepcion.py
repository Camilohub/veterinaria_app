from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
import mysql.connector
from db import get_db_connection
from validadores import (
    ROL_RECEPCION, ESTADOS_CERRADOS, ESPECIES_COMUNES,
    validar_especie, validar_nombre_persona, validar_cc,
    validar_telefono_co, validar_email_estricto, construir_direccion_co,
    validar_fecha_no_futura, validar_fecha_hora_futura,
    validar_transicion_estado, calcular_balance_cita, cita_saldada,
    url_whatsapp, registrar_auditoria
)

recepcion_bp = Blueprint("recepcion", __name__, url_prefix="/recepcion")


def redirigir_recepcion(tab_defecto="tab-agenda", **kwargs):
    tab = (request.form.get("tab") or request.args.get("tab") or tab_defecto).strip()
    if not tab.startswith("tab-"):
        tab = f"tab-{tab}"
    return redirect(url_for("recepcion.panel", tab=tab, **kwargs))


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

    cursor.execute("SELECT * FROM sexo ORDER BY idsexo")
    sexos = cursor.fetchall()

    cursor.execute(
        """
        SELECT m.*, sx.descripcion AS sexo, d.nombre_dueño, d.documento_dueño
        FROM mascota m
        LEFT JOIN sexo sx ON m.idsexo = sx.idsexo
        JOIN dueño d ON m.id_dueño = d.id_dueño
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
        SELECT tm.*, tm.cantidad AS cantidad_prescrita, m.nombre_Medicamento, ma.nombre_mascota,
               s.nombre_sede, c.idcita, c.idestado_cita,
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
        cancelada = r.get("idestado_cita") in (9, 10)
        agotada = int(r["entregado"] or 0) >= int(r["cantidad_prescrita"] or 0)
        r["bloqueada_entrega"] = vencido or sin_stock or cancelada or agotada
        r["motivo_bloqueo"] = (
            "Cita cancelada" if cancelada else (
                "Completada" if agotada else (
                    "Vencido" if vencido else ("Sin stock" if sin_stock else "")
                )
            )
        )

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
        duenos=duenos, mascotas=mascotas, sexos=sexos, veterinarios=veterinarios, sedes=sedes,
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
    ok_nom, res_nom = validar_nombre_persona(request.form.get("nombre_dueño"), "El nombre del propietario")
    if not ok_nom:
        flash(res_nom, "danger")
        return redirigir_recepcion("tab-clientes")
    ok_cc, res_cc = validar_cc(request.form.get("documento_dueño"))
    if not ok_cc:
        flash(res_cc, "danger")
        return redirigir_recepcion("tab-clientes")
    ok_tel, res_tel = validar_telefono_co(request.form.get("telefono_dueño"))
    if not ok_tel:
        flash(res_tel, "danger")
        return redirigir_recepcion("tab-clientes")
    email_raw = (request.form.get("email_dueño") or "").strip()
    email_final = None
    if email_raw:
        ok_em, res_em = validar_email_estricto(email_raw)
        if not ok_em:
            flash(res_em, "danger")
            return redirigir_recepcion("tab-clientes")
        email_final = res_em
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
        flash(res_dir, "danger")
        return redirigir_recepcion("tab-clientes")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO dueño (nombre_dueño, documento_dueño, telefono_dueño, email_dueño, direccion_dueño)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (res_nom, res_cc, res_tel, email_final, res_dir),
        )
        conn.commit()
        flash("Propietario registrado correctamente.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_recepcion("tab-clientes")


@recepcion_bp.route("/dueno/editar/<int:id_dueno>", methods=["POST"])
def editar_dueno(id_dueno):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    cc_raw = (request.form.get("documento_dueño") or "").strip()
    cc_final = None
    if cc_raw:
        ok_cc, res_cc = validar_cc(cc_raw)
        if not ok_cc:
            flash(res_cc, "danger")
            return redirigir_recepcion("tab-clientes")
        cc_final = res_cc
    ok_tel, res_tel = validar_telefono_co(request.form.get("telefono_dueño"))
    if not ok_tel:
        flash(res_tel, "danger")
        return redirigir_recepcion("tab-clientes")
    email_raw = (request.form.get("email_dueño") or "").strip()
    email_final = None
    if email_raw:
        ok_em, res_em = validar_email_estricto(email_raw)
        if not ok_em:
            flash(res_em, "danger")
            return redirigir_recepcion("tab-clientes")
        email_final = res_em

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        campos = ["telefono_dueño = %s", "email_dueño = %s"]
        vals = [res_tel, email_final]
        if cc_final:
            campos.append("documento_dueño = %s")
            vals.append(cc_final)
        vals.append(id_dueno)
        cursor.execute(f"UPDATE dueño SET {', '.join(campos)} WHERE id_dueño = %s", tuple(vals))
        conn.commit()
        flash("Datos del propietario actualizados.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_recepcion("tab-clientes")


@recepcion_bp.route("/mascota", methods=["POST"])
def crear_mascota():
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    especie = request.form.get("especie", "").strip()
    fecha_nac = request.form.get("fecha_Nacimiento") or None
    idsexo = request.form.get("idsexo", type=int)
    peso_raw = request.form.get("peso")
    if not validar_especie(especie) or not validar_fecha_no_futura(fecha_nac):
        flash("Verifica que la especie sea válida y la fecha de nacimiento no sea futura.", "danger")
        return redirigir_recepcion("tab-clientes")
    if fecha_nac and date.fromisoformat(fecha_nac[:10]) < date.today() - timedelta(days=365 * 35):
        flash("La fecha de nacimiento de la mascota no puede superar los 35 años.", "warning")
        return redirigir_recepcion("tab-clientes")
    if not idsexo:
        flash("Selecciona el sexo de la mascota.", "warning")
        return redirigir_recepcion("tab-clientes")
    peso_val = None
    if peso_raw:
        try:
            peso_val = float(peso_raw)
        except ValueError:
            peso_val = -1
        if not (0.1 <= peso_val <= 120):
            flash("El peso de la mascota debe estar entre 0.1 y 120 kg.", "warning")
            return redirigir_recepcion("tab-clientes")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO mascota (nombre_mascota, especie, raza, fecha_Nacimiento, id_dueño, idsexo, peso, alergias)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.form["nombre_mascota"].strip(),
                especie,
                (request.form.get("raza") or "").strip() or None,
                fecha_nac,
                request.form["id_dueño"],
                idsexo,
                peso_val,
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
    return redirigir_recepcion("tab-clientes")


@recepcion_bp.route("/mascota/peso/<int:idmascota>", methods=["POST"])
def editar_peso_mascota(idmascota):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    try:
        peso_val = float(request.form.get("peso") or 0)
    except ValueError:
        peso_val = -1
    if not (0.1 <= peso_val <= 120):
        flash("El peso debe estar entre 0.1 y 120 kg.", "warning")
        return redirigir_recepcion("tab-clientes")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE mascota SET peso = %s WHERE idmascota = %s", (peso_val, idmascota))
        conn.commit()
        flash("Peso actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_recepcion("tab-clientes")


@recepcion_bp.route("/cita", methods=["POST"])
def crear_cita():
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    fecha_hora = request.form["fecha_hora"].replace("T", " ") + ":00"
    if not validar_fecha_hora_futura(fecha_hora):
        flash("No puedes agendar citas en fechas u horas pasadas.", "danger")
        return redirigir_recepcion("tab-agenda")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT estado_paciente FROM mascota WHERE idmascota = %s", (request.form["idmascota"],))
        m = cursor.fetchone()
        if m and m.get("estado_paciente") == "Fallecido":
            flash("No se pueden agendar citas para un paciente fallecido.", "danger")
            return redirigir_recepcion("tab-agenda")
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
    return redirigir_recepcion("tab-agenda", fecha=fecha_hora[:10])


@recepcion_bp.route("/cita/reprogramar/<int:idcita>", methods=["POST"])
def reprogramar_cita(idcita):
    if session.get("idRol") != ROL_RECEPCION:
        return redirect(url_for("panel"))
    fecha_hora = request.form["fecha_hora"].replace("T", " ") + ":00"
    if not validar_fecha_hora_futura(fecha_hora):
        flash("La nueva fecha y hora no puede estar en el pasado.", "danger")
        return redirigir_recepcion("tab-agenda")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if cita_saldada(cursor, idcita):
            flash("Esta cita ya está saldada y no puede modificarse.", "warning")
            return redirigir_recepcion("tab-agenda")
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
    return redirigir_recepcion("tab-agenda", fecha=fecha_hora[:10])


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
            return redirigir_recepcion("tab-agenda")
        cursor.execute("SELECT idestado_cita, fecha_hora FROM cita WHERE idcita = %s", (idcita,))
        actual = cursor.fetchone()
        if not actual or not validar_transicion_estado(actual["idestado_cita"], nuevo_estado):
            flash("Transición de estado no permitida para esta cita.", "danger")
            return redirigir_recepcion("tab-agenda")
        if nuevo_estado in (8, 10) and actual["fecha_hora"] > datetime.now():
            flash("No puedes marcar como Completada o No asistió una cita futura.", "warning")
            return redirigir_recepcion("tab-agenda")
        cursor.execute("UPDATE cita SET idestado_cita = %s WHERE idcita = %s", (nuevo_estado, idcita))
        conn.commit()
        flash("Estado actualizado.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_recepcion("tab-agenda")


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
            SELECT t.idcita, c.idSede, c.idestado_cita, tm.idMedicamento, tm.cantidad AS prescrita,
                   COALESCE(inv.stock, 0) AS stock_sede,
                   DATEDIFF(inv.fecha_vencimiento, CURDATE()) AS dias_vence,
                   COALESCE((
                       SELECT SUM(em.cantidad_entregada) FROM entrega_medicamento em
                       WHERE em.idtratamiento_medicamento = tm.idtratamiento_medicamento AND em.estado_entrega = 'Entregada'
                   ), 0) AS ya_entregado
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
            return redirigir_recepcion("tab-farmacia")
        if row["idestado_cita"] in (9, 10):
            flash("No se pueden despachar medicamentos para una cita cancelada o sin asistencia.", "danger")
            return redirigir_recepcion("tab-farmacia")
        if cita_saldada(cursor, row["idcita"]):
            flash("Cita ya saldada; no se pueden entregar medicamentos.", "warning")
            return redirigir_recepcion("tab-farmacia")
        if row["dias_vence"] is not None and row["dias_vence"] < 0:
            flash("No es posible entregar este medicamento porque el lote está vencido.", "danger")
            return redirigir_recepcion("tab-farmacia")
        pendiente = int(row["prescrita"]) - int(row["ya_entregado"])
        if cant <= 0 or cant > pendiente:
            flash(f"Cantidad inválida: solo quedan {pendiente} unidad(es) pendientes de esta receta.", "warning")
            return redirigir_recepcion("tab-farmacia")
        if (row["stock_sede"] or 0) < cant:
            flash("Stock insuficiente en la sede para realizar la entrega.", "danger")
            return redirigir_recepcion("tab-farmacia")

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
    return redirigir_recepcion("tab-farmacia")


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
            return redirigir_recepcion("tab-farmacia")
        cursor.execute("UPDATE entrega_medicamento SET estado_entrega = 'Anulada' WHERE id_entrega = %s", (id_entrega,))
        registrar_auditoria(cursor, session["idusuario"], "ANULAR_ENTREGA", f"Anuló entrega de farmacia #{id_entrega}")
        conn.commit()
        flash("Entrega anulada y registrada en auditoría.", "warning")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error: {err.msg}", "danger")
    finally:
        cursor.close()
        conn.close()
    return redirigir_recepcion("tab-farmacia")
