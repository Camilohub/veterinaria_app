import re

VIAS_COLOMBIA = (
    "Calle", "Carrera", "Transversal", "Diagonal",
    "Avenida", "Circular", "Manzana", "Vereda"
)

UNIDADES_DOSIS = (
    "tableta(s)", "cápsula(s)", "ml", "gota(s)",
    "ampolla(s)", "sobre(s)", "aplicación(es)"
)

TLDS_VALIDOS = {
    "com", "co", "com.co", "edu.co", "gov.co", "org.co", "net.co",
    "org", "net", "edu", "gov", "es", "io", "me", "info", "biz",
    "us", "mx", "ar", "cl", "pe", "ec"
}

PATRONES_TECLADO = (
    "asdf", "sdfg", "dfgh", "fghj", "ghjk", "hjkl",
    "qwer", "wert", "erty", "rtyu", "tyui", "yuio", "uiop",
    "zxcv", "xcvb", "cvbn", "vbnm", "jkl", "asd", "qwe", "zxc"
)

def _parece_teclazo(texto):
    t = re.sub(r"[^a-záéíóúüñ]", "", texto.lower())
    if not t:
        return True
    if re.search(r"(.)\1{3,}", t):
        return True
    for pat in PATRONES_TECLADO:
        if pat in t:
            return True
    if re.search(r"[bcdfghjklmnpqrstvwxz]{4,}", t):
        return True
    if len(t) >= 6 and re.search(r"(.{2,4}).*\1.*\1", t):
        return True
    if len(t) >= 6 and t not in {"salada", "saladas", "alfalfa"}:
        if set(t).issubset(set("asdfghjkl")) or set(t).issubset(set("qwertyuiop")) or set(t).issubset(set("zxcvbnm")):
            return True
    return False

def validar_nombre_persona(nombre, etiqueta="El nombre"):
    nombre = " ".join((nombre or "").strip().split())
    if len(nombre) < 5 or len(nombre) > 80:
        return False, f"{etiqueta} debe tener entre 5 y 80 caracteres."
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]+", nombre):
        return False, f"{etiqueta} solo puede contener letras y espacios."
    palabras = nombre.split(" ")
    if len(palabras) < 2:
        return False, f"{etiqueta} debe incluir al menos nombre y apellido."
    for p in palabras:
        if len(p) < 2:
            return False, f"Cada palabra en {etiqueta.lower()} debe tener al menos 2 letras."
        if not re.search(r"[aeiouáéíóúüyAEIOUÁÉÍÓÚÜY]", p) or _parece_teclazo(p):
            return False, f"{etiqueta} ingresado no parece un nombre real."
    return True, nombre

def validar_cc(cc):
    cc = re.sub(r"\s+", "", str(cc or ""))
    if not re.fullmatch(r"([1-9][0-9]{5,7}|1[0-9]{9})", cc):
        return False, "La cédula colombiana debe tener de 6 a 8 dígitos, o 10 dígitos iniciando en 1."
    if len(set(cc)) == 1 or re.search(r"(\d)\1{4,}", cc) or cc in ("123456", "1234567", "12345678", "1234567890", "1012345678", "1000000000", "87654321", "98765432"):
        return False, "El número de cédula ingresado no es válido."
    return True, cc

def validar_telefono_co(tel):
    tel = re.sub(r"[\s\-()+]", "", str(tel or ""))
    if tel.startswith("57") and len(tel) == 12:
        tel = tel[2:]
    if not re.fullmatch(r"3[0-9]{9}", tel):
        return False, "El teléfono celular debe tener 10 dígitos e iniciar en 3."
    if not (300 <= int(tel[:3]) <= 351):
        return False, "El prefijo del operador celular colombiano no es válido."
    sufijo = tel[3:]
    pares = [sufijo[i:i+2] for i in range(0, 6, 2)]
    secuencias_obvias = (
        "1234567", "2345678", "3456789", "0123456",
        "7654321", "8765432", "9876543", "6543210",
        "1122334", "2233445", "3344556", "4455667", "5566778", "6677889",
        "0011223", "1122330", "1231231", "1234123"
    )
    if (
        len(set(sufijo)) <= 3
        or re.search(r"(\d)\1{3,}", sufijo)
        or all(p[0] == p[1] for p in pares)
        or sufijo in secuencias_obvias
        or tel in ("3123456789", "3012345678", "3987654321", "3001234567")
    ):
        return False, "El número celular ingresado parece ficticio o de prueba."
    return True, tel

