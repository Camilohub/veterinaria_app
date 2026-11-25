from datetime import datetime
from db import get_db_connection

conn = get_db_connection()
cur = conn.cursor(dictionary=True)

cur.execute("SELECT DATABASE() AS db_name")
db_name = cur.fetchone()["db_name"]

cur.execute("""
    SELECT TABLE_NAME
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_NAME
""")
tablas = [r["TABLE_NAME"] for r in cur.fetchall()]

cur.execute("""
    SELECT TABLE_NAME, REFERENCED_TABLE_NAME
    FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
""")
deps = {t: set() for t in tablas}
for r in cur.fetchall():
    if r["REFERENCED_TABLE_NAME"] != r["TABLE_NAME"]:
        deps[r["TABLE_NAME"]].add(r["REFERENCED_TABLE_NAME"])

ordenadas = []
restantes = list(tablas)
while restantes:
    progressed = False
    for t in list(restantes):
        if deps[t].issubset(set(ordenadas)):
            ordenadas.append(t)
            restantes.remove(t)
            progressed = True
    if not progressed:
        ordenadas.extend(restantes)
        break

sql_lines = [
    "-- Script SQL Consolidado - Veterinaria App",
    f"-- Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    f"-- Esquema origen: {db_name}",
    "",
    "SET NAMES utf8mb4;",
    "SET FOREIGN_KEY_CHECKS = 0;",
    "",
]

for t in ordenadas:
    cur.execute(f"SHOW CREATE TABLE `{t}`")
    row = cur.fetchone()
    stmt = row.get("Create Table") or list(row.values())[1]
    sql_lines.append(f"-- Tabla: {t}")
    sql_lines.append(f"DROP TABLE IF EXISTS `{t}`;")
    sql_lines.append(stmt + ";")
    sql_lines.append("")

sql_lines.append("SET FOREIGN_KEY_CHECKS = 1;")
sql_lines.append("")
sql_lines.append("-- ============================================================")
sql_lines.append("-- TRIGGERS ACTIVOS")
sql_lines.append("-- ============================================================")
sql_lines.append("")

cur.execute("SHOW TRIGGERS")
triggers = cur.fetchall()
for tr in triggers:
    sql_lines.append(f"DROP TRIGGER IF EXISTS `{tr['Trigger']}`;")
    sql_lines.append("DELIMITER $$")
    sql_lines.append(f"CREATE TRIGGER `{tr['Trigger']}` {tr['Timing']} {tr['Event']} ON `{tr['Table']}`")
    sql_lines.append("FOR EACH ROW")
    sql_lines.append(tr["Statement"].strip() + "$$")
    sql_lines.append("DELIMITER ;")
    sql_lines.append("")

with open("schema_veterinaria.sql", "w", encoding="utf-8") as f:
    f.write("\n".join(sql_lines))

cur.execute("""
    SELECT
        c.TABLE_NAME,
        c.COLUMN_NAME,
        c.COLUMN_TYPE,
        c.COLUMN_KEY,
        k.REFERENCED_TABLE_NAME
    FROM information_schema.COLUMNS c
    LEFT JOIN information_schema.KEY_COLUMN_USAGE k
      ON k.TABLE_SCHEMA = c.TABLE_SCHEMA
     AND k.TABLE_NAME = c.TABLE_NAME
     AND k.COLUMN_NAME = c.COLUMN_NAME
     AND k.REFERENCED_TABLE_NAME IS NOT NULL
    WHERE c.TABLE_SCHEMA = DATABASE()
    ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
""")
cols_info = {}
relaciones = set()
for r in cur.fetchall():
    t = r["TABLE_NAME"]
    ctype = r["COLUMN_TYPE"].split("(")[0].replace(" ", "_")
    key_tag = "PK" if r["COLUMN_KEY"] == "PRI" else ("FK" if r["REFERENCED_TABLE_NAME"] else "")
    cols_info.setdefault(t, []).append(f"        {ctype} {r['COLUMN_NAME']} {key_tag}".strip())
    if r["REFERENCED_TABLE_NAME"]:
        relaciones.add(f"    {r['REFERENCED_TABLE_NAME']} ||--o{{ {t} : {r['COLUMN_NAME']}")

mmd_lines = ["erDiagram"]
for t in ordenadas:
    mmd_lines.append(f"    {t} {{")
    for c_line in cols_info.get(t, []):
        mmd_lines.append(c_line)
    mmd_lines.append("    }")
mmd_lines.extend(sorted(relaciones))

with open("diagrama_er.mmd", "w", encoding="utf-8") as f:
    f.write("\n".join(mmd_lines))

cur.close()
conn.close()
print(f"SQL LIMPIO GENERADO: schema_veterinaria.sql ({len(ordenadas)} tablas, {len(triggers)} triggers)")
print("MERMAID ER GENERADO: diagrama_er.mmd")
