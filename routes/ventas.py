"""Ventas: los vendedores las generan; admin y especiales aprueban, rechazan y anulan."""
from io import BytesIO

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, session, url_for

import pdf
from pdf_responses import nombre_pdf_cliente, responder_pdf
import services
from db import query
from utils import (aviso, es_especial, especial_o_admin_required, login_required, parse_decimal_input,
                   roles_required, to_float, to_int)

bp = Blueprint("ventas", __name__, url_prefix="/ventas")


def _venta_o_404(venta_id):
    """Devuelve la venta visible: admin todas, vendedor las suyas y especial pendientes/aprobadas."""
    v = query(
        """SELECT v.*, c.nombre AS cliente, c.documento, c.telefono, u.nombre AS vendedor,
                  a.nombre AS aprobador
             FROM ventas v JOIN clientes c ON c.id = v.cliente_id JOIN usuarios u ON u.id = v.vendedor_id
             LEFT JOIN usuarios a ON a.id = v.aprobado_por WHERE v.id = ?""", (venta_id,), one=True)
    if v is None or (g.user["rol"] == "vendedor" and v["vendedor_id"] != g.user["id"]
                     and not (es_especial() and v["estado"] in ("pendiente", "aprobada"))):
        abort(404)
    return v


@bp.route("/")
@login_required
def lista():
    """Lista ventas propias y, para el vendedor especial, todas las pendientes y aprobadas."""
    estado = request.args.get("estado", "")
    q = request.args.get("q", "").strip()
    vendedor = to_int(request.args.get("vendedor"))
    sql = """SELECT v.*, c.nombre AS cliente, u.nombre AS vendedor,
                    ar.estado AS armado_estado, (v.id > ?) AS req_armado
               FROM ventas v
               JOIN clientes c ON c.id = v.cliente_id JOIN usuarios u ON u.id = v.vendedor_id
               LEFT JOIN armados ar ON ar.venta_id = v.id WHERE 1=1"""
    args = [services.armado_desde_venta()]
    if g.user["rol"] == "vendedor":
        if es_especial():
            sql += " AND (v.vendedor_id = ? OR v.estado IN ('pendiente', 'aprobada'))"; args.append(g.user["id"])
        else:
            sql += " AND v.vendedor_id = ?"; args.append(g.user["id"])
    elif vendedor:
        sql += " AND v.vendedor_id = ?"; args.append(vendedor)
    if estado in ("pendiente", "aprobada", "rechazada", "anulada"):
        sql += " AND v.estado = ?"; args.append(estado)
    if q:
        sql += " AND (c.nombre LIKE ? OR v.numero LIKE ?)"; args += [f"%{q}%"] * 2
    sql += " ORDER BY v.id DESC LIMIT 200"
    vendedores = query("SELECT id, nombre, rol FROM usuarios ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, nombre", (g.user["id"],)) if g.user["rol"] == "admin" else []
    return render_template("ventas/lista.html", ventas=query(sql, args), estado=estado, q=q,
                           vendedor=vendedor, vendedores=vendedores)


# ============================================================ venta en 3 pasos
# Paso 1: /ventas/nueva     → catálogo estilo tienda; los productos van al carrito.
# Paso 2: /ventas/carrito   → cantidades, precios, cliente y condiciones.
# Paso 3: /ventas/confirmar → resumen final y se genera la solicitud o la venta.
# El carrito vive en la sesión del vendedor; al final services.crear_venta vuelve a validar todo.
CARRITO, ORDEN_CARRITO, BORRADOR = "carrito", "carrito_orden", "venta_borrador"


def _carrito():
    """Carrito actual {producto_id (texto): cantidad}."""
    carrito = session.get(CARRITO, {})
    orden = session.get(ORDEN_CARRITO, list(carrito))
    ids = [str(pid) for pid in orden if str(pid) in carrito]
    ids.extend(pid for pid in carrito if pid not in ids)
    return {pid: carrito[pid] for pid in ids}


def _guardar_carrito(c):
    session[CARRITO] = c
    session[ORDEN_CARRITO] = list(c)
    session.modified = True


def _borrador():
    """Cliente y condiciones elegidos en el paso 2 (se conservan si el vendedor vuelve atrás)."""
    return dict(session.get(BORRADOR, {}))


def _guardar_borrador(b):
    session[BORRADOR] = b
    session.modified = True


def _limpiar_venta():
    session.pop(CARRITO, None)
    session.pop(ORDEN_CARRITO, None)
    session.pop(BORRADOR, None)


