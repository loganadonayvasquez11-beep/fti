"""Utilidades comunes: fechas, formatos, seguridad (login, roles, CSRF)."""
import functools
import math
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from markupsafe import Markup


# ------------------------------------------------------------------ fechas
def _zona():
    """Zona horaria fija de la aplicación (UTC-4 por defecto, Venezuela)."""
    return timezone(timedelta(hours=current_app.config.get("UTC_OFFSET_HORAS", -4)))


def ahora():
    """Fecha y hora actual como texto 'AAAA-MM-DD HH:MM:SS'."""
    return datetime.now(_zona()).strftime("%Y-%m-%d %H:%M:%S")


def hoy():
    """Fecha de hoy como texto 'AAAA-MM-DD'."""
    return datetime.now(_zona()).strftime("%Y-%m-%d")


def sumar_dias(fecha_iso, dias):
    """Suma 'dias' a una fecha 'AAAA-MM-DD' y devuelve el resultado en el mismo formato."""
    d = datetime.strptime(fecha_iso[:10], "%Y-%m-%d").date()
    return (d + timedelta(days=int(dias))).isoformat()


# ---------------------------------------------------------------- formatos
def to_float(valor, defecto=0.0):
    """Convierte texto a número aceptando coma o punto decimal. Devuelve 'defecto' si falla."""
    if valor is None:
        return defecto
    s = str(valor).strip().replace(" ", "")
    if not s:
        return defecto
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")   # 1.234,50 -> 1234.50
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return defecto


def parse_decimal_input(valor, campo="El importe", defecto=None):
    """Convierte un importe decimal escrito por el usuario o rechaza texto no numérico."""
    texto = "" if valor is None else str(valor).strip()
    if not texto:
        if defecto is not None:
            return defecto
        raise ValueError(f"{campo} es obligatorio.")
    if not re.fullmatch(r"(?:\d+(?:[.,]\d{0,2})?|[.,]\d{1,2})", texto):
        raise ValueError(f"{campo} debe ser numérico, sin letras y con máximo 2 decimales.")
    numero = float(texto.replace(",", "."))
    if not math.isfinite(numero):
        raise ValueError(f"{campo} está fuera del rango permitido.")
    return numero


def to_int(valor, defecto=0):
    """Convierte texto a entero; devuelve 'defecto' si no es válido."""
    try:
        return int(float(str(valor).strip()))
    except (ValueError, TypeError):
        return defecto


def fmt_num(v, dec=2):
    """Formato venezolano: 1.234,50"""
    try:
        s = f"{float(v):,.{dec}f}"
    except (ValueError, TypeError):
        s = f"{0:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_qty(v):
    """Cantidades sin decimales innecesarios (5.0 -> 5, 2.5 -> 2,5)."""
    try:
        v = float(v)
    except (ValueError, TypeError):
        return "0"
    return fmt_num(v, 0) if abs(v - round(v)) < 1e-9 else fmt_num(v, 3).rstrip("0").rstrip(",")


def fmt_money(v, moneda="USD"):
    """Monto en dólares: '$ 1.234,50' (el sistema ya no maneja bolívares)."""
    simbolo = "$"
    return f"{simbolo} {fmt_num(v)}"


def fmt_fecha(s):
    """'2026-10-26 16:40:00' -> '26/10/2026'."""
    if not s:
        return ""
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(s)


def fmt_fechahora(s):
    """'2026-10-26 16:40:00' -> '26/10/2026 16:40'."""
    if not s:
        return ""
    try:
        return datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(s)


# ---------------------------------------------------------------- seguridad
def login_required(view):
    """Decorador: exige una sesión iniciada; si no, manda al login."""
    @functools.wraps(view)
    def envuelta(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return envuelta


def roles_required(*roles):
    """Decorador: exige que el usuario tenga alguno de los roles indicados (403 si no)."""
    def decorador(view):
        @functools.wraps(view)
        @login_required
        def envuelta(*args, **kwargs):
            if g.user["rol"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return envuelta
    return decorador





def es_especial():
    """True si el usuario actual es un vendedor de tipo 'especial'."""
    return g.get("user") is not None and g.user["rol"] == "vendedor" and g.user["tipo_vendedor"] == "especial"


def especial_o_admin_required(view):
    """Decorador: solo admin o vendedor especial (403 para el resto)."""
    @functools.wraps(view)
    @login_required
    def envuelta(*args, **kwargs):
        if not (es_admin() or es_especial()):
            abort(403)
        return view(*args, **kwargs)
    return envuelta


def csrf_token():
    """Devuelve (y crea si hace falta) el token anti-CSRF de la sesión."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def csrf_field():
    """Campo oculto HTML con el token CSRF para incluir en todos los formularios POST."""
    return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')


def verificar_csrf():
    """Rechaza (400) cualquier POST cuyo token no coincida con el de la sesión."""
    enviado = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    esperado = session.get("csrf", "")
    if not esperado or not secrets.compare_digest(enviado, esperado):
        abort(400, "Token de seguridad inválido. Recarga la página e inténtalo de nuevo.")


def es_admin():
    """True si el usuario actual es administrador."""
    return g.get("user") is not None and g.user["rol"] == "admin"


def aviso(mensaje, tipo="ok"):
    """Atajo para mostrar un mensaje flash (tipo: ok, error, warn)."""
    flash(mensaje, tipo)
