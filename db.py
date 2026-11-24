import os
import mysql.connector


def cargar_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea and not linea.startswith("#") and "=" in linea:
                    k, v = linea.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")


cargar_env()


def get_db_connection():
    return mysql.connector.connect(
        port=int(os.getenv("DB_PORT", 3306)),
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "veterinaria"),
    )
