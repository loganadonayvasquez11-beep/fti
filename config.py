"""Configuración general de la aplicación."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Valores de configuración de Flask. Se pueden sobreescribir con variables de entorno."""

    # Clave para firmar las sesiones. CÁMBIALA en producción (variable de entorno SECRET_KEY).
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32)

    # Ruta del archivo SQLite
    DATABASE = os.environ.get("DATABASE", os.path.join(BASE_DIR, "inventario.db"))
    # Si se define, PostgreSQL tiene prioridad sobre DATABASE.
    DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # máximo 5 MB por petición/subida

    # Venezuela está en UTC-4 y no usa horario de verano
    UTC_OFFSET_HORAS = -4

    # Cookies de sesión más seguras
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 horas
