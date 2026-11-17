"""Panel principal (dashboard) para admin y vendedor."""
from flask import Blueprint, g, render_template

from services import listar_cobros_periodo, resumen_panel
from utils import hoy, login_required

bp = Blueprint("panel", __name__)


@bp.route("/")
@login_required
def inicio():
    """Muestra las cifras clave: ventas del mes, por cobrar, vencidas, pendientes y stock bajo."""
    return render_template("panel.html", r=resumen_panel(g.user))


@bp.route("/cobros/mes")
@login_required
def cobros_mes():
    """Muestra el detalle de los cobros recibidos durante el mes actual."""
    fecha_hoy = hoy()
    desde = fecha_hoy[:7] + "-01"
    vendedor_id = g.user["id"] if g.user["rol"] != "admin" else None
    cobros = listar_cobros_periodo(desde, fecha_hoy, vendedor_id)
    total = sum(float(cobro["monto_usd"]) for cobro in cobros)
    return render_template(
        "panel/cobros_mes.html",
        cobros=cobros,
        desde=desde,
        hasta=fecha_hoy,
        total=total,
    )
