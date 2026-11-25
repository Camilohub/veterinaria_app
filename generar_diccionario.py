from datetime import datetime
from db import get_db_connection
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def set_cell_bg(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)

def set_cell_margins(cell, top=80, bottom=80, left=110, right=110):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement("w:tcMar")
    for m, val in [("top", top), ("bottom", bottom), ("left", left), ("right", right)]:
        node = OxmlElement(f"w:{m}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        tcMar.append(node)
    tcPr.append(tcMar)

def prevent_row_split(row):
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:cantSplit"))

def repeat_header_row(row):
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:tblHeader"))

DESCRIPCIONES_TABLAS = {
    "auditoria_log": "Bitácora inmutable de trazabilidad que registra acciones críticas ejecutadas por los usuarios del sistema (altas, bajas, cobros, anulaciones).",
    "cita": "Núcleo operativo de agenda clínica; vincula al paciente, veterinario tratante, sede de atención, estado del flujo y usuario que agendó.",
    "cita_procedimiento": "Tabla asociativa que registra los procedimientos clínicos realizados durante una cita y congela el precio histórico cobrado.",
    "dueño": "Maestro de propietarios o responsables legales de las mascotas atendidas en la clínica veterinaria.",
    "entrega_medicamento": "Control de despacho de farmacia asociado a cada ítem recetado; valida stock por sede, vigencia y bloquea borrados físicos.",
    "estado_cita": "Catálogo maestro de estados del ciclo de vida de una cita (Programada, En atención, Completada, Cancelada, No asistió).",
    "inventario_sede": "Control de existencias y fechas de vencimiento de medicamentos por cada sede física de la clínica.",
    "mascota": "Registro clínico de pacientes animales asociados a un propietario, incluyendo especie, raza, peso y alergias.",
    "medicamento": "Catálogo maestro de fármacos disponibles en la clínica con su precio unitario de referencia y estado activo/inactivo.",
    "movimiento_inventario": "Kardex de movimientos de almacén (entradas, salidas por entrega, anulaciones y ajustes) con saldos anterior y nuevo.",
    "pago": "Registro financiero de recaudos y abonos aplicados a una cita, con soporte para múltiples métodos de pago y estado de aprobación.",
    "procedimiento": "Catálogo maestro de servicios y procedimientos médicos/quirúrgicos con su tarifa estándar y estado.",
    "rol": "Catálogo de perfiles de acceso y autorización del sistema (Recepción, Veterinario, Administrador, Super Administrador).",
    "sede": "Maestro de sucursales o puntos de atención de la clínica veterinaria con su ubicación y dirección.",
    "signos_vitales": "Registro estructurado de parámetros fisiológicos tomados durante la valoración clínica de la mascota.",
    "tratamiento": "Diagnóstico médico e indicaciones terapéuticas formuladas por el veterinario en una cita.",
    "tratamiento_medicamento": "Detalle de prescripción farmacológica (fármaco, cantidad total, dosis, frecuencia y duración) dentro de un tratamiento.",
    "usuario": "Cuentas de acceso al sistema con credenciales cifradas, rol asignado y estado de activación.",
    "vacuna": "Historial de inmunizaciones aplicadas a cada mascota, incluyendo lote y fecha de refuerzo.",
    "vacunacion": "Registro complementario de esquemas de vacunación asociados a la atención clínica.",
    "veterinario": "Maestro de médicos veterinarios habilitados, vinculados a su cuenta de usuario, tarjeta profesional y sede principal.",
}

DESCRIPCIONES_COLS = {
    "idcita": "Identificador único autoincremental de la cita médica.",
    "fecha_hora": "Fecha y hora programada para la atención clínica.",
    "motivo": "Motivo de consulta reportado por el propietario al agendar.",
    "signos_vitales": "Resumen clínico de constantes fisiológicas registradas en consulta.",
    "idmascota": "Identificador único del paciente animal.",
    "idveterinario": "Identificador único del médico veterinario.",
    "idSede": "Identificador único de la sede o sucursal.",
    "idestado_cita": "Estado actual de la cita dentro del flujo operativo.",
    "idusuario": "Usuario del sistema responsable de la operación o registro.",
    "id_dueño": "Identificador único del propietario de la mascota.",
    "nombre_dueño": "Nombre completo del propietario.",
    "documento_dueño": "Número de documento de identidad del propietario.",
    "telefono_dueño": "Teléfono de contacto del propietario (usado para notificaciones).",
    "correo_dueño": "Correo electrónico de contacto del propietario.",
    "direccion_dueño": "Dirección de residencia del propietario.",
    "nombre_mascota": "Nombre del paciente animal.",
    "especie": "Especie animal (Canino, Felino, etc.).",
    "raza": "Raza o cruce del paciente.",
    "sexo": "Sexo biológico del paciente.",
    "fecha_nacimiento": "Fecha de nacimiento estimada o exacta de la mascota.",
    "peso": "Peso corporal registrado en kilogramos.",
    "alergias": "Alergias conocidas o contraindicaciones médicas del paciente.",
    "estado_paciente": "Condición vital del paciente (Activo / Fallecido).",
    "idMedicamento": "Identificador único del medicamento en el catálogo.",
    "nombre_Medicamento": "Nombre comercial o genérico del fármaco.",
    "precio": "Precio unitario de venta al público.",
    "activo": "Indicador lógico de vigencia (1 = Activo, 0 = Inactivo).",
    "idProcedimiento": "Identificador único del procedimiento clínico.",
    "nombre_Procedimiento": "Nombre del servicio o procedimiento médico.",
    "descripcion_procedimiento": "Descripción técnica del alcance del procedimiento.",
    "costo": "Tarifa estándar vigente del procedimiento.",
    "precio_cobrado": "Valor efectivamente facturado en la cita (inmutable ante cambios futuros de tarifa).",
    "idtratamiento": "Identificador único del diagnóstico/tratamiento médico.",
    "diagnostico": "Evaluación clínica y diagnóstico emitido por el veterinario.",
    "indicaciones": "Recomendaciones generales y cuidados post-consulta.",
    "idtratamiento_medicamento": "Identificador único del ítem formulado en la receta.",
    "cantidad": "Cantidad total de unidades/tabletas prescritas para entrega.",
    "dosis": "Cantidad específica por toma (ej. 1 tableta, 5 ml).",
    "frecuencia": "Intervalo de administración (ej. Cada 12 horas).",
    "duracion_dias": "Duración total del tratamiento expresada en días.",
    "id_entrega": "Identificador único del despacho realizado en farmacia.",
    "cantidad_entregada": "Unidades físicas entregadas al propietario en ese despacho.",
    "precio_unitario": "Precio unitario del medicamento al momento de la entrega.",
    "fecha_entrega": "Marca de tiempo exacta del despacho en farmacia.",
    "estado_entrega": "Estado del despacho ('Entregada' o 'Anulada').",
    "fecha_anulacion": "Marca de tiempo en caso de anulación del despacho.",
    "stock": "Existencias físicas disponibles en la sede.",
    "fecha_vencimiento": "Fecha de caducidad del lote almacenado en la sede.",
    "tipo_movimiento": "Clasificación del movimiento en kardex (ENTRADA, SALIDA_ENTREGA, ANULACION_ENTREGA, AJUSTE).",
    "stock_anterior": "Saldo de inventario previo al movimiento.",
    "stock_nuevo": "Saldo de inventario resultante tras el movimiento.",
    "referencia": "Documento, cita o motivo de respaldo del movimiento.",
    "idpago": "Identificador único de la transacción de pago.",
    "monto_total": "Valor monetario recaudado en la transacción.",
    "metodo_pago": "Medio de pago utilizado (Efectivo, Transferencia, Datáfono, etc.).",
    "estado_pago": "Estado financiero del pago (Aprobado / Reembolsado).",
    "referencia_externa": "Número de comprobante o aprobación bancaria externa.",
    "fecha_pago": "Fecha y hora de registro del recaudo en caja.",
}

conn = get_db_connection()
cur = conn.cursor(dictionary=True)

cur.execute("SELECT DATABASE() AS db_name")
db_name = cur.fetchone()["db_name"]

cur.execute("""
    SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_NAME
""")
tablas = cur.fetchall()

cur.execute("""
    SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_DEFAULT,
           IS_NULLABLE, COLUMN_TYPE, COLUMN_KEY, EXTRA
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
    ORDER BY TABLE_NAME, ORDINAL_POSITION
""")
cols_por_tabla = {}
for r in cur.fetchall():
    cols_por_tabla.setdefault(r["TABLE_NAME"], []).append(r)

cur.execute("""
    SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME,
           REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
    FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
    ORDER BY TABLE_NAME, COLUMN_NAME
""")
fks_por_tabla = {}
todas_fks = []
for r in cur.fetchall():
    fks_por_tabla.setdefault(r["TABLE_NAME"], {})[r["COLUMN_NAME"]] = (
        r["REFERENCED_TABLE_NAME"],
        r["REFERENCED_COLUMN_NAME"],
        r["CONSTRAINT_NAME"],
    )
    todas_fks.append(r)

cur.execute("""
    SELECT tc.TABLE_NAME, tc.CONSTRAINT_NAME, cc.CHECK_CLAUSE
    FROM information_schema.TABLE_CONSTRAINTS tc
    JOIN information_schema.CHECK_CONSTRAINTS cc
      ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
     AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
    WHERE tc.TABLE_SCHEMA = DATABASE() AND tc.CONSTRAINT_TYPE = 'CHECK'
    ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME
""")
checks_por_tabla = {}
for r in cur.fetchall():
    checks_por_tabla.setdefault(r["TABLE_NAME"], []).append(r)

cur.execute("SHOW TRIGGERS")
triggers = cur.fetchall()

cur.close()
conn.close()

doc = Document()

for section in doc.sections:
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.65)
    section.right_margin = Inches(0.65)

