"""Crea la base PostgreSQL local y migra SQLite sin exponer contraseñas."""
import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE_NAME = "fti_db"
DATABASE_USER = "fti_db"


def _read_user_environment(name):
    try:
        import winreg
    except ImportError:
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def _save_user_environment(name, value):
    try:
        import winreg
    except ImportError as exc:
        raise RuntimeError("La configuración automática está disponible solo en Windows.") from exc

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    os.environ[name] = value


def _validate_local_url(database_url):
    parsed = urlsplit(database_url)
    if (
        parsed.scheme not in ("postgres", "postgresql")
        or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or unquote(parsed.username or "") != DATABASE_USER
        or unquote(parsed.path.lstrip("/")) != DATABASE_NAME
    ):
        raise RuntimeError(
            "DATABASE_URL ya está configurada para otro destino. "
            "No se modificó ninguna base de datos."
        )


def _create_local_database():
    import psycopg
    from psycopg import sql

    admin_password = getpass.getpass("Contraseña de postgres configurada al instalar: ")
    if not admin_password:
        raise ValueError("La contraseña no puede estar vacía.")
    app_password = secrets.token_urlsafe(32)
    with psycopg.connect(
        host="127.0.0.1",
        port=5432,
        dbname="postgres",
        user="postgres",
        password=admin_password,
        autocommit=True,
    ) as connection:
        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (DATABASE_USER,)
        ).fetchone()
        database_exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE_NAME,)
        ).fetchone()
        if role_exists or database_exists:
            raise RuntimeError(
                f"Ya existe el usuario o la base {DATABASE_NAME}. "
                "No se modificó; configura DATABASE_URL para ese destino o revisa pgAdmin."
            )
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(DATABASE_USER), sql.Literal(app_password)
            )
        )
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(DATABASE_NAME), sql.Identifier(DATABASE_USER)
            )
        )
    return (
        f"postgresql://{DATABASE_USER}:{quote(app_password, safe='')}"
        f"@127.0.0.1:5432/{DATABASE_NAME}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", default=str(ROOT / "inventario.db"),
        help="Archivo SQLite que se va a migrar (se conserva intacto).",
    )
    args = parser.parse_args()
    if not Path(args.source).is_file():
        raise FileNotFoundError(f"No existe la base SQLite: {args.source}")

    stored_database_url = _read_user_environment("DATABASE_URL")
    database_url = os.environ.get("DATABASE_URL") or stored_database_url
    if database_url:
        _validate_local_url(database_url)
        if not stored_database_url:
            _save_user_environment("DATABASE_URL", database_url)
    else:
        database_url = _create_local_database()
        _save_user_environment("DATABASE_URL", database_url)

    stored_secret_key = _read_user_environment("SECRET_KEY")
    secret_key = os.environ.get("SECRET_KEY") or stored_secret_key
    if not secret_key:
        secret_key = secrets.token_urlsafe(48)
    if not stored_secret_key:
        _save_user_environment("SECRET_KEY", secret_key)
    else:
        os.environ["SECRET_KEY"] = secret_key

    import psycopg
    from psycopg.rows import dict_row

    from db import PostgreSQLConnection
    from migrar_postgresql import migrate_sqlite_to_postgresql

    connection = PostgreSQLConnection(
        psycopg.connect(database_url, row_factory=dict_row)
    )
    try:
        migrate_sqlite_to_postgresql(args.source, connection)
    finally:
        connection.close()
    print("Configuración lista: PostgreSQL local y datos SQLite migrados.")
    print("DATABASE_URL y SECRET_KEY quedaron guardadas para este usuario de Windows.")
    print("Cierra y vuelve a abrir PowerShell antes de iniciar la aplicación.")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        sys.exit(f"ERROR: {error}")
