"""Inicio y cierre de sesión, y cambio de clave del usuario."""
import secrets

from flask import Blueprint, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db, query
from services import audit
from utils import aviso, login_required

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Muestra el formulario de acceso y valida usuario y clave."""
    if g.get("user"):
        return redirect(url_for("panel.inicio"))
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip().lower()
        clave = request.form.get("clave", "")
        u = query("SELECT * FROM usuarios WHERE usuario = ?", (usuario,), one=True)
        if u and u["activo"] and check_password_hash(u["clave_hash"], clave):
            session.clear()                                  # evita fijación de sesión
            session["uid"] = u["id"]
            session["csrf"] = secrets.token_hex(16)
            g.user = u
            audit("login", usuario)
            destino = request.args.get("next", "")
            # solo se aceptan rutas internas para evitar redirecciones a sitios externos
            if destino.startswith("/") and not destino.startswith("//"):
                return redirect(destino)
            return redirect(url_for("panel.inicio"))
        aviso("Usuario o clave incorrectos.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    """Cierra la sesión actual."""
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/cuenta", methods=["GET", "POST"])
@login_required
def cuenta():
    """Permite al usuario cambiar su propia clave."""
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        repetir = request.form.get("repetir", "")
        if not check_password_hash(g.user["clave_hash"], actual):
            aviso("La clave actual no es correcta.", "error")
        elif len(nueva) < 6:
            aviso("La nueva clave debe tener al menos 6 caracteres.", "error")
        elif nueva != repetir:
            aviso("Las claves nuevas no coinciden.", "error")
        else:
            db = get_db()
            db.execute("UPDATE usuarios SET clave_hash = ? WHERE id = ?",
                       (generate_password_hash(nueva), g.user["id"]))
            db.commit()
            audit("cambio_clave", g.user["usuario"])
            aviso("Clave actualizada.")
            return redirect(url_for("panel.inicio"))
    return render_template("cuenta.html")