style_normal = doc.styles["Normal"]
style_normal.font.name = "Calibri"
style_normal.font.size = Pt(10)
style_normal.font.color.rgb = RGBColor(0x22, 0x22, 0x22)

MORADO = "5B2C83"
MORADO_CLARO = "EFE7F6"
GRIS_FILA = "F8F9FA"

p_sub = doc.add_paragraph()
p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_sub.paragraph_format.space_before = Pt(70)
r_sub = p_sub.add_run("DOCUMENTACIÓN TÉCNICA DE ARQUITECTURA DE DATOS")
r_sub.bold = True
r_sub.font.size = Pt(11)
r_sub.font.color.rgb = RGBColor(0x5B, 0x2C, 0x83)

p_tit = doc.add_paragraph()
p_tit.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_tit.paragraph_format.space_before = Pt(12)
r_tit = p_tit.add_run("DICCIONARIO DE DATOS COMPLETO\nSISTEMA DE GESTIÓN CLÍNICA VETERINARIA")
r_tit.bold = True
r_tit.font.size = Pt(22)
r_tit.font.color.rgb = RGBColor(0x1E, 0x1E, 0x2F)

p_desc = doc.add_paragraph()
p_desc.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_desc.paragraph_format.space_before = Pt(16)
r_desc = p_desc.add_run(
    "Especificación íntegra de entidades, atributos, dominios de datos, integridad referencial,\n"
    "restricciones CHECK y código completo de disparadores (Triggers) en MySQL."
)
r_desc.font.size = Pt(11.5)
r_desc.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

