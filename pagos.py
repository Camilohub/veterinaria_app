from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response
import csv
import io
import mysql.connector
from db import get_db_connection
from validadores import (
    ROL_RECEPCION, ROL_ADMIN, ROL_SUPERADMIN,
    calcular_balance_cita, registrar_auditoria
)

pagos_bp = Blueprint("pagos", __name__, url_prefix="/pagos")

ROLES_COBRO = (ROL_RECEPCION, ROL_ADMIN, ROL_SUPERADMIN)


@pagos_bp.route("/checkout/<int:idcita>")
def checkout(idcita):
    if session.get("idRol") not in ROLES_COBRO:
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT c.*, m.nombre_mascota, m.especie, d.nombre_dueño, d.documento_dueño,
               d.telefono_dueño, v.nombre_veterinario, s.nombre_sede
        FROM cita c
        JOIN mascota m ON c.idmascota = m.idmascota
        JOIN dueño d ON m.id_dueño = d.id_dueño
        JOIN veterinario v ON c.idveterinario = v.idveterinario
        JOIN sede s ON c.idSede = s.idSede
        WHERE c.idcita = %s
        """,
        (idcita,),
    )
    cita = cursor.fetchone()
    if not cita:
        cursor.close()
        conn.close()
        flash("Cita no encontrada.", "warning")
        return redirect(url_for("panel"))

    total_chk, pagado_chk, _, _ = calcular_balance_cita(cursor, idcita)
    if total_chk > 0 and pagado_chk >= total_chk - 0.01 and cita["idestado_cita"] not in (8, 9, 10):
        cursor.execute("UPDATE cita SET idestado_cita = 8 WHERE idcita = %s", (idcita,))
        conn.commit()
        cita["idestado_cita"] = 8

    cursor.execute(
        """
        SELECT cp.*, pr.nombre_Procedimiento
        FROM cita_procedimiento cp
        JOIN procedimiento pr ON cp.idProcedimiento = pr.idProcedimiento
        WHERE cp.idcita = %s
        """,
        (idcita,),
    )
    procedimientos = cursor.fetchall()

    cursor.execute(
        """
        SELECT em.*, m.nombre_Medicamento, (em.cantidad_entregada * em.precio_unitario) AS subtotal
        FROM entrega_medicamento em
        JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento
        JOIN medicamento m ON tm.idMedicamento = m.idMedicamento
        JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
        WHERE t.idcita = %s AND em.estado_entrega = 'Entregada'
        """,
        (idcita,),
    )
    entregas = cursor.fetchall()

    cursor.execute("SELECT * FROM pago WHERE idcita = %s ORDER BY idpago DESC", (idcita,))
    pagos_cita = cursor.fetchall()

    total_cita, total_pagado, saldo_pendiente, esta_saldada = calcular_balance_cita(cursor, idcita)
    cursor.close()
    conn.close()

    return render_template(
        "checkout.html",
        cita=cita,
        procedimientos=procedimientos,
        entregas=entregas,
        pagos_cita=pagos_cita,
        total_cita=total_cita,
        total_pagado=total_pagado,
        saldo_pendiente=saldo_pendiente,
        esta_saldada=esta_saldada,
    )


@pagos_bp.route("/procesar/<int:idcita>", methods=["POST"])
def procesar(idcita):
    if session.get("idRol") not in ROLES_COBRO:
        return redirect(url_for("panel"))

    metodo = request.form.get("metodo_pago", "Efectivo Caja").strip()
    referencia = (request.form.get("referencia_externa") or "").strip() or None
    monto_raw = request.form.get("monto_total", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        total_cita, total_pagado, saldo_pendiente, esta_saldada = calcular_balance_cita(cursor, idcita)
        if esta_saldada or saldo_pendiente <= 0:
            flash("Esta cita ya se encuentra saldada.", "info")
            return redirect(url_for("pagos.checkout", idcita=idcita))

        monto = float(monto_raw) if monto_raw else saldo_pendiente
        if monto <= 0:
            flash("El monto a cobrar debe ser mayor a cero.", "warning")
            return redirect(url_for("pagos.checkout", idcita=idcita))

        if monto > saldo_pendiente + 0.01:
            flash(f"El monto (${monto:,.2f}) supera el saldo pendiente (${saldo_pendiente:,.2f}).", "warning")
            return redirect(url_for("pagos.checkout", idcita=idcita))

        cursor.execute(
            """
            INSERT INTO pago (idcita, monto_total, metodo_pago, estado_pago, referencia_externa, idusuario)
            VALUES (%s, %s, %s, 'Aprobado', %s, %s)
            """,
            (idcita, monto, metodo, referencia, session["idusuario"]),
        )
        idpago = cursor.lastrowid
        if monto >= saldo_pendiente - 0.01:
            cursor.execute(
                "UPDATE cita SET idestado_cita = 8 WHERE idcita = %s AND idestado_cita NOT IN (9, 10)",
                (idcita,),
            )
        registrar_auditoria(
            cursor,
            session["idusuario"],
            "REGISTRAR_PAGO",
            f"Registró pago #{idpago} por ${monto:,.2f} ({metodo}) en cita #{idcita}",
        )
        conn.commit()
        flash("Pago registrado correctamente.", "success")
        return redirect(url_for("pagos.recibo", idpago=idpago))
    except ValueError:
        conn.rollback()
        flash("Monto inválido.", "danger")
        return redirect(url_for("pagos.checkout", idcita=idcita))
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error al procesar el pago: {err.msg}", "danger")
        return redirect(url_for("pagos.checkout", idcita=idcita))
    finally:
        cursor.close()
        conn.close()


@pagos_bp.route("/reembolsar/<int:idpago>", methods=["POST"])
def reembolsar(idpago):
    if session.get("idRol") not in ROLES_COBRO:
        return redirect(url_for("panel"))
    motivo = (request.form.get("motivo_reembolso") or "").strip() or "Reembolso registrado en caja"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM pago WHERE idpago = %s", (idpago,))
        pago = cursor.fetchone()
        if not pago:
            flash("Pago no encontrado.", "warning")
            return redirect(url_for("panel"))
        if pago["estado_pago"] == "Reembolsado":
            flash("Este pago ya había sido reembolsado.", "info")
            return redirect(url_for("pagos.checkout", idcita=pago["idcita"]))

        cursor.execute(
            "UPDATE pago SET estado_pago = 'Reembolsado' WHERE idpago = %s",
            (idpago,),
        )
        _, _, saldo_pendiente, _ = calcular_balance_cita(cursor, pago["idcita"])
        cursor.execute("SELECT idestado_cita FROM cita WHERE idcita = %s", (pago["idcita"],))
        cita_row = cursor.fetchone()
        if cita_row and cita_row["idestado_cita"] == 8 and saldo_pendiente > 0:
            cursor.execute("UPDATE cita SET idestado_cita = 7 WHERE idcita = %s", (pago["idcita"],))

        registrar_auditoria(
            cursor,
            session["idusuario"],
            "REEMBOLSAR_PAGO",
            f"Reembolsó pago #{idpago} (${float(pago['monto_total']):,.2f}) de cita #{pago['idcita']}. Motivo: {motivo}",
        )
        conn.commit()
        flash("Pago reembolsado. Si la cita tenía saldo pendiente, quedó disponible para ajuste/cobro.", "warning")
        return redirect(url_for("pagos.checkout", idcita=pago["idcita"]))
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Error al reembolsar: {err.msg}", "danger")
        return redirect(url_for("panel"))
    finally:
        cursor.close()
        conn.close()


@pagos_bp.route("/recibo/<int:idpago>")
def recibo(idpago):
    if session.get("idRol") not in ROLES_COBRO:
        return redirect(url_for("panel"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT p.*, c.fecha_hora, c.motivo, m.nombre_mascota, m.especie,
               d.nombre_dueño, d.documento_dueño, d.telefono_dueño,
               v.nombre_veterinario, s.nombre_sede, u.nombre_usuario AS cajero
        FROM pago p
        JOIN cita c ON p.idcita = c.idcita
        JOIN mascota m ON c.idmascota = m.idmascota
        JOIN dueño d ON m.id_dueño = d.id_dueño
        JOIN veterinario v ON c.idveterinario = v.idveterinario
        JOIN sede s ON c.idSede = s.idSede
        LEFT JOIN usuario u ON p.idusuario = u.idusuario
        WHERE p.idpago = %s
        """,
        (idpago,),
    )
    pago = cursor.fetchone()
    if not pago:
        cursor.close()
        conn.close()
        flash("Recibo no encontrado.", "warning")
        return redirect(url_for("panel"))

    cursor.execute(
        """
        SELECT cp.*, pr.nombre_Procedimiento
        FROM cita_procedimiento cp
        JOIN procedimiento pr ON cp.idProcedimiento = pr.idProcedimiento
        WHERE cp.idcita = %s
        """,
        (pago["idcita"],),
    )
    procedimientos = cursor.fetchall()

    cursor.execute(
        """
        SELECT em.*, m.nombre_Medicamento, (em.cantidad_entregada * em.precio_unitario) AS subtotal
        FROM entrega_medicamento em
        JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento
        JOIN medicamento m ON tm.idMedicamento = m.idMedicamento
        JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
        WHERE t.idcita = %s AND em.estado_entrega = 'Entregada'
        """,
        (pago["idcita"],),
    )
    entregas = cursor.fetchall()

    total_cita, total_pagado, saldo_pendiente, esta_saldada = calcular_balance_cita(cursor, pago["idcita"])
    cursor.close()
    conn.close()

    return render_template(
        "recibo.html",
        pago=pago,
        procedimientos=procedimientos,
        entregas=entregas,
        total_cita=total_cita,
        total_pagado=total_pagado,
        saldo_pendiente=saldo_pendiente,
        esta_saldada=esta_saldada,
    )