def validar_email_estricto(correo):
    correo = (correo or "").strip().lower()
    if len(correo) > 100 or not re.fullmatch(r"[a-z0-9._%+-]{2,64}@[a-z0-9.-]{2,63}\.[a-z]{2,10}", correo):
        return False, "El formato del correo electrónico no es válido."
    local, dominio = correo.split("@", 1)
    partes_dom = dominio.split(".")
    tld = ".".join(partes_dom[-2:]) if len(partes_dom) >= 3 and ".".join(partes_dom[-2:]) in TLDS_VALIDOS else partes_dom[-1]
    if tld not in TLDS_VALIDOS:
        return False, f"La extensión de dominio '.{tld}' no es válida."
    dom_base = partes_dom[0]
    if len(dom_base) < 2 or _parece_teclazo(dom_base) or _parece_teclazo(re.sub(r"[^a-z]", "", local)):
        return False, "El correo electrónico ingresado parece ficticio o mal escrito."
    return True, correo

def construir_direccion_co(via_tipo, via_num, via_letra, num_sec, sec_letra, placa, complemento=""):
    via_tipo = (via_tipo or "").strip()
    if via_tipo not in VIAS_COLOMBIA:
        return False, "Seleccione un tipo de vía válido."
    try:
        n1 = int(via_num)
        n2 = int(num_sec)
        n3 = int(placa)
    except (TypeError, ValueError):
        return False, "Los números de la dirección deben ser valores numéricos enteros."
    if not (1 <= n1 <= 250 and 1 <= n2 <= 250 and 1 <= n3 <= 250):
        return False, "Los números de vía, cruce y placa deben estar entre 1 y 250."
    l1 = (via_letra or "").strip().upper()
    l2 = (sec_letra or "").strip().upper()
    if l1 and not re.fullmatch(r"([A-Z]|BIS|[A-Z]\s?BIS)", l1):
        return False, "Letra de vía principal no válida (ej. A, B o Bis)."
    if l2 and not re.fullmatch(r"[A-Z]", l2):
        return False, "Letra de número secundario no válida (A-Z)."
    comp = " ".join((complemento or "").strip().split())
    if len(comp) > 60:
        return False, "El complemento de la dirección no puede superar 60 caracteres."
    if comp:
        if len(comp) < 3 or not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\s.,#-]+", comp):
            return False, "Ingresa un barrio o complemento válido (mínimo 3 caracteres, sin símbolos extraños)."
        palabras_comp = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", comp)
        if not any(len(p) >= 2 for p in palabras_comp):
            return False, "El complemento o barrio debe incluir una palabra válida (ej. Barrio Fontibón, Apto 201)."
        for p in palabras_comp:
            if len(p) >= 4 and (not re.search(r"[aeiouáéíóúüyAEIOUÁÉÍÓÚÜY]", p) or _parece_teclazo(p)):
                return False, f"El texto '{p}' en el complemento/barrio no parece válido."
    parte1 = f"{via_tipo} {n1}{(' ' + l1) if l1 else ''}"
    parte2 = f"{n2}{l2}"
    direccion = f"{parte1} # {parte2} - {n3}"
    if comp:
        direccion += f", {comp}"
    return True, direccion

def validar_receta_medica(cantidad, dosis_num, dosis_unidad, frecuencia_horas, duracion_dias):
    try:
        cant = int(cantidad)
        d_num = float(dosis_num)
        freq = int(frecuencia_horas)
        dias = int(duracion_dias)
    except (TypeError, ValueError):
        return False, "Cantidad, dosis, frecuencia y días deben ser valores numéricos válidos."
    if not (1 <= cant <= 100):
        return False, "La cantidad total a despachar debe estar entre 1 y 100 unidades."
    if not (0.25 <= d_num <= 10):
        return False, "La dosis por toma debe estar entre 0.25 y 10."
    if dosis_unidad not in UNIDADES_DOSIS:
        return False, "Seleccione una unidad de dosis válida."
    if not (1 <= freq <= 72):
        return False, "La frecuencia debe estar entre cada 1 y 72 horas."
    if not (1 <= dias <= 60):
        return False, "La duración del tratamiento debe estar entre 1 y 60 días."
    d_str = f"{int(d_num)}" if d_num.is_integer() else f"{d_num:g}"
    return True, {
        "cantidad": cant,
        "dosis": f"{d_str} {dosis_unidad}",
        "frecuencia": f"Cada {freq} horas",
        "duracion_dias": dias,
    }