doc.add_paragraph("\n" * 3)

meta_table = doc.add_table(rows=5, cols=2)
meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
meta_rows = [
    ("Proyecto / Aplicación", "Veterinaria App (Flask + MySQL)"),
    ("Base de Datos / Esquema", f"{db_name} (InnoDB / UTF-8)"),
    ("Entorno de Despliegue", "Aiven Cloud MySQL & Render Web Service"),
    ("Total de Entidades / Triggers", f"{len(tablas)} Tablas Relacionales | {len(triggers)} Triggers Activos"),
    ("Fecha de Generación", datetime.now().strftime("%Y-%m-%d %H:%M")),
]
for idx, (k, v) in enumerate(meta_rows):
    row = meta_table.rows[idx]
    prevent_row_split(row)
    c0, c1 = row.cells[0], row.cells[1]
    c0.width = Inches(2.4)
    c1.width = Inches(3.8)
    set_cell_bg(c0, MORADO_CLARO)
    set_cell_bg(c1, "FFFFFF")
    set_cell_margins(c0, 90, 90, 140, 140)
    set_cell_margins(c1, 90, 90, 140, 140)
    r0 = c0.paragraphs[0].add_run(k)
    r0.bold = True
    r0.font.color.rgb = RGBColor(0x5B, 0x2C, 0x83)
    c1.paragraphs[0].add_run(v)

