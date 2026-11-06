"""Clientes: cada vendedor administra su cartera; el admin ve y edita todas."""
import re

from flask import Blueprint, abort, g, redirect, render_template, request, url_for
from io import BytesIO

import pdf
from pdf_responses import nombre_pdf_cliente, responder_pdf
from db import get_db, query
from services import audit, saldo_cliente_usd
from utils import aviso, ahora, login_required, roles_required, to_int

bp = Blueprint("clientes", __name__, url_prefix="/clientes")


def _cliente_o_404(cliente_id):
    """Devuelve el cliente si el usuario puede verlo (admin: todos; vendedor: los suyos)."""
    c = query(
        "SELECT c.*, u.nombre AS vendedor_nombre FROM clientes c JOIN usuarios u ON u.id = c.vendedor_id WHERE c.id = ?",
        (cliente_id,), one=True)
    if c is None or (g.user["rol"] == "vendedor" and c["vendedor_id"] != g.user["id"]):
        abort(404)
    return c


@bp.route("/")
@login_required
def lista():
    """Lista de clientes con su deuda actual. Filtros: búsqueda y (admin) vendedor."""
    q = request.args.get("q", "").strip()
    vendedor = to_int(request.args.get("vendedor"))
    sql = """SELECT c.*, u.nombre AS vendedor_nombre,
                    COALESCE((SELECT SUM(saldo_usd) FROM v_deudas d
                               WHERE d.cliente_id = c.id AND d.estado = 'activa'), 0) AS deuda_usd
               FROM clientes c JOIN usuarios u ON u.id = c.vendedor_id WHERE c.activo = 1"""
    args = []
    if g.user["rol"] == "vendedor":
        sql += " AND c.vendedor_id = ?"; args.append(g.user["id"])
    elif vendedor:
        sql += " AND c.vendedor_id = ?"; args.append(vendedor)
    if q:
        sql += " AND (c.nombre LIKE ? OR c.documento LIKE ? OR c.telefono LIKE ?)"; args += [f"%{q}%"] * 3
    sql += " ORDER BY c.nombre"
    vendedores = query("SELECT id, nombre FROM usuarios WHERE rol='vendedor' ORDER BY nombre") if g.user["rol"] == "admin" else []
    return render_template("clientes/lista.html", clientes=query(sql, args), vendedores=vendedores,
                           q=q, tipo="", vendedor=vendedor)


def _leer_form(f):
    """Lee datos de identidad del cliente. El pago se decide en cada venta, no aquí."""
    nombre = f.get("nombre", "").strip()
    if not nombre:
        return None, "El nombre del cliente es obligatorio."
    tipo_documento = f.get("documento_tipo", "V").strip().upper()
    numero_documento = f.get("documento_numero", "").strip()
    if tipo_documento not in {"J", "V", "E"}:
        return None, "Selecciona un tipo de identificación válido."
    if numero_documento and not re.fullmatch(r"[0-9]+", numero_documento):
        return None, "La identificación solo puede contener números."
    return {
        "nombre": nombre,
        "documento": f"{tipo_documento}-{numero_documento}" if numero_documento else "",
        "telefono": f.get("telefono", "").strip(), "direccion": f.get("direccion", "").strip(),
        # Compatibilidad con el esquema existente: los campos históricos ya no gobiernan ventas.
        "tipo_pago": "contado", "limite": 0,
        # Plazo de crédito fijo del cliente: solo lo lee/cambia el admin (0 = automático por monto).
        # Para un vendedor queda en None y no se toca el valor guardado.
        "dias_credito": max(to_int(f.get("dias_credito")), 0) if g.user["rol"] == "admin" else None,
    }, None


