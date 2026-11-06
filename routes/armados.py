"""Pedidos para armar.

El admin, al aprobar una venta (o después, si fue de contado), decide qué vendedor ESPECIAL arma el pedido,
o elige "cualquiera" para que llegue a todos los especiales. El vendedor que aparece es el responsable actual
del cliente; el pedido sigue asociado a la venta original.
"""
from flask import Blueprint, abort, g, redirect, render_template, request, url_for

import services
from db import query
from utils import aviso, es_especial, especial_o_admin_required, login_required, roles_required

bp = Blueprint("armados", __name__, url_prefix="/armados")


def _lista_armados(donde, args):
    """Armados con datos de la venta y de las personas implicadas; cada uno trae sus productos."""
    filas = query(
        f"""SELECT a.*, v.numero, v.tipo_pago, v.fecha AS venta_fecha, c.vendedor_id, uv.nombre AS vendedor, c.nombre AS cliente,
                   ua.nombre AS asignado_nombre, ub.nombre AS armador_nombre
              FROM armados a JOIN ventas v ON v.id = a.venta_id AND v.estado = 'aprobada'
              JOIN clientes c ON c.id = v.cliente_id JOIN usuarios uv ON uv.id = c.vendedor_id
              LEFT JOIN usuarios ua ON ua.id = a.asignado_a LEFT JOIN usuarios ub ON ub.id = a.armado_por
             WHERE {donde} ORDER BY (a.estado = 'pendiente') DESC, a.id DESC LIMIT 100""", args)
    return [{"a": f, "lineas": services.lineas_armado(f["venta_id"])} for f in filas]


@bp.route("/")
@login_required
def lista():
    """Bandeja de pedidos para armar.

    Vendedor especial: los que le asignaron + los de "cualquiera" + los que ya armó.
    Admin: todos, más las ventas aprobadas que aún no tienen a nadie asignado. Vendedor normal: no aplica (403)."""
    if g.user["rol"] == "admin":
        estado = request.args.get("estado", "pendiente")
        esperando_salida = request.args.get("esperando_salida") == "1"
        if esperando_salida:
            estado = "armado"
            donde, args = ("a.estado = 'armado' AND COALESCE(a.fecha_salida, '') = ''", [])
            sin_asignar = []
        else:
            donde, args = ("1=1", []) if estado == "todos" else ("a.estado = ?", [estado if estado in ("pendiente", "armado") else "pendiente"])
            sin_asignar = query(
                """SELECT v.id, v.numero, v.fecha, uv.nombre AS vendedor, c.nombre AS cliente
                     FROM ventas v JOIN clientes c ON c.id = v.cliente_id JOIN usuarios uv ON uv.id = c.vendedor_id
                    WHERE v.estado = 'aprobada' AND v.id > ? AND NOT EXISTS (SELECT 1 FROM armados a WHERE a.venta_id = v.id)
                    ORDER BY v.id DESC LIMIT 100""", (services.armado_desde_venta(),))
        return render_template("armados/lista.html", pedidos=_lista_armados(donde, args), sin_asignar=sin_asignar,
                               estado=estado, esperando_salida=esperando_salida, modo="admin")
    if not es_especial():
        abort(403)
    uid = g.user["id"]
    pendientes = _lista_armados("a.estado = 'pendiente' AND (a.asignado_a = ? OR a.asignado_a IS NULL)", [uid])
    hechos = _lista_armados("a.estado = 'armado' AND a.armado_por = ?", [uid])[:20]
    sin_asignar = query(
        """SELECT v.id, v.numero, v.fecha, uv.nombre AS vendedor, c.nombre AS cliente
             FROM ventas v JOIN clientes c ON c.id = v.cliente_id JOIN usuarios uv ON uv.id = c.vendedor_id
            WHERE v.estado = 'aprobada' AND v.id > ? AND NOT EXISTS (SELECT 1 FROM armados a WHERE a.venta_id = v.id)
            ORDER BY v.id DESC LIMIT 100""", (services.armado_desde_venta(),))
    return render_template("armados/lista.html", pedidos=pendientes, hechos=hechos, sin_asignar=sin_asignar,
                           estado="pendiente", modo="especial")


@bp.route("/asignar/<int:venta_id>", methods=["POST"])
@especial_o_admin_required
def asignar(venta_id):
    """El admin o vendedor especial asigna (o reasigna) quién arma una venta aprobada."""
    try:
        services.asignar_armado(venta_id, request.form.get("armador", ""), g.user["id"])
        aviso("Pedido enviado a armar.")
    except ValueError as e:
        aviso(str(e), "error")
    destino = request.form.get("volver", "")
    return redirect(destino if destino.startswith("/") and not destino.startswith("//")
                    else url_for("ventas.detalle", venta_id=venta_id))


@bp.route("/venta/<int:venta_id>/marcar-armado", methods=["POST"])
@roles_required("admin")
def marcar_armado_admin(venta_id):
    """Permite al administrador marcar un pedido como armado sin asignarlo a un vendedor."""
    try:
        services.admin_confirmar_armado(venta_id, g.user["id"])
        aviso("Pedido marcado como armado sin asignarlo a un vendedor.")
    except ValueError as e:
        aviso(str(e), "error")
    destino = request.form.get("volver", "")
    return redirect(destino if destino.startswith("/") and not destino.startswith("//")
                    else url_for("armados.lista"))


@bp.route("/<int:armado_id>/confirmar", methods=["POST"])
@login_required
def confirmar(armado_id):
    """El vendedor especial confirma que armó el pedido. El primero en confirmar se queda con él."""
    if not es_especial():
        abort(403)
    try:
        services.confirmar_armado(armado_id, g.user)
        aviso("Pedido armado. Se le avisó al vendedor de la venta.")
    except ValueError as e:
        aviso(str(e), "error")
    return redirect(url_for("armados.lista"))


@bp.route("/<int:armado_id>/salida", methods=["POST"])
@especial_o_admin_required
def salida(armado_id):
    """El admin o un vendedor especial elige cuándo sale un pedido ya armado."""
    try:
        services.programar_salida(armado_id, request.form.get("fecha_salida", ""), g.user["id"])
        aviso("Fecha de salida guardada. El crédito y su vencimiento se calcularon desde esa fecha.")
    except ValueError as e:
        aviso(str(e), "error")
    destino = request.form.get("volver", "")
    return redirect(destino if destino.startswith("/") and not destino.startswith("//")
                    else url_for("armados.lista"))
