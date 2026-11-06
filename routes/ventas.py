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
    """Devuelve la venta visible según el vendedor actual del cliente."""
    v = query(
        """SELECT v.*, c.vendedor_id AS cartera_vendedor_id, c.nombre AS cliente, c.documento, c.telefono,
                  u.nombre AS vendedor, a.nombre AS aprobador
             FROM ventas v JOIN clientes c ON c.id = v.cliente_id JOIN usuarios u ON u.id = c.vendedor_id
             LEFT JOIN usuarios a ON a.id = v.aprobado_por WHERE v.id = ?""", (venta_id,), one=True)
    if v is None or (g.user["rol"] == "vendedor" and v["cartera_vendedor_id"] != g.user["id"]
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
               JOIN clientes c ON c.id = v.cliente_id JOIN usuarios u ON u.id = c.vendedor_id
               LEFT JOIN armados ar ON ar.venta_id = v.id WHERE 1=1"""
    args = [services.armado_desde_venta()]
    if g.user["rol"] == "vendedor":
        if es_especial():
            sql += " AND (c.vendedor_id = ? OR v.estado IN ('pendiente', 'aprobada'))"; args.append(g.user["id"])
        else:
            sql += " AND c.vendedor_id = ?"; args.append(g.user["id"])
    elif vendedor:
        sql += " AND c.vendedor_id = ?"; args.append(vendedor)
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
PRESUPUESTO_ORIGEN = "presupuesto_origen"


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
    session.pop(PRESUPUESTO_ORIGEN, None)


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
        if cart:
            if quiere_json:
                return jsonify(ok=True, agregados=0, ids=[], errores=[], items=len(cart), carrito=cart)
            return redirect(url_for("ventas.carrito"))
        errores.append("Marca al menos un producto de la tabla.")
    if quiere_json:
        return jsonify(ok=not errores, agregados=agregados, ids=ids, errores=errores, items=len(cart), carrito=cart), \
            (200 if not errores else 400)
    for e in errores:
        aviso(e, "error")
    if agregados:
        aviso(f"{agregados} producto(s) añadido(s) al carrito.")
        return redirect(url_for("ventas.carrito"))
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
            "tipo_pago": f.get("tipo_pago", "credito"), "metodo_pago": f.get("metodo_pago", ""),
            "cuota_inicial": f.get("cuota_inicial", ""),
            "descuento_porcentaje": f.get("descuento_porcentaje", ""),
            "nota": f.get("nota", ""), "precios": precios})
        if not cart:
            aviso("Tu carrito quedó vacío.", "warn")
            return redirect(url_for("ventas.nueva"))
        if f.get("accion") == "presupuesto":
            return redirect(url_for("ventas.presupuesto_pdf"))
        if f.get("accion") == "guardar_presupuesto":
            try:
                presupuesto_id = services.guardar_presupuesto(g.user["id"], _borrador(), cart)
            except ValueError as e:
                aviso(str(e), "error")
                return redirect(url_for("ventas.carrito"))
            aviso(f"Presupuesto #{presupuesto_id} guardado en tu cartera y en la ficha del cliente.")
            return redirect(url_for("ventas.presupuestos"))
        try:
            services.resumen_venta(g.user["id"], _borrador(), cart)
            return redirect(url_for("ventas.confirmar"))
        except ValueError as e:
            aviso(str(e), "error")
            return redirect(url_for("ventas.carrito"))
    clientes = query("SELECT * FROM clientes WHERE vendedor_id = ? AND activo = 1 ORDER BY nombre", (g.user["id"],))
    return render_template("ventas/carrito.html", lineas=_productos_del_carrito(), clientes=clientes,
                           b=_borrador(), paso=2, presupuesto_origen=session.get(PRESUPUESTO_ORIGEN))


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
        presupuesto_id = to_int(session.get(PRESUPUESTO_ORIGEN))
        if presupuesto_id:
            try:
                services.marcar_presupuesto_convertido(
                    presupuesto_id, g.user["id"], venta_id,
                    cuota_inicial=to_float(b.get("cuota_inicial")),
                )
            except ValueError as e:
                aviso(f"La venta se creó, pero no se pudo actualizar su presupuesto: {e}", "error")
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


@bp.route("/presupuesto.pdf")
@roles_required("vendedor")
def presupuesto_pdf():
    """Genera un presupuesto temporal con los datos de venta, sin modificar inventario."""
    cart = _carrito()
    if not cart:
        aviso("Tu carrito está vacío: elige primero los productos.", "warn")
        return redirect(url_for("ventas.nueva"))
    try:
        r = services.resumen_presupuesto(g.user["id"], _borrador(), cart)
    except ValueError as e:
        aviso(str(e), "error")
        return redirect(url_for("ventas.carrito"))
    nombre = nombre_pdf_cliente(r["cliente"]["nombre"], "Presupuesto")
    return responder_pdf(lambda escala: pdf.presupuesto(r, g.user["nombre"], escala), nombre)


@bp.route("/presupuestos")
@roles_required("vendedor")
def presupuestos():
    """Presupuestos guardados por el vendedor, incluidos los ya convertidos."""
    filas = query(
        """SELECT p.*, c.nombre AS cliente
             FROM presupuestos p JOIN clientes c ON c.id = p.cliente_id
            WHERE p.vendedor_id = ? ORDER BY p.actualizado DESC, p.id DESC""",
        (g.user["id"],),
    )
    return render_template("ventas/presupuestos.html", presupuestos=filas)


@bp.route("/presupuestos/<int:presupuesto_id>/editar", methods=["GET", "POST"])
@roles_required("vendedor")
def editar_presupuesto(presupuesto_id):
    """Edita el cliente, productos y condiciones de un presupuesto aún abierto."""
    guardado = services.presupuesto_vendedor(presupuesto_id, g.user["id"])
    if guardado is None or guardado["presupuesto"]["venta_id"] is not None:
        abort(404)
    p = guardado["presupuesto"]
    if request.method == "POST":
        ids = request.form.getlist("producto_id")
        cantidades = request.form.getlist("cantidad")
        precios = request.form.getlist("precio")
        if not ids or len(ids) != len(cantidades) or len(ids) != len(precios):
            aviso("El presupuesto debe conservar al menos un producto válido.", "error")
            return redirect(url_for("ventas.editar_presupuesto", presupuesto_id=presupuesto_id))
        items = [
            {"producto_id": pid, "cantidad": cant, "precio": precio}
            for pid, cant, precio in zip(ids, cantidades, precios)
        ]
        borrador = {
            "cliente_id": request.form.get("cliente_id", ""),
            "tipo_pago": request.form.get("tipo_pago", "contado"),
            "metodo_pago": request.form.get("metodo_pago", ""),
            "cuota_inicial": request.form.get("cuota_inicial", ""),
            "descuento_porcentaje": request.form.get("descuento_porcentaje", ""),
            "nota": request.form.get("nota", ""),
        }
        carrito = {str(item["producto_id"]): item["cantidad"] for item in items}
        borrador["precios"] = {str(item["producto_id"]): item["precio"] for item in items}
        try:
            services.guardar_presupuesto(
                g.user["id"], borrador, carrito, presupuesto_id=presupuesto_id
            )
        except ValueError as e:
            aviso(str(e), "error")
            return redirect(url_for("ventas.editar_presupuesto", presupuesto_id=presupuesto_id))
        aviso(f"Presupuesto #{presupuesto_id} actualizado.")
        return redirect(url_for("ventas.presupuestos"))

    clientes = query(
        """SELECT id, nombre FROM clientes
            WHERE vendedor_id = ? AND (activo = 1 OR id = ?) ORDER BY nombre""",
        (g.user["id"], p["cliente_id"]),
    )
    productos = query(
        "SELECT id, codigo, nombre, unidad, precio_mayor_usd FROM productos WHERE activo = 1 ORDER BY nombre"
    )
    return render_template(
        "ventas/presupuesto_editar.html", p=p, lineas=guardado["lineas"],
        clientes=clientes, productos=productos,
    )


@bp.route("/presupuestos/<int:presupuesto_id>/eliminar", methods=["POST"])
@roles_required("vendedor")
def eliminar_presupuesto(presupuesto_id):
    """Elimina definitivamente un presupuesto que todavía no se convirtió en venta."""
    try:
        services.eliminar_presupuesto(presupuesto_id, g.user["id"])
        aviso("Presupuesto eliminado.")
    except ValueError as e:
        aviso(str(e), "error")
    return redirect(url_for("ventas.presupuestos"))


@bp.route("/presupuestos/<int:presupuesto_id>/convertir", methods=["POST"])
@roles_required("vendedor")
def convertir_presupuesto(presupuesto_id):
    """Carga el presupuesto en el carrito para completar condiciones antes de crear la venta."""
    guardado = services.presupuesto_vendedor(presupuesto_id, g.user["id"])
    if guardado is None or guardado["presupuesto"]["venta_id"] is not None:
        abort(404)
    p = guardado["presupuesto"]
    carrito = {str(l["producto_id"]): l["cantidad"] for l in guardado["lineas"]}
    precios = {str(l["producto_id"]): str(l["precio_unit"]) for l in guardado["lineas"]}
    session[CARRITO] = carrito
    session[ORDEN_CARRITO] = list(carrito)
    session[BORRADOR] = {
        "cliente_id": str(p["cliente_id"]), "tipo_pago": p["tipo_pago"],
        "metodo_pago": p["metodo_pago"] or "", "cuota_inicial": str(p["cuota_inicial"]),
        "descuento_porcentaje": str(p["descuento_porcentaje"]),
        "nota": p["nota"] or "", "precios": precios,
    }
    session[PRESUPUESTO_ORIGEN] = presupuesto_id
    session.modified = True
    aviso("Revisa el cliente, productos y condiciones de pago. La compra se crea solo al confirmarla.")
    return redirect(url_for("ventas.carrito"))


@bp.route("/presupuestos/<int:presupuesto_id>/pdf")
@roles_required("vendedor")
def presupuesto_guardado_pdf(presupuesto_id):
    """Descarga el PDF de un presupuesto guardado del vendedor."""
    resumen = services.resumen_presupuesto_guardado(presupuesto_id, g.user["id"])
    if resumen is None:
        abort(404)
    nombre = nombre_pdf_cliente(resumen["cliente"]["nombre"], f"Presupuesto {presupuesto_id}")
    return responder_pdf(lambda escala: pdf.presupuesto(resumen, g.user["nombre"], escala), nombre)


@bp.route("/<int:venta_id>/editar", methods=["GET", "POST"])
@login_required
def editar(venta_id):
    """Permite editar pedidos propios pendientes o cualquier venta pendiente/aprobada como admin."""
    venta = _venta_o_404(venta_id)
    admin = g.user["rol"] == "admin"
    if admin:
        if venta["estado"] not in ("pendiente", "aprobada"):
            abort(404)
    elif venta["vendedor_id"] != g.user["id"] or venta["estado"] != "pendiente":
        abort(404)

    if request.method == "POST":
        producto_ids = request.form.getlist("producto_id")
        cantidades = request.form.getlist("cantidad")
        precios = request.form.getlist("precio")
        if not producto_ids or len(producto_ids) != len(cantidades) or len(producto_ids) != len(precios):
            aviso("El pedido debe tener al menos un producto válido.", "error")
            return redirect(url_for("ventas.editar", venta_id=venta_id))
        items = [
            {"producto_id": pid, "cantidad": cantidad, "precio": precio}
            for pid, cantidad, precio in zip(producto_ids, cantidades, precios)
        ]
        try:
            services.editar_venta_pendiente(
                venta_id, g.user["id"], items,
                request.form.get("descuento_porcentaje", ""), request.form.get("nota", ""),
                admin=admin,
                fecha_compra=request.form.get("fecha_compra") if admin else None,
            )
        except ValueError as e:
            aviso(str(e), "error")
            return redirect(url_for("ventas.editar", venta_id=venta_id))
        if admin and venta["estado"] == "aprobada":
            aviso("Venta aprobada actualizada. Stock y deuda se ajustaron según los cambios.")
        else:
            aviso("Pedido actualizado. Los cambios se aplicarán al aprobarlo.")
        return redirect(url_for("ventas.detalle", venta_id=venta_id))

    lineas = query(
        """SELECT d.*, p.codigo, p.nombre, p.unidad FROM venta_detalles d
             JOIN productos p ON p.id = d.producto_id WHERE d.venta_id = ? ORDER BY d.id""",
        (venta_id,),
    )
    productos = query(
        "SELECT id, codigo, nombre, unidad, precio_mayor_usd FROM productos WHERE activo = 1 ORDER BY nombre"
    )
    return render_template("ventas/editar.html", v=venta, lineas=lineas,
                           productos=productos, paso=2, admin_edit=admin)


@bp.route("/<int:venta_id>/armado/confirmar-admin", methods=["POST"])
@login_required
def confirmar_armado_admin(venta_id):
    """Permite al administrador confirmar el armado sin depender de una asignación."""
    if g.user["rol"] != "admin":
        abort(403)
    _venta_o_404(venta_id)
    try:
        services.admin_confirmar_armado(venta_id, g.user["id"])
        aviso("Pedido marcado como armado por el administrador.")
    except ValueError as e:
        aviso(str(e), "error")
    return redirect(url_for("ventas.detalle", venta_id=venta_id))


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
    return responder_pdf(lambda escala: pdf.nota_venta(venta_id, escala), nombre)