@pagos_bp.route("/exportar-cierre.csv")
def exportar_cierre_csv():
    if session.get("idRol") not in ROLES_COBRO:
        return redirect(url_for("panel"))
    fecha_detalle = request.args.get("fecha") or ""
    sede_detalle = request.args.get("sede_id", type=int)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    conds = ["p.estado_pago = 'Aprobado'"]
    params = []
    if fecha_detalle:
        conds.append("DATE(p.fecha_pago) = %s")
        params.append(fecha_detalle)
    if sede_detalle:
        conds.append("c.idSede = %s")
        params.append(sede_detalle)
    where_sql = "WHERE " + " AND ".join(conds)

    cursor.execute(
        f"""
        SELECT p.idpago, p.fecha_pago, p.idcita, d.nombre_dueño, d.documento_dueño,
               m.nombre_mascota, s.nombre_sede, p.metodo_pago, p.referencia_externa, p.monto_total
        FROM pago p
        JOIN cita c ON p.idcita = c.idcita
        JOIN mascota m ON c.idmascota = m.idmascota
        JOIN dueño d ON m.id_dueño = d.id_dueño
        JOIN sede s ON c.idSede = s.idSede
        {where_sql}
        ORDER BY p.idpago DESC
        """,
        tuple(params),
    )
    filas = cursor.fetchall()
    cursor.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID Pago", "Fecha Pago", "ID Cita", "Propietario", "Documento",
        "Mascota", "Sede", "Metodo", "Referencia", "Monto"
    ])
    for f in filas:
        writer.writerow([
            f["idpago"], f["fecha_pago"], f["idcita"], f["nombre_dueño"],
            f["documento_dueño"], f["nombre_mascota"], f["nombre_sede"],
            f["metodo_pago"], f["referencia_externa"] or "", f["monto_total"]
        ])

    nombre_archivo = f"cierre_caja_{fecha_detalle or 'general'}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={nombre_archivo}"},
    )