@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
def nuevo():
    """Crea un cliente. El vendedor lo agrega a su cartera; el admin elige el vendedor."""
    vendedores = query("SELECT id, nombre FROM usuarios WHERE rol='vendedor' AND activo=1 ORDER BY nombre")
    if request.method == "POST":
        datos, error = _leer_form(request.form)
        vendedor_id = g.user["id"] if g.user["rol"] == "vendedor" else to_int(request.form.get("vendedor_id"))
        if not error and not vendedor_id:
            error = "Selecciona el vendedor al que pertenece el cliente."
        if error:
            aviso(error, "error")
            return render_template("clientes/form.html", c=request.form, vendedores=vendedores, nuevo=True,
                                   documento_tipo=request.form.get("documento_tipo", "V"),
                                   documento_numero=request.form.get("documento_numero", ""))
        db = get_db()
        cur = db.execute(
            """INSERT INTO clientes (vendedor_id, nombre, documento, telefono, direccion, tipo_pago,
               dias_credito, limite_credito_usd, creado) VALUES (?,?,?,?,?,?,?,?,?)""",
            (vendedor_id, datos["nombre"], datos["documento"], datos["telefono"], datos["direccion"],
             datos["tipo_pago"], datos["dias_credito"] or 0, datos["limite"], ahora()))
        db.commit()
        cliente_id = cur.lastrowid
        audit("cliente_creado", datos["nombre"])
        aviso("Cliente agregado a la cartera.")
        siguiente = request.args.get("volver_a_venta")
        return redirect(url_for("ventas.nueva", cliente=cliente_id) if siguiente else url_for("clientes.detalle", cliente_id=cliente_id))
    return render_template("clientes/form.html", c={"tipo_pago": "contado"}, vendedores=vendedores, nuevo=True,
                           documento_tipo="V", documento_numero="")


@bp.route("/<int:cliente_id>")
@login_required
def detalle(cliente_id):
    """Ficha del cliente: datos, deudas, ventas recientes y botón de estado de cuenta."""
    c = _cliente_o_404(cliente_id)
    deudas = query("SELECT * FROM v_deudas WHERE cliente_id = ? AND estado != 'anulada' ORDER BY fecha DESC", (cliente_id,))
    ventas = query("SELECT * FROM ventas WHERE cliente_id = ? ORDER BY id DESC LIMIT 10", (cliente_id,))
    presupuestos = query(
        """SELECT id, actualizado, tipo_pago, total, venta_id FROM presupuestos
            WHERE cliente_id = ? AND vendedor_id = ? ORDER BY actualizado DESC LIMIT 20""",
        (cliente_id, g.user["id"]),
    ) if g.user["rol"] == "vendedor" else []
    return render_template("clientes/detalle.html", c=c, deudas=deudas, ventas=ventas,
                           saldo=saldo_cliente_usd(cliente_id), presupuestos=presupuestos)