def validar_sql_consola(sql_raw):
    sql = (sql_raw or "").strip()
    if not sql:
        return False, "Escriba una sentencia SQL antes de ejecutar."
    if sql.endswith(";"):
        sql = sql[:-1].strip()
    if ";" in sql:
        return False, "No se permite ejecutar múltiples sentencias separadas por ';'."
    if "--" in sql or "/*" in sql or "#" in sql:
        return False, "No se permiten comentarios SQL (--, /*, #) en la consola."
    primer_token = sql.split()[0].upper()
    if primer_token not in ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "INSERT"):
        return False, f"Comando '{primer_token}' no permitido. Solo se autorizan SELECT, SHOW, DESCRIBE, EXPLAIN e INSERT."
    prohibidos = (
        r"\b(DELETE|DROP|TRUNCATE|UPDATE|ALTER|CREATE|REPLACE|RENAME|"
        r"GRANT|REVOKE|CALL|EXEC|EXECUTE|LOAD|OUTFILE|DUMPFILE|"
        r"HANDLER|LOCK|UNLOCK|SET|KILL|FLUSH|RESET)\b"
    )
    m = re.search(prohibidos, sql, flags=re.IGNORECASE)
    if m:
        return False, f"Operación bloqueada por seguridad: palabra reservada '{m.group(1).upper()}' no permitida."
    return True, sql


def validar_item_catalogo(nombre, etiqueta="El nombre"):
    nombre = " ".join((nombre or "").strip().split())
    if len(nombre) < 3 or len(nombre) > 80:
        return False, f"{etiqueta} debe tener entre 3 y 80 caracteres."
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\s.,()%+/-]+", nombre):
        return False, f"{etiqueta} contiene caracteres no permitidos."
    palabras = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", nombre)
    if not any(len(p) >= 3 for p in palabras):
        return False, f"{etiqueta} debe incluir una palabra válida de al menos 3 letras."
    palabras_min = [p.lower() for p in palabras if len(p) >= 2]
    if len(palabras_min) >= 2 and len(set(palabras_min)) == 1:
        return False, f"{etiqueta} no puede repetir la misma palabra."
    for p in palabras:
        if len(p) >= 3:
            if not re.search(r"[aeiouáéíóúüyAEIOUÁÉÍÓÚÜY]", p) or _parece_teclazo(p):
                return False, f"{etiqueta} '{nombre}' no parece un término clínico real."
    return True, nombre


def validar_descripcion_opcional(texto, etiqueta="La descripción"):
    texto = " ".join((texto or "").strip().split())
    if not texto:
        return True, ""
    if len(texto) < 4 or len(texto) > 150:
        return False, f"{etiqueta} debe tener entre 4 y 150 caracteres."
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\s.,;:()%+/-]+", texto):
        return False, f"{etiqueta} contiene caracteres no permitidos."
    palabras = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", texto)
    if not any(len(p) >= 3 for p in palabras):
        return False, f"{etiqueta} debe incluir palabras válidas."
    for p in palabras:
        if len(p) >= 3 and (not re.search(r"[aeiouáéíóúüyAEIOUÁÉÍÓÚÜY]", p) or _parece_teclazo(p)):
            return False, f"{etiqueta} contiene texto no válido ('{p}')."
    return True, texto


VACUNAS_MASCOTAS = (
    "Rabia",
    "Pentavalente Canina (DHPPI + L)",
    "Hexavalente Canina",
    "Parvovirus Canino",
    "Moquillo / Distemper Canino",
    "Bordetella (Tos de las Perreras)",
    "Leptospirosis Canina",
    "Triple Felina (FVRCP)",
    "Leucemia Felina (FeLV)",
    "Polivalente / Refuerzo Anual",
)