def _productos_del_carrito():
    """Productos del carrito con datos frescos. Quita los que ya no están disponibles."""
    cart = _carrito()
    if not cart:
        return []
    ids = [to_int(k) for k in cart]
    filas = {str(r["id"]): r for r in query(
        f"SELECT p.*, c.nombre AS categoria, COALESCE(NULLIF(p.stock_minimo, 0), ?) AS minimo_ef "
        f"FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id "
        f"WHERE p.activo = 1 AND p.id IN ({','.join('?' * len(ids))})", [services.stock_alerta_defecto()] + ids)}
    perdidos = [k for k in cart if k not in filas]
    if perdidos:
        for k in perdidos:
            cart.pop(k)
        _guardar_carrito(cart)
        aviso("Quitamos del carrito productos que ya no están disponibles.", "warn")
    return [(filas[k], cart[k]) for k in cart if k in filas]


@bp.route("/nueva")
@roles_required("vendedor")
def nueva():
    """PASO 1 · Productos: catálogo estilo tienda con buscador; cada producto se añade al carrito."""
    cliente = to_int(request.args.get("cliente"))
    if cliente:   # viene de "Vender a este cliente" o de crear un cliente nuevo
        b = _borrador(); b["cliente_id"] = str(cliente); _guardar_borrador(b)
        if _carrito():
            return redirect(url_for("ventas.carrito"))
    productos = query(
        """SELECT p.*, c.nombre AS categoria, COALESCE(NULLIF(p.stock_minimo, 0), ?) AS minimo_ef
             FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id
            WHERE p.activo = 1 ORDER BY (p.stock <= 0), p.nombre""", (services.stock_alerta_defecto(),))   # los agotados se muestran al final, deshabilitados
    categorias = query("SELECT * FROM categorias WHERE activo = 1 ORDER BY nombre")
    recientes = services.precios_recientes()
    return render_template("ventas/nueva.html", productos=productos, categorias=categorias,
                           carrito=_carrito(), paso=1,
                           recientes=recientes, ids_recientes={r["producto_id"] for r in recientes})


@bp.route("/carrito/agregar", methods=["POST"])
@roles_required("vendedor")
def carrito_agregar():
    """Añade un producto al carrito (responde JSON si lo pide el JavaScript de la tienda)."""
    quiere_json = request.headers.get("X-Requested-With") == "fetch"
    prod = query("SELECT * FROM productos WHERE id = ? AND activo = 1", (to_int(request.form.get("producto_id")),), one=True)
    cant = to_float(request.form.get("cantidad"), 1.0)
    cart = _carrito()
    error = None
    if prod is None:
        error = "Ese producto ya no está disponible."
    elif not cant.is_integer() or cant <= 0:
        error = "La cantidad debe ser un número entero mayor que cero."
    else:
        total = round(cart.get(str(prod["id"]), 0) + cant, 4)
        if total > prod["stock"] + 1e-9:
            error = services.msg_sin_stock(g.user["id"], prod["nombre"], prod["stock"])
        else:
            cart[str(prod["id"])] = total
            _guardar_carrito(cart)
    if quiere_json:
        return jsonify(ok=error is None, error=error, items=len(cart),
                       en_carrito=cart.get(str(prod["id"]), 0) if prod else 0), (200 if error is None else 400)
    aviso(error or "Producto añadido al carrito.", "error" if error else "ok")
    return redirect(request.form.get("volver") or url_for("ventas.nueva"))


@bp.route("/carrito/agregar-varios", methods=["POST"])
@roles_required("vendedor")
def carrito_agregar_varios():
    """Añade al carrito todos los productos marcados en la tabla (campos 'sel' y 'cant_<id>').

    Valida el stock de cada uno; los que fallan se informan y los demás sí se añaden.
    Responde JSON si lo pide el JavaScript de la tabla."""
    quiere_json = request.headers.get("X-Requested-With") == "fetch"
    cart = _carrito()
    errores, agregados, ids = [], 0, []
    for pid in dict.fromkeys(request.form.getlist("sel")):      # sin repetidos, en orden
        prod = query("SELECT * FROM productos WHERE id = ? AND activo = 1", (to_int(pid),), one=True)
        cant = to_float(request.form.get(f"cant_{pid}"), 1.0)
        if prod is None:
            errores.append("Un producto ya no está disponible.")
        elif not cant.is_integer() or cant <= 0:
            errores.append(f"La cantidad de «{prod['nombre']}» debe ser un número entero mayor que cero.")
        else:
            total = round(cart.get(str(prod["id"]), 0) + cant, 4)
            if total > prod["stock"] + 1e-9:
                errores.append(services.msg_sin_stock(g.user["id"], prod["nombre"], prod["stock"]))
            else:
                cart[str(prod["id"])] = total
                agregados += 1
                ids.append(str(prod["id"]))
    if agregados:
        _guardar_carrito(cart)
    if not request.form.getlist("sel"):
        errores.append("Marca al menos un producto de la tabla.")
    if quiere_json:
        return jsonify(ok=not errores, agregados=agregados, ids=ids, errores=errores, items=len(cart), carrito=cart), \
            (200 if not errores else 400)
    for e in errores:
        aviso(e, "error")
    if agregados:
        aviso(f"{agregados} producto(s) añadido(s) al carrito.")
    return redirect(url_for("ventas.nueva"))


