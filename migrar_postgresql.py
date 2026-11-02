"""Copia una base SQLite existente a un PostgreSQL vacío sin modificar SQLite."""
import os
import sqlite3

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


def _crear_esquema_postgresql(connection):
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
    """)


def migrate_sqlite_to_postgresql(source_path, target):
    """Migra todas las tablas de aplicación dentro de una transacción PostgreSQL."""
    source_path = os.path.abspath(source_path)
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"No existe la base SQLite: {source_path}")
    if not database.is_postgresql(target):
        raise ValueError("El destino debe ser una conexión PostgreSQL.")

    source = sqlite3.connect(f"file:{source_path.replace(os.sep, '/')}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute("BEGIN")
        source_check = source.execute("PRAGMA integrity_check").fetchone()[0]
        if source_check != "ok":
            raise RuntimeError(f"La base SQLite no pasó integrity_check: {source_check}")
        if source.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("La base SQLite contiene referencias foráneas inválidas.")

        source_tables = {
            row["name"] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        unknown_tables = source_tables - set(TABLAS)
        if unknown_tables:
            raise RuntimeError(
                "Hay tablas no incluidas en la migración: " + ", ".join(sorted(unknown_tables))
            )

        existing_tables = {
            row["table_name"] for row in target.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' "
                "AND table_name NOT LIKE 'pg_%'"
            ).fetchall()
        }
        unknown_target_tables = existing_tables - set(TABLAS)
        if unknown_target_tables:
            raise RuntimeError(
                "El esquema PostgreSQL contiene tablas ajenas a la aplicación: "
                + ", ".join(sorted(unknown_target_tables))
            )

        target.execute("SELECT 1")
        for table in TABLAS:
            if database.table_exists(target, table):
                if target.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]:
                    raise RuntimeError(
                        f"El destino PostgreSQL ya contiene datos en {table}; "
                        "la migración solo acepta un destino vacío."
                    )

        with target:
            _crear_esquema_postgresql(target)
            for table in TABLAS:
                if table not in source_tables:
                    continue
                columns = [
                    column["name"] for column in source.execute(f'PRAGMA table_info("{table}")')
                ]
                target_columns = database.table_columns(target, table)
                if set(columns) != target_columns:
                    raise RuntimeError(
                        f"Las columnas de {table} no coinciden entre SQLite y PostgreSQL."
                    )
                order_column = "id" if "id" in columns else "clave"
                source_cursor = source.execute(
                    f'SELECT * FROM "{table}" ORDER BY "{order_column}"'
                )
                quoted_columns = ", ".join(f'"{column}"' for column in columns)
                placeholders = ", ".join("?" for _ in columns)
                insert_sql = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
                source_count = 0
                while True:
                    batch = source_cursor.fetchmany(500)
                    if not batch:
                        break
                    for row in batch:
                        target.execute(insert_sql, tuple(row))
                    source_count += len(batch)
                target_count = target.execute(
                    f'SELECT COUNT(*) AS n FROM "{table}"'
                ).fetchone()["n"]
                if target_count != source_count:
                    raise RuntimeError(
                        f"Cantidad de filas distinta en {table}: "
                        f"SQLite={source_count}, PostgreSQL={target_count}."
                    )
                expected_rows = source.execute(
                    f'SELECT * FROM "{table}" ORDER BY "{order_column}"'
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
                        if tuple(expected) != tuple(actual[column] for column in columns):
                            raise RuntimeError(f"El contenido migrado de {table} no coincide con SQLite.")

            has_sequences = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
            ).fetchone()
            sequences = {
                row["name"]: row["seq"]
                for row in source.execute("SELECT name, seq FROM sqlite_sequence")
            } if has_sequences else {}
            for table in TABLAS:
                if table not in source_tables or "id" not in database.table_columns(target, table):
                    continue
                sequence = target.execute(
                    "SELECT pg_get_serial_sequence(?, 'id') AS name", (table,)
                ).fetchone()["name"]
                if not sequence:
                    continue
                maximum = target.execute(
                    f'SELECT MAX(id) AS maximum FROM "{table}"'
                ).fetchone()["maximum"]
                sequence_value = max(maximum or 0, sequences.get(table, 0))
                if sequence_value:
                    target.execute(
                        "SELECT setval(?::regclass, ?, true)", (sequence, sequence_value)
                    )
                else:
                    target.execute(
                        "SELECT setval(?::regclass, 1, false)", (sequence,)
                    )
    finally:
        source.close()
