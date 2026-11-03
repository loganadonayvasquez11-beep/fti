"""Configuración general de la aplicación."""
import os


class Config:
    """Valores de configuración de Flask. Se pueden sobreescribir con variables de entorno."""

    # Clave para firmar las sesiones. CÁMBIALA en producción (variable de entorno SECRET_KEY).
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32)

    # La base SQLite queda fuera del código versionado, en instance/ por defecto.
    DATABASE_PATH = os.environ.get("DATABASE_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "instance", "inventario.db"
    )
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # máximo 5 MB por petición/subida

    # Venezuela está en UTC-4 y no usa horario de verano
    UTC_OFFSET_HORAS = -4

    # Cookies de sesión más seguras
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 horas
