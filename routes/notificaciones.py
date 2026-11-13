"""Suscripción y bandeja de notificaciones del navegador."""
from flask import Blueprint, current_app, g, jsonify, request

from db import query
from push_notifications import guardar_suscripcion
from utils import login_required

bp = Blueprint("notificaciones", __name__, url_prefix="/notificaciones")


@bp.get("/config")
@login_required
def config():
    return jsonify({
        "publicKey": current_app.config.get("VAPID_PUBLIC_KEY", ""),
        "pushEnabled": bool(
            current_app.config.get("VAPID_PUBLIC_KEY")
            and current_app.config.get("VAPID_PRIVATE_KEY")
            and current_app.config.get("VAPID_SUBJECT")
        ),
    })


@bp.post("/suscribir")
@login_required
def suscribir():
    data = request.get_json(silent=True) or {}
    subscription = data.get("subscription")
    if not isinstance(subscription, dict):
        return jsonify({"error": "La suscripción de notificaciones no es válida."}), 400
    try:
        guardar_suscripcion(g.user["id"], subscription)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.get("/pendientes")
@login_required
def pendientes():
    if g.user["rol"] == "admin":
        ventas = query(
            "SELECT id, numero FROM ventas WHERE estado='pendiente' ORDER BY id"
        )
        tasks = [
            {"id": f"venta-{venta['id']}", "url": f"/ventas/{venta['id']}",
             "label": f"Venta {venta['numero']} pendiente de aprobación"}
            for venta in ventas
        ]
    elif g.user["tipo_vendedor"] == "especial":
        armados = query(
            """SELECT a.id, v.numero FROM armados a
                 JOIN ventas v ON v.id = a.venta_id
                WHERE a.estado='pendiente' AND v.estado='aprobada'
                  AND (a.asignado_a = ? OR a.asignado_a IS NULL)
                ORDER BY a.id""",
            (g.user["id"],),
        )
        tasks = [
            {"id": f"armado-{armado['id']}", "url": "/armados/",
             "label": f"Pedido {armado['numero']} pendiente de armar"}
            for armado in armados
        ]
    else:
        tasks = []
    return jsonify({"tasks": tasks, "interval": 3000})