doc.add_page_break()

h1 = doc.add_heading("1. Resumen Arquitectónico y Convenciones", level=1)
h1.runs[0].font.color.rgb = RGBColor(0x5B, 0x2C, 0x83)

doc.add_paragraph(
    "El modelo relacional de Veterinaria App está diseñado bajo la Tercera Forma Normal (3FN) utilizando el motor "
    "transaccional InnoDB en MySQL 8. Garantiza consistencia ACID en operaciones críticas como el agendamiento sin "
    "cruce de horarios, el despacho farmacéutico con control de lote/vencimiento, la facturación en caja y la "
    "trazabilidad mediante bitácora de auditoría."
)

conv_table = doc.add_table(rows=6, cols=2)
conv_table.alignment = WD_TABLE_ALIGNMENT.CENTER
repeat_header_row(conv_table.rows[0])
convenciones = [
    ("Sigla / Símbolo", "Significado Técnico"),
    ("PK (Primary Key)", "Llave Primaria autoincremental que identifica de forma unívoca cada registro."),
    ("FK (Foreign Key)", "Llave Foránea que garantiza integridad referencial con la tabla maestra correspondiente."),
    ("UQ (Unique Key)", "Restricción de unicidad que impide duplicados de negocio (ej. documento, nombre de sede)."),
    ("NN (Not Null)", "Atributo obligatorio que no admite valores nulos en inserción o actualización."),
    ("CHECK", "Regla de validación a nivel de motor (ej. precios >= 0, cantidades > 0)."),
]
for idx, (a, b) in enumerate(convenciones):
    row = conv_table.rows[idx]
    prevent_row_split(row)
    c0, c1 = row.cells[0], row.cells[1]
    c0.width = Inches(2.0)
    c1.width = Inches(5.0)
    set_cell_margins(c0, 70, 70, 100, 100)
    set_cell_margins(c1, 70, 70, 100, 100)
    if idx == 0:
        set_cell_bg(c0, MORADO)
        set_cell_bg(c1, MORADO)
        for c, txt in [(c0, a), (c1, b)]:
            r = c.paragraphs[0].add_run(txt)
            r.bold = True
            r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    else:
        bg = GRIS_FILA if idx % 2 == 0 else "FFFFFF"
        set_cell_bg(c0, bg)
        set_cell_bg(c1, bg)
        r = c0.paragraphs[0].add_run(a)
        r.bold = True
        c1.paragraphs[0].add_run(b)

h2 = doc.add_heading("2. Catálogo General de Tablas", level=1)
h2.runs[0].font.color.rgb = RGBColor(0x5B, 0x2C, 0x83)

cat_table = doc.add_table(rows=len(tablas) + 1, cols=4)
cat_table.alignment = WD_TABLE_ALIGNMENT.CENTER
repeat_header_row(cat_table.rows[0])
prevent_row_split(cat_table.rows[0])
headers_cat = ["#", "Tabla", "Motor / Cotejamiento", "Propósito Funcional"]
widths_cat = [Inches(0.4), Inches(1.8), Inches(1.5), Inches(3.3)]
for j, htxt in enumerate(headers_cat):
    cell = cat_table.rows[0].cells[j]
    cell.width = widths_cat[j]
    set_cell_bg(cell, MORADO)
    set_cell_margins(cell, 70, 70, 80, 80)
    r = cell.paragraphs[0].add_run(htxt)
    r.bold = True
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    r.font.size = Pt(9)