@bp.route("/<int:cliente_id>/editar", methods=["GET", "POST"])
@login_required
def editar(cliente_id):
    """Edita los datos del cliente. Solo el admin puede reasignarlo a otro vendedor."""
    c = _cliente_o_404(cliente_id)
    vendedores = query("SELECT id, nombre FROM usuarios WHERE rol='vendedor' AND activo=1 ORDER BY nombre")
    if request.method == "POST":
        datos, error = _leer_form(request.form)
        if error:
            aviso(error, "error")
            return render_template("clientes/form.html", c=request.form, vendedores=vendedores, nuevo=False,
                                   cid=cliente_id, documento_tipo=request.form.get("documento_tipo", "V"),
                                   documento_numero=request.form.get("documento_numero", ""))
        db = get_db()
        vendedor_id = c["vendedor_id"]
        if g.user["rol"] == "admin" and to_int(request.form.get("vendedor_id")):
            vendedor_id = to_int(request.form.get("vendedor_id"))
        # Un vendedor no puede alterar el plazo de crédito: se conserva el que ya tenía el cliente.
        dias = datos["dias_credito"] if datos["dias_credito"] is not None else c["dias_credito"]
        with db:
            db.execute(
                """UPDATE clientes SET nombre=?, documento=?, telefono=?, direccion=?, tipo_pago=?,
                   dias_credito=?, limite_credito_usd=?, vendedor_id=? WHERE id=?""",
                (datos["nombre"], datos["documento"], datos["telefono"], datos["direccion"], datos["tipo_pago"],
                 dias, datos["limite"], vendedor_id, cliente_id))
            if vendedor_id != c["vendedor_id"]:
                db.execute("UPDATE ventas SET vendedor_id=? WHERE cliente_id=?", (vendedor_id, cliente_id))
                db.execute("UPDATE deudas SET vendedor_id=? WHERE cliente_id=?", (vendedor_id, cliente_id))
        if vendedor_id != c["vendedor_id"]:
            audit("cliente_traspasado", f"{datos['nombre']}: vendedor #{c['vendedor_id']} -> #{vendedor_id}")
        if dias != c["dias_credito"]:
            audit("cliente_plazo_credito", f"{datos['nombre']}: {c['dias_credito']} -> {dias} días (0 = automático)")
        audit("cliente_editado", datos["nombre"])
        aviso("Cliente actualizado.")
        return redirect(url_for("clientes.detalle", cliente_id=cliente_id))
    coincidencia = re.fullmatch(r"\s*([JVE])\s*-\s*([0-9]+)\s*", c["documento"] or "", re.IGNORECASE)
    documento_tipo = coincidencia.group(1).upper() if coincidencia else "V"
    documento_numero = coincidencia.group(2) if coincidencia else ""
    return render_template("clientes/form.html", c=c, vendedores=vendedores, nuevo=False, cid=cliente_id,
                           documento_tipo=documento_tipo, documento_numero=documento_numero)


COLORES_CLIENTE = ("verde", "amarillo", "rojo")


@bp.route("/<int:cliente_id>/color", methods=["POST"])
@roles_required("admin")
def color(cliente_id):
    """Solo el admin marca al cliente como verde, amarillo o rojo (o le quita la marca)."""
    c = _cliente_o_404(cliente_id)
    nuevo = request.form.get("color", "")
    if nuevo not in COLORES_CLIENTE + ("",):
        abort(400, "Color inválido.")
    db = get_db()
    db.execute("UPDATE clientes SET color = ? WHERE id = ?", (nuevo or None, cliente_id))
    db.commit()
    audit("cliente_color", f"{c['nombre']}: {c['color'] or 'sin color'} -> {nuevo or 'sin color'}")
    aviso("Color del cliente actualizado." if nuevo else "Se quitó el color del cliente.")
    return redirect(url_for("clientes.detalle", cliente_id=cliente_id))


@bp.route("/<int:cliente_id>/estado-cuenta.pdf")
@login_required
def estado_cuenta_pdf(cliente_id):
    """Descarga el estado de cuenta del cliente en PDF."""
    c = _cliente_o_404(cliente_id)
    nombre = nombre_pdf_cliente(c["nombre"], "Estado de cuenta")
    return responder_pdf(lambda escala: pdf.estado_cuenta(cliente_id, escala), nombre)


@bp.route("/<int:cliente_id>/desactivar", methods=["POST"])
@login_required
def desactivar(cliente_id):
    """Oculta al cliente de la cartera (no se borra para conservar el historial). Con deuda viva no se permite."""
    c = _cliente_o_404(cliente_id)
    if saldo_cliente_usd(cliente_id) > 0.005:
        aviso("No puedes quitar un cliente que todavía tiene deuda.", "error")
        return redirect(url_for("clientes.detalle", cliente_id=cliente_id))
    db = get_db()
    db.execute("UPDATE clientes SET activo = 0 WHERE id = ?", (cliente_id,))
    db.commit()
    audit("cliente_desactivado", c["nombre"])
    aviso("Cliente quitado de la cartera.")
    return redirect(url_for("clientes.lista"))
