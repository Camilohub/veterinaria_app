import re
import urllib.parse
from datetime import datetime, date

ROL_ADMIN = 1
ROL_RECEPCION = 2
ROL_VETERINARIO = 3
ROL_SUPERADMIN = 4

ESTADO_COMPLETADA = 8
ESTADOS_CERRADOS = (8, 9, 10)

TRANSICIONES_CITA = {
    1: {1, 7, 8, 9, 10},
    7: {1, 7, 8, 9, 10},
    8: {8, 1, 7},
    9: {9, 1},
    10: {10, 1},
}

ESPECIES_COMUNES = [
    "Canino (Perro)",
    "Felino (Gato)",
    "Ave",
    "Conejo / Roedor",
    "Reptil",
    "Otro",
]


def validar_especie(especie):
    return bool(especie and especie.strip() in ESPECIES_COMUNES)


def validar_cc_colombia(cc):
    return bool(cc and re.fullmatch(r"\d{6,10}", cc.strip()))


def validar_telefono_colombia(tel):
    return bool(tel and re.fullmatch(r"3\d{9}", tel.strip()))


def validar_correo(correo):
    if not correo:
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", correo.strip()))


def validar_fecha_no_futura(fecha_str):
    if not fecha_str:
        return True
    try:
        f = date.fromisoformat(str(fecha_str)[:10])
        return f <= date.today()
    except ValueError:
        return False


def validar_fecha_hora_futura(fecha_hora_str):
    if not fecha_hora_str:
        return False
    try:
        dt = datetime.fromisoformat(str(fecha_hora_str).replace("T", " "))
        return dt >= datetime.now()
    except ValueError:
        return False


def validar_transicion_estado(estado_actual, estado_nuevo):
    actual = int(estado_actual)
    nuevo = int(estado_nuevo)
    permitidos = TRANSICIONES_CITA.get(actual, {1, 2, 3, 4, 5, 6})
    return nuevo in permitidos


def url_whatsapp(telefono, mensaje):
    limpio = re.sub(r"\D", "", telefono or "")
    if len(limpio) == 10 and limpio.startswith("3"):
        limpio = "57" + limpio
    texto = urllib.parse.quote(mensaje or "")
    return f"https://wa.me/{limpio}?text={texto}" if limpio else ""


def registrar_auditoria(cursor, idusuario, accion, detalle):
    if not idusuario:
        return
    cursor.execute(
        "INSERT INTO auditoria_log (idusuario, accion, detalle) VALUES (%s, %s, %s)",
        (idusuario, accion[:60], detalle[:300]),
    )


def registrar_movimiento_inventario(
    cursor, id_sede, id_medicamento, tipo_movimiento, cantidad,
    stock_anterior, stock_nuevo, referencia="", idusuario=None
):
    cursor.execute(
        """
        INSERT INTO movimiento_inventario
            (idSede, idMedicamento, tipo_movimiento, cantidad, stock_anterior, stock_nuevo, referencia, idusuario)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            id_sede,
            id_medicamento,
            tipo_movimiento,
            int(cantidad),
            int(stock_anterior),
            int(stock_nuevo),
            (referencia or "")[:120],
            idusuario,
        ),
    )


def calcular_balance_cita(cursor, idcita):
    cursor.execute(
        """
        SELECT
            COALESCE((
                SELECT SUM(cp.precio_cobrado)
                FROM cita_procedimiento cp
                WHERE cp.idcita = %s
            ), 0) +
            COALESCE((
                SELECT SUM(em.cantidad_entregada * em.precio_unitario)
                FROM entrega_medicamento em
                JOIN tratamiento_medicamento tm ON em.idtratamiento_medicamento = tm.idtratamiento_medicamento
                JOIN tratamiento t ON tm.idtratamiento = t.idtratamiento
                WHERE t.idcita = %s AND em.estado_entrega = 'Entregada'
            ), 0) AS total_cita,
            COALESCE((
                SELECT SUM(p.monto_total)
                FROM pago p
                WHERE p.idcita = %s AND p.estado_pago = 'Aprobado'
            ), 0) AS total_pagado
        """,
        (idcita, idcita, idcita),
    )
    row = cursor.fetchone()
    if not row:
        return 0.0, 0.0, 0.0, False
    total_cita = float(row["total_cita"] if isinstance(row, dict) else row[0])
    total_pagado = float(row["total_pagado"] if isinstance(row, dict) else row[1])
    saldo_pendiente = round(max(0.0, total_cita - total_pagado), 2)
    esta_saldada = (total_pagado >= total_cita) if total_cita > 0 else (total_pagado > 0)
    return round(total_cita, 2), round(total_pagado, 2), saldo_pendiente, esta_saldada


def cita_saldada(cursor, idcita):
    _, _, _, saldada = calcular_balance_cita(cursor, idcita)
    return saldada