for idx, t in enumerate(tablas, 1):
    tname = t["TABLE_NAME"]
    row = cat_table.rows[idx]
    prevent_row_split(row)
    bg = GRIS_FILA if idx % 2 == 0 else "FFFFFF"
    vals = [
        str(idx),
        tname,
        f"{t['ENGINE']} ({t['TABLE_COLLATION']})",
        DESCRIPCIONES_TABLAS.get(tname, "Entidad operativa del sistema veterinario."),
    ]
    for j, val in enumerate(vals):
        cell = row.cells[j]
        cell.width = widths_cat[j]
        set_cell_bg(cell, bg)
        set_cell_margins(cell, 60, 60, 80, 80)
        r = cell.paragraphs[0].add_run(val)
        r.font.size = Pt(8.5)
        if j == 1:
            r.bold = True

doc.add_page_break()

h3 = doc.add_heading("3. Especificación Detallada de Entidades y Atributos", level=1)
h3.runs[0].font.color.rgb = RGBColor(0x5B, 0x2C, 0x83)

col_headers = ["Atributo", "Tipo de Dato", "Nulo", "Clave", "Default / Extra", "Descripción y Reglas"]
col_widths = [Inches(1.45), Inches(1.15), Inches(0.45), Inches(0.65), Inches(1.05), Inches(2.25)]