@bp.route("/carrito/quitar", methods=["POST"])
@roles_required("vendedor")
def carrito_quitar():
    """Quita un producto del carrito (o lo vacía si se pide)."""
    cart = _carrito()
    if request.form.get("todo"):
        _limpiar_venta()
        aviso("Carrito vaciado.")
        return redirect(url_for("ventas.nueva"))
    cart.pop(str(to_int(request.form.get("producto_id"))), None)
    _guardar_carrito(cart)
    return redirect(url_for("ventas.carrito") if cart else url_for("ventas.nueva"))


@bp.route("/carrito", methods=["GET", "POST"])
@roles_required("vendedor")
def carrito():
    """PASO 2 · Carrito y cliente: cantidades, precios, cliente y condiciones de la venta."""
    if not _carrito():
        aviso("Tu carrito está vacío: elige primero los productos.", "warn")
        return redirect(url_for("ventas.nueva"))
    if request.method == "POST":
        f = request.form
        cart = {}
        for pid, cant_actual in _carrito().items():
            cant = float(to_float(f.get(f"cant_{pid}"), cant_actual))
            if not cant.is_integer() or cant < 0:
                aviso("Las cantidades deben ser números enteros iguales o mayores que cero.", "error")
                return redirect(url_for("ventas.carrito"))
            if cant > 0:
                cart[pid] = cant
        _guardar_carrito(cart)
        precios = {}
        for pid in cart:   # solo se recuerdan los precios que el vendedor cambió a mano
            prod = query("SELECT * FROM productos WHERE id = ?", (to_int(pid),), one=True)
            escrito = f.get(f"precio_{pid}", "").strip()
            if escrito:
                try:
                    precio = parse_decimal_input(escrito, "El precio")
                except ValueError as e:
                    aviso(str(e), "error")
                    return redirect(url_for("ventas.carrito"))
                if prod and abs(precio - services.precio_lista(prod)) > 0.004:
                    precios[pid] = escrito
        _guardar_borrador({
            "cliente_id": f.get("cliente_id", ""),
            "tipo_pago": f.get("tipo_pago", "contado"), "metodo_pago": f.get("metodo_pago", ""),
            "cuota_inicial": f.get("cuota_inicial", ""), "descuento": f.get("descuento", ""),
            "nota": f.get("nota", ""), "precios": precios})
        if not cart:
            aviso("Tu carrito quedó vacío.", "warn")
            return redirect(url_for("ventas.nueva"))
        try:
            services.resumen_venta(g.user["id"], _borrador(), cart)
            return redirect(url_for("ventas.confirmar"))
        except ValueError as e:
            aviso(str(e), "error")
            return redirect(url_for("ventas.carrito"))
    clientes = query("SELECT * FROM clientes WHERE vendedor_id = ? AND activo = 1 ORDER BY nombre", (g.user["id"],))
    return render_template("ventas/carrito.html", lineas=_productos_del_carrito(), clientes=clientes,
                           b=_borrador(), paso=2)


@bp.route("/confirmar", methods=["GET", "POST"])
@roles_required("vendedor")
def confirmar():
    """PASO 3 · Confirmación: resumen final; al confirmar se genera la solicitud (crédito) o la venta (contado)."""
    cart, b = _carrito(), _borrador()
    if not cart:
        return redirect(url_for("ventas.nueva"))
    if not b.get("cliente_id"):
        return redirect(url_for("ventas.carrito"))
    if request.method == "POST":
        items = [{"producto_id": pid, "cantidad": cant, "precio": (b.get("precios") or {}).get(pid, "")}
                 for pid, cant in cart.items()]
        try:
            venta_id = services.crear_venta(g.user["id"], b, items)
        except ValueError as e:
            aviso(str(e), "error")
            return redirect(url_for("ventas.carrito"))
        _limpiar_venta()
        v = query("SELECT estado FROM ventas WHERE id = ?", (venta_id,), one=True)
        aviso("Venta registrada y cobrada." if v["estado"] == "aprobada"
              else "Venta enviada al administrador para su aprobación.")
        return redirect(url_for("ventas.detalle", venta_id=venta_id))
    try:
        r = services.resumen_venta(g.user["id"], b, cart)
    except ValueError as e:
        aviso(str(e), "error")
        return redirect(url_for("ventas.carrito"))
    return render_template("ventas/confirmar.html", r=r, paso=3)


