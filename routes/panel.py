"""Panel principal (dashboard) para admin y vendedor."""
from flask import Blueprint, g, render_template

from services import resumen_panel
from utils import login_required

bp = Blueprint("panel", __name__)


@bp.route("/")
@login_required
def inicio():
    """Muestra las cifras clave: ventas del mes, por cobrar, vencidas, pendientes y stock bajo."""
    return render_template("panel.html", r=resumen_panel(g.user))
