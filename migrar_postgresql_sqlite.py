"""Copia una base PostgreSQL existente a un archivo SQLite nuevo."""
import os
import sqlite3
import uuid

import db as database


TABLAS = (
    "usuarios",
    "configuracion",
    "categorias",
    "productos",
    "clientes",
    "ventas",
    "venta_detalles",
    "deudas",
    "abonos",
    "movimientos_stock",
    "auditoria",
    "precio_cambios",
    "armados",
    "archivos_historicos",
)


def _crear_esquema_sqlite(connection):
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    with open(schema_path, encoding="utf-8") as schema_file:
        connection.executescript(schema_file.read())
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS precio_cambios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL REFERENCES productos(id),
            usuario_id INTEGER REFERENCES usuarios(id),
            fecha TEXT NOT NULL,
            expira TEXT NOT NULL,
            mayor_ant REAL NOT NULL,
            mayor_nuevo REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_precio_cambios ON precio_cambios(expira, producto_id);
        CREATE TABLE IF NOT EXISTS armados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venta_id INTEGER NOT NULL UNIQUE REFERENCES ventas(id),
            asignado_a INTEGER REFERENCES usuarios(id),
            estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'armado')),
            asignado_por INTEGER REFERENCES usuarios(id),
            fecha_asignacion TEXT NOT NULL,
            armado_por INTEGER REFERENCES usuarios(id),
            fecha_armado TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_armados_estado ON armados(estado, asignado_a);
        CREATE TABLE IF NOT EXISTS archivos_historicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            tipo TEXT NOT NULL,
            desde TEXT NOT NULL,
            hasta TEXT NOT NULL,
            generado TEXT NOT NULL,
            ruta TEXT NOT NULL,
            usuario_id INTEGER REFERENCES usuarios(id),
            estado TEXT NOT NULL DEFAULT 'listo'
        );
    """)


def _tabla_sqlite_vacia(connection):
    return connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone() is None


def migrate_postgresql_to_sqlite(source_url, destination_path):
    """Migra todas las tablas conocidas sin alterar PostgreSQL ni pisar datos SQLite."""
    if destination_path == ":memory:":
        raise ValueError("La migración requiere un archivo SQLite persistente.")

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "La migración desde PostgreSQL requiere psycopg; instala requirements.txt."
        ) from exc

    destination_path = os.path.abspath(destination_path)
    destination_dir = os.path.dirname(destination_path)
    os.makedirs(destination_dir, exist_ok=True)
    if os.path.exists(destination_path):
        with sqlite3.connect(destination_path) as existing:
            if not _tabla_sqlite_vacia(existing):
                raise RuntimeError(
                    f"El destino SQLite ya contiene tablas: {destination_path}. "
                    "Usa un archivo nuevo para evitar sobrescribir datos."
                )

    temporary_path = f"{destination_path}.migration-{uuid.uuid4().hex}.tmp"
    source = psycopg.connect(source_url, row_factory=dict_row)
    target = None
    try:
        source_tables = {
            row["table_name"] for row in source.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
            ).fetchall()
        }
        unknown_tables = source_tables - set(TABLAS)
        if unknown_tables:
            raise RuntimeError(
                "Hay tablas no incluidas en la migración: " + ", ".join(sorted(unknown_tables))
            )

        target = sqlite3.connect(temporary_path)
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA foreign_keys = ON")
        _crear_esquema_sqlite(target)

        with target:
            target.execute("BEGIN IMMEDIATE")
            for table in TABLAS:
                if table not in source_tables:
                    continue
                columns = [
                    row["column_name"] for row in source.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = %s "
                        "ORDER BY ordinal_position", (table,)
                    ).fetchall()
                ]
                target_columns = database.table_columns(target, table)
                if set(columns) != target_columns:
                    raise RuntimeError(
                        f"Las columnas de {table} no coinciden entre PostgreSQL y SQLite. "
                        "Actualiza primero el esquema de origen."
                    )

                order_column = "id" if "id" in columns else "clave"
                quoted_columns = ", ".join(f'"{column}"' for column in columns)
                source_rows = source.execute(
                    f'SELECT {quoted_columns} FROM "{table}" ORDER BY "{order_column}"'
                )
                placeholders = ", ".join("?" for _ in columns)
                target_sql = (
                    f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
                )
                count = 0
                while True:
                    batch = source_rows.fetchmany(500)
                    if not batch:
                        break
                    target.executemany(
                        target_sql,
                        [tuple(row[column] for column in columns) for row in batch],
                    )
                    count += len(batch)

                actual_count = target.execute(
                    f'SELECT COUNT(*) AS n FROM "{table}"'
                ).fetchone()["n"]
                if actual_count != count:
                    raise RuntimeError(
                        f"Cantidad de filas distinta en {table}: "
                        f"PostgreSQL={count}, SQLite={actual_count}."
                    )

                expected_rows = source.execute(
                    f'SELECT {quoted_columns} FROM "{table}" ORDER BY "{order_column}"'
                )
                actual_rows = target.execute(
                    f'SELECT {quoted_columns} FROM "{table}" ORDER BY "{order_column}"'
                )
                while True:
                    expected_batch = expected_rows.fetchmany(500)
                    actual_batch = actual_rows.fetchmany(500)
                    if len(expected_batch) != len(actual_batch):
                        raise RuntimeError(f"No coinciden las filas migradas de {table}.")
                    if not expected_batch:
                        break
                    for expected, actual in zip(expected_batch, actual_batch):
                        if tuple(expected[column] for column in columns) != tuple(
                            actual[column] for column in columns
                        ):
                            raise RuntimeError(
                                f"El contenido migrado no coincide en la tabla {table}."
                            )

            violations = target.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    "La copia contiene referencias inválidas; PostgreSQL no fue modificado."
                )
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"La copia SQLite no pasó integrity_check: {integrity}")
    except Exception:
        if target is not None:
            target.close()
            target = None
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
        raise
    finally:
        source.close()
        if target is not None:
            target.close()

    os.replace(temporary_path, destination_path)