for idx_t, t in enumerate(tablas, 1):
    tname = t["TABLE_NAME"]
    sub = doc.add_heading(f"3.{idx_t}. Tabla: {tname}", level=2)
    sub.runs[0].font.color.rgb = RGBColor(0x3D, 0x1D, 0x5C)
    sub.paragraph_format.keep_with_next = True

    p_desc_t = doc.add_paragraph()
    p_desc_t.paragraph_format.space_after = Pt(4)
    p_desc_t.paragraph_format.keep_with_next = True
    r_b = p_desc_t.add_run("Descripción: ")
    r_b.bold = True
    p_desc_t.add_run(DESCRIPCIONES_TABLAS.get(tname, "Tabla del esquema relacional."))

    cols = cols_por_tabla.get(tname, [])
    fks = fks_por_tabla.get(tname, {})
    checks = checks_por_tabla.get(tname, [])

    tbl = doc.add_table(rows=len(cols) + 1, cols=6)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    repeat_header_row(tbl.rows[0])
    prevent_row_split(tbl.rows[0])

    for j, htxt in enumerate(col_headers):
        cell = tbl.rows[0].cells[j]
        cell.width = col_widths[j]
        set_cell_bg(cell, MORADO)
        set_cell_margins(cell, 65, 65, 70, 70)
        r = cell.paragraphs[0].add_run(htxt)
        r.bold = True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        r.font.size = Pt(8.5)

    for i_c, col in enumerate(cols, 1):
        cname = col["COLUMN_NAME"]
        row = tbl.rows[i_c]
        prevent_row_split(row)
        bg = GRIS_FILA if i_c % 2 == 0 else "FFFFFF"

        claves = []
        if col["COLUMN_KEY"] == "PRI":
            claves.append("PK")
        if cname in fks:
            claves.append("FK")
        if col["COLUMN_KEY"] == "UNI":
            claves.append("UQ")
        clave_str = ", ".join(claves) if claves else "-"

        extra_parts = []
        if col["COLUMN_DEFAULT"] is not None:
            extra_parts.append(f"Def: {col['COLUMN_DEFAULT']}")
        if col["EXTRA"]:
            extra_parts.append(col["EXTRA"])
        extra_str = " | ".join(extra_parts) if extra_parts else "-"

        desc_col = DESCRIPCIONES_COLS.get(cname, f"Campo '{cname}' de la entidad {tname}.")
        if cname in fks:
            ref_t, ref_c, _ = fks[cname]
            desc_col += f" [Ref: {ref_t}.{ref_c}]"

        fila_vals = [
            cname,
            col["COLUMN_TYPE"],
            "SÍ" if col["IS_NULLABLE"] == "YES" else "NO",
            clave_str,
            extra_str,
            desc_col,
        ]

        for j, val in enumerate(fila_vals):
            cell = row.cells[j]
            cell.width = col_widths[j]
            set_cell_bg(cell, bg)
            set_cell_margins(cell, 55, 55, 70, 70)
            r = cell.paragraphs[0].add_run(val)
            r.font.size = Pt(8)
            if j == 0 or (j == 3 and clave_str != "-"):
                r.bold = True

    if checks:
        p_chk = doc.add_paragraph()
        p_chk.paragraph_format.space_before = Pt(3)
        p_chk.paragraph_format.space_after = Pt(6)
        r_ck = p_chk.add_run("Restricciones CHECK: ")
        r_ck.bold = True
        r_ck.font.size = Pt(8.5)
        chk_txt = "; ".join(f"{ck['CONSTRAINT_NAME']} {ck['CHECK_CLAUSE']}" for ck in checks)
        r_ck2 = p_chk.add_run(chk_txt)
        r_ck2.font.size = Pt(8.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(4)

doc.add_page_break()

h4 = doc.add_heading("4. Matriz de Relaciones (Llaves Foráneas)", level=1)
h4.runs[0].font.color.rgb = RGBColor(0x5B, 0x2C, 0x83)

fk_table = doc.add_table(rows=len(todas_fks) + 1, cols=4)
fk_table.alignment = WD_TABLE_ALIGNMENT.CENTER
repeat_header_row(fk_table.rows[0])
prevent_row_split(fk_table.rows[0])
fk_headers = ["Tabla Origen", "Columna FK", "Tabla Referenciada", "Restricción"]
fk_widths = [Inches(1.6), Inches(1.7), Inches(1.8), Inches(1.9)]
for j, htxt in enumerate(fk_headers):
    cell = fk_table.rows[0].cells[j]
    cell.width = fk_widths[j]
    set_cell_bg(cell, MORADO)
    set_cell_margins(cell, 65, 65, 70, 70)
    r = cell.paragraphs[0].add_run(htxt)
    r.bold = True
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    r.font.size = Pt(8.5)

for idx, fk in enumerate(todas_fks, 1):
    row = fk_table.rows[idx]
    prevent_row_split(row)
    bg = GRIS_FILA if idx % 2 == 0 else "FFFFFF"
    vals = [
        fk["TABLE_NAME"],
        fk["COLUMN_NAME"],
        f"{fk['REFERENCED_TABLE_NAME']}.{fk['REFERENCED_COLUMN_NAME']}",
        fk["CONSTRAINT_NAME"],
    ]
    for j, val in enumerate(vals):
        cell = row.cells[j]
        cell.width = fk_widths[j]
        set_cell_bg(cell, bg)
        set_cell_margins(cell, 55, 55, 70, 70)
        r = cell.paragraphs[0].add_run(val)
        r.font.size = Pt(8)
        if j == 0:
            r.bold = True

doc.add_page_break()

h5 = doc.add_heading("5. Lógica de Negocio y Disparadores (Triggers)", level=1)
h5.runs[0].font.color.rgb = RGBColor(0x5B, 0x2C, 0x83)

doc.add_paragraph(
    "A continuación se documentan de forma íntegra los disparadores (Triggers) implementados en el motor MySQL "
    "para garantizar la integridad transaccional y las reglas de negocio clínicas y administrativas:"
)

for idx_tr, tr in enumerate(triggers, 1):
    sub_tr = doc.add_heading(
        f"5.{idx_tr}. {tr['Trigger']} ({tr['Timing']} {tr['Event']} ON {tr['Table']})",
        level=2,
    )
    sub_tr.runs[0].font.color.rgb = RGBColor(0x3D, 0x1D, 0x5C)
    sub_tr.paragraph_format.keep_with_next = True

    tr_box = doc.add_table(rows=1, cols=1)
    tr_box.alignment = WD_TABLE_ALIGNMENT.CENTER
    prevent_row_split(tr_box.rows[0])
    cell = tr_box.rows[0].cells[0]
    cell.width = Inches(7.0)
    set_cell_bg(cell, GRIS_FILA)
    set_cell_margins(cell, 80, 80, 110, 110)
    p_code = cell.paragraphs[0]
    p_code.paragraph_format.space_after = Pt(0)
    r_code = p_code.add_run(tr["Statement"].strip())
    r_code.font.name = "Consolas"
    r_code.font.size = Pt(8)

    doc.add_paragraph().paragraph_format.space_after = Pt(4)

output_file = "Diccionario_Datos_VeterinariaApp.docx"
doc.save(output_file)
print(f"ARCHIVO GENERADO: {output_file}")
