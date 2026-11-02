"""Agrega productos nuevos a una base SQLite o PostgreSQL.

Uso: python importar_productos.py ruta/a/inventario.db
     (PostgreSQL: configura DATABASE_URL y ejecuta python importar_productos.py)
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import db as database

STOCK_INICIAL = 100
STOCK_MINIMO = 50


def _respaldo_postgresql(database_url):
    executable = shutil.which("pg_dump")
    if not executable:
        raise RuntimeError("No se encontró pg_dump. Instala PostgreSQL completo antes de importar productos.")
    parsed = urlsplit(database_url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ValueError("DATABASE_URL debe ser una URL PostgreSQL.")
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(
        backup_dir, f"inventario_pre_productos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dump"
    )
    env = os.environ.copy()
    for key, value in (
        ("PGHOST", parsed.hostname),
        ("PGPORT", str(parsed.port or 5432)),
        ("PGDATABASE", unquote(parsed.path.lstrip("/"))),
        ("PGUSER", unquote(parsed.username or "")),
        ("PGPASSWORD", unquote(parsed.password or "")),
    ):
        if value:
            env[key] = value
    for key, values in parse_qs(parsed.query).items():
        if key in ("sslmode", "sslcert", "sslkey", "sslrootcert", "connect_timeout"):
            env["PG" + key.upper()] = values[-1]
    try:
        subprocess.run(
            [executable, "--format=custom", "--file", backup_path],
            check=True, env=env, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"No se pudo crear el respaldo PostgreSQL: {exc.stderr.strip()}") from exc
    print("Respaldo PostgreSQL creado:", backup_path)


def _importar_productos(connection, postgres=False):
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "productos_nuevos.json"), encoding="utf-8") as file:
        productos = json.load(file)
    ahora = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d %H:%M:%S")

    categorias = {
        row["nombre"]: row["id"]
        for row in connection.execute("SELECT id, nombre FROM categorias")
    }
    administrador = connection.execute(
        "SELECT id FROM usuarios WHERE rol = 'admin' ORDER BY id LIMIT 1"
    ).fetchone()
    if not administrador:
        raise RuntimeError("No existe un usuario administrador en la base de datos.")
    admin_id = administrador["id"]
    existentes = {row["codigo"] for row in connection.execute("SELECT codigo FROM productos")}

    nuevos = saltados = 0
    try:
        with connection:
            for producto in productos:
                if producto["codigo"] in existentes:
                    saltados += 1
                    continue
                cursor = connection.execute(
                    """INSERT INTO productos (codigo, nombre, imagen, categoria_id, unidad, costo_usd,
                                              precio_mayor_usd, stock, stock_minimo, vendidos, activo, creado)
                       VALUES (?,?,?,?,?,0,?,?,?,0,1,?)""" + (" RETURNING id" if postgres else ""),
                    (producto["codigo"], producto["nombre"], producto["imagen"],
                     categorias[producto["categoria"]], producto["unidad"],
                     producto["precio_mayor_usd"], STOCK_INICIAL, STOCK_MINIMO, ahora),
                )
                producto_id = cursor.fetchone()["id"] if postgres else cursor.lastrowid
                connection.execute(
                    """INSERT INTO movimientos_stock
                       (producto_id, fecha, tipo, cantidad, stock_resultante, motivo, venta_id, usuario_id)
                       VALUES (?,?,'entrada',?,?,'Stock inicial',NULL,?)""",
                    (producto_id, ahora, STOCK_INICIAL, STOCK_INICIAL, admin_id),
                )
                nuevos += 1
    except Exception:
        print("ERROR: no se completó la importación. Restaura la base desde su respaldo si hace falta.")
        raise
    print(f"Productos agregados: {nuevos} | ya existían (saltados): {saltados}")


def main(ruta_db=None):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url and ruta_db is None:
        _respaldo_postgresql(database_url)
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL requiere psycopg; instala requirements.txt.") from exc
        if database_url.startswith("postgres://"):
            database_url = "postgresql://" + database_url[len("postgres://"):]
        connection = database.PostgreSQLConnection(
            psycopg.connect(database_url, row_factory=dict_row)
        )
        try:
            _importar_productos(connection, postgres=True)
        finally:
            connection.close()
        return

    if not ruta_db:
        raise ValueError("Indica la ruta SQLite o configura DATABASE_URL para PostgreSQL.")
    backup = f"{ruta_db}.respaldo-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copyfile(ruta_db, backup)
    print("Respaldo creado:", backup)
    connection = sqlite3.connect(ruta_db)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        _importar_productos(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url and len(sys.argv) == 1:
        main()
    elif len(sys.argv) == 2 and os.path.exists(sys.argv[1]):
        main(sys.argv[1])
    else:
        sys.exit("Uso: python importar_productos.py ruta/a/inventario.db")