@bp.route("/<int:venta_id>")
@login_required
def detalle(venta_id):
    """Detalle de la venta: líneas, precios modificados, stock disponible y botones de aprobación (admin)."""
    v = _venta_o_404(venta_id)
    lineas = query(
        """SELECT d.*, p.codigo, p.nombre, p.unidad, p.stock FROM venta_detalles d
             JOIN productos p ON p.id = d.producto_id WHERE d.venta_id = ? ORDER BY d.id""", (venta_id,))
    deuda = query("SELECT * FROM v_deudas WHERE venta_id = ?", (venta_id,), one=True)
    armado = query(
        """SELECT a.*, ua.nombre AS asignado_nombre, ub.nombre AS armador_nombre FROM armados a
             LEFT JOIN usuarios ua ON ua.id = a.asignado_a LEFT JOIN usuarios ub ON ub.id = a.armado_por
            WHERE a.venta_id = ?""", (venta_id,), one=True)
    return render_template("ventas/detalle.html", v=v, lineas=lineas, deuda=deuda, armado=armado,
                           req_armado=venta_id > services.armado_desde_venta())


@bp.route("/<int:venta_id>/aprobar", methods=["POST"])
@especial_o_admin_required
def aprobar(venta_id):
    """El administrador o un vendedor especial aprueba una venta pendiente."""
    venta = _venta_o_404(venta_id)
    try:
        services.aprobar_venta(venta_id, g.user["id"])
        aviso("Venta aprobada: stock descontado y deuda activada.")
    except ValueError as e:
        aviso(str(e), "error")
        return redirect(url_for("ventas.detalle", venta_id=venta_id))
    # Opcional: quién arma el pedido ('' = decidir después, 'cualquiera' o el id de un vendedor especial).
    armador = request.form.get("armador", "").strip() if g.user["rol"] == "admin" or es_especial() else ""
    if armador:
        try:
            services.asignar_armado(venta_id, armador, g.user["id"])
            aviso("Pedido enviado a armar.")
        except ValueError as e:
            aviso(str(e), "error")
    if g.user["rol"] == "vendedor" and venta["vendedor_id"] != g.user["id"]:
        return redirect(url_for("ventas.lista", estado="pendiente"))
    return redirect(url_for("ventas.detalle", venta_id=venta_id))


@bp.route("/<int:venta_id>/rechazar", methods=["POST"])
@especial_o_admin_required
def rechazar(venta_id):
    """El administrador o un vendedor especial rechaza una venta pendiente con un motivo."""
    venta = _venta_o_404(venta_id)
    try:
        services.rechazar_venta(venta_id, g.user["id"], request.form.get("motivo", "").strip())
        aviso("Venta rechazada.")
    except ValueError as e:
        aviso(str(e), "error")
        if g.user["rol"] == "vendedor" and venta["vendedor_id"] != g.user["id"]:
            return redirect(url_for("ventas.lista", estado="pendiente"))
        return redirect(url_for("ventas.detalle", venta_id=venta_id))
    if g.user["rol"] == "vendedor" and venta["vendedor_id"] != g.user["id"]:
        return redirect(url_for("ventas.lista", estado="pendiente"))
    return redirect(url_for("ventas.detalle", venta_id=venta_id))


@bp.route("/<int:venta_id>/anular", methods=["POST"])
@especial_o_admin_required
def anular(venta_id):
    """El administrador o un vendedor especial anula una venta aprobada."""
    venta = _venta_o_404(venta_id)
    try:
        services.anular_venta(venta_id, g.user["id"], request.form.get("motivo", "").strip())
        aviso("Venta anulada y stock devuelto al almacén.")
    except ValueError as e:
        aviso(str(e), "error")
        if g.user["rol"] == "vendedor" and venta["vendedor_id"] != g.user["id"]:
            return redirect(url_for("ventas.lista"))
        return redirect(url_for("ventas.detalle", venta_id=venta_id))
    if g.user["rol"] == "vendedor" and venta["vendedor_id"] != g.user["id"]:
        return redirect(url_for("ventas.lista"))
    return redirect(url_for("ventas.detalle", venta_id=venta_id))


@bp.route("/<int:venta_id>/nota.pdf")
@login_required
def nota_pdf(venta_id):
    """Nota de venta en PDF."""
    v = _venta_o_404(venta_id)
    nombre = nombre_pdf_cliente(v["cliente"], f"Venta {v['numero']}")
    return responder_pdf(BytesIO(pdf.nota_venta(venta_id)), nombre)
