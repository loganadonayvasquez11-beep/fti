"""Deudas y abonos. El vendedor cobra sobre sus deudores; el admin además puede poner, editar y quitar deudas."""
from io import BytesIO

from flask import Blueprint, abort, g, redirect, render_template, request, send_file, url_for

import pdf
import services
from db import query
from utils import aviso, hoy, login_required, roles_required, to_float, to_int

bp = Blueprint("deudas", __name__)


def _deuda_o_404(deuda_id):
    """Devuelve la deuda si el usuario puede verla (admin: todas; vendedor: las suyas)."""
    d = query("SELECT * FROM v_deudas WHERE id = ?", (deuda_id,), one=True)
    if d is None or (g.user["rol"] == "vendedor" and d["vendedor_id"] != g.user["id"]):
        abort(404)
    return d


@bp.route("/deudas")
@login_required
def lista():
    """Lista de deudas con filtros: estado (activas, vencidas, pagadas, pendientes, anuladas), texto y vendedor."""
    estado = request.args.get("estado", "activas")
    q = request.args.get("q", "").strip()
    vendedor = to_int(request.args.get("vendedor"))
    sql, args = "SELECT * FROM v_deudas WHERE 1=1", []
    if g.user["rol"] == "vendedor":
        sql += " AND vendedor_id = ?"; args.append(g.user["id"])
    elif vendedor:
        sql += " AND vendedor_id = ?"; args.append(vendedor)
    if estado == "activas":
        sql += " AND estado = 'activa'"
    elif estado == "vencidas":
        sql += " AND estado = 'activa' AND vencimiento < ?"; args.append(hoy())
    elif estado in ("pagada", "pendiente", "anulada"):
        sql += " AND estado = ?"; args.append(estado)
    if q:
        sql += " AND cliente_nombre LIKE ?"; args.append(f"%{q}%")
    sql += " ORDER BY vencimiento ASC, id DESC LIMIT 300"
    filas = query(sql, args)
    total = sum(d["saldo_usd"] for d in filas if d["estado"] == "activa")
    vendedores = query("SELECT id, nombre, rol FROM usuarios ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, nombre", (g.user["id"],)) if g.user["rol"] == "admin" else []
    return render_template("deudas/lista.html", deudas=filas, estado=estado, q=q, vendedor=vendedor,
                           vendedores=vendedores, total=total)


@bp.route("/deudas/<int:deuda_id>")
@login_required
def detalle(deuda_id):
    """Detalle de una deuda con su historial de abonos y el formulario para abonar."""
    d = _deuda_o_404(deuda_id)
    abonos = query(
        """SELECT a.*, u.nombre AS registrador FROM abonos a LEFT JOIN usuarios u ON u.id = a.registrado_por
            WHERE a.deuda_id = ? ORDER BY a.id DESC""", (deuda_id,))
    return render_template("deudas/detalle.html", d=d, abonos=abonos)


@bp.route("/deudas/<int:deuda_id>/abonar", methods=["POST"])
@login_required
def abonar(deuda_id):
    """Registra un abono (parcial o total) en dólares."""
    _deuda_o_404(deuda_id)
    f = request.form
    try:
        abono_id = services.registrar_abono(
            deuda_id, to_float(f.get("monto")), "USD", f.get("metodo", "").strip(),
            f.get("referencia", "").strip(), f.get("nota", "").strip(), g.user)
        aviso("Abono registrado.")
        return redirect(url_for("deudas.detalle", deuda_id=deuda_id, recibo=abono_id))
    except ValueError as e:
        aviso(str(e), "error")
        return redirect(url_for("deudas.detalle", deuda_id=deuda_id))


@bp.route("/abonos/<int:abono_id>/recibo.pdf")
@login_required
def recibo_pdf(abono_id):
    """Recibo de pago en PDF."""
    a = query("SELECT deuda_id FROM abonos WHERE id = ?", (abono_id,), one=True)
    if a is None:
        abort(404)
    _deuda_o_404(a["deuda_id"])
    return send_file(BytesIO(pdf.recibo_abono(abono_id)), mimetype="application/pdf", download_name=f"R-{abono_id:06d}.pdf")


@bp.route("/abonos/<int:abono_id>/anular", methods=["POST"])
@roles_required("admin")
def anular_abono(abono_id):
    """El admin anula un abono."""
    a = query("SELECT deuda_id FROM abonos WHERE id = ?", (abono_id,), one=True)
    if a is None:
        abort(404)
    try:
        services.anular_abono(abono_id)
        aviso("Abono anulado.")
    except ValueError as e:
        aviso(str(e), "error")
    return redirect(url_for("deudas.detalle", deuda_id=a["deuda_id"]))


# ------------------------------------------------------ gestión de deudas (admin)
@bp.route("/deudas/nueva", methods=["GET", "POST"])
@roles_required("admin")
def nueva():
    """El admin pone una deuda manual a cualquier cliente."""
    clientes = query("""SELECT c.id, c.nombre, c.dias_credito, u.nombre AS vendedor FROM clientes c
                          JOIN usuarios u ON u.id = c.vendedor_id WHERE c.activo = 1 ORDER BY c.nombre""")
    if request.method == "POST":
        f = request.form
        try:
            did = services.crear_deuda_manual(g.user["id"], to_int(f.get("cliente_id")), f.get("concepto", "").strip(),
                                              to_float(f.get("monto_usd")), max(to_int(f.get("dias_credito")), 0))
            aviso("Deuda agregada.")
            return redirect(url_for("deudas.detalle", deuda_id=did))
        except ValueError as e:
            aviso(str(e), "error")
    return render_template("deudas/form.html", d=request.form, clientes=clientes, nueva=True,
                           cliente_sel=to_int(request.args.get("cliente")))


@bp.route("/deudas/<int:deuda_id>/editar", methods=["GET", "POST"])
@roles_required("admin")
def editar(deuda_id):
    """El admin modifica monto, concepto o plazo de una deuda."""
    d = _deuda_o_404(deuda_id)
    if request.method == "POST":
        f = request.form
        try:
            services.editar_deuda(deuda_id, f.get("concepto", "").strip(), to_float(f.get("monto_usd")),
                                  max(to_int(f.get("dias_credito")), 0))
            aviso("Deuda actualizada.")
            return redirect(url_for("deudas.detalle", deuda_id=deuda_id))
        except ValueError as e:
            aviso(str(e), "error")
    return render_template("deudas/form.html", d=d, clientes=[], nueva=False)


@bp.route("/deudas/<int:deuda_id>/anular", methods=["POST"])
@roles_required("admin")
def anular(deuda_id):
    """El admin quita (anula) una deuda."""
    try:
        services.anular_deuda(deuda_id)
        aviso("Deuda quitada. Puedes reactivarla cuando quieras.")
    except ValueError as e:
        aviso(str(e), "error")
    return redirect(url_for("deudas.detalle", deuda_id=deuda_id))


@bp.route("/deudas/<int:deuda_id>/reactivar", methods=["POST"])
@roles_required("admin")
def reactivar(deuda_id):
    """El admin vuelve a poner una deuda que había quitado."""
    try:
        services.reactivar_deuda(deuda_id)
        aviso("Deuda reactivada.")
    except ValueError as e:
        aviso(str(e), "error")
    return redirect(url_for("deudas.detalle", deuda_id=deuda_id))
