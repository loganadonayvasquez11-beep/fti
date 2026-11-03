"""Catálogo y almacén central: productos, categorías, movimientos y kardex.

Solo el admin crea y edita productos, precios y stock; vendedores especiales pueden descargar la lista
de mercancía. Cada cambio de precio queda en una lista temporal de 48 horas.
"""
from flask import Blueprint, current_app, g, redirect, render_template, request, send_file, url_for

from io import BytesIO

import pdf
from db import get_db, query
from services import (audit, precios_recientes, registrar_cambio_precio, registrar_movimiento_manual,
                      stock_alerta_defecto)
from utils import (ahora, aviso, especial_o_admin_required, login_required, roles_required, to_float,
                   parse_decimal_input, to_int)
from werkzeug.utils import secure_filename
from pathlib import Path
from datetime import datetime, timedelta
import uuid

bp = Blueprint("catalogo", __name__)


@bp.route("/productos")
@login_required
def productos():
    """Lista de productos con filtros por texto, categoría y stock bajo."""
    q = request.args.get("q", "").strip()
    cat = to_int(request.args.get("categoria"))
    bajo = request.args.get("bajo") == "1"
    ver_inactivos = request.args.get("inactivos") == "1" and g.user["rol"] == "admin"
    defecto = stock_alerta_defecto()
    # minimo_ef = mínimo propio del producto o, si no tiene, la alerta general de Configuración
    sql = """SELECT p.*, c.nombre AS categoria, COALESCE(NULLIF(p.stock_minimo, 0), ?) AS minimo_ef
               FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id WHERE 1=1"""
    args = [defecto]
    if not ver_inactivos:
        sql += " AND p.activo = 1"
    if q:
        sql += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR c.nombre LIKE ?)"; args += [f"%{q}%"] * 3
    if cat:
        sql += " AND p.categoria_id = ?"; args.append(cat)
    if bajo:
        sql += " AND p.stock <= COALESCE(NULLIF(p.stock_minimo, 0), ?)"; args.append(defecto)
    sql += " ORDER BY p.nombre"
    recientes = precios_recientes()
    return render_template("productos/lista.html", productos=query(sql, args),
                           categorias=query("SELECT * FROM categorias WHERE activo=1 ORDER BY nombre"),
                           q=q, cat=cat, bajo=bajo, inactivos=ver_inactivos,
                           recientes=recientes, ids_recientes={r["producto_id"] for r in recientes})


@bp.route("/productos/lista.pdf")
@especial_o_admin_required
def productos_pdf():
    """PDF en lista con toda la mercancía activa: SIN precios ni cantidades en stock.

    Lo pueden descargar el admin y los vendedores especiales."""
    filas = query("""SELECT p.codigo, p.nombre, p.unidad, c.nombre AS categoria, p.precio_mayor_usd
                       FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id
                      WHERE p.activo = 1 ORDER BY c.nombre IS NULL, c.nombre, p.nombre""")
    audit("lista_stock_pdf", f"{g.user['usuario']} ({len(filas)} productos)")
    return send_file(BytesIO(pdf.lista_mercancia(filas)), mimetype="application/pdf",
                     as_attachment=True, download_name=f"lista_mercancia_{ahora()[:10]}.pdf")


def _leer_producto(f):
    """Valida el formulario de producto. Devuelve (datos, error)."""
    codigo, nombre = f.get("codigo", "").strip().upper(), f.get("nombre", "").strip()
    if not codigo:
        return None, "El código del producto es obligatorio: escríbelo tú mismo."
    if not nombre:
        return None, "El nombre del producto es obligatorio."
    if not to_int(f.get("categoria_id")):
        return None, "Selecciona la categoría del producto."
    try:
        mayor = parse_decimal_input(f.get("precio_mayor_usd"), "El precio al mayor")
    except ValueError as e:
        return None, str(e)
    if mayor < 0:
        return None, "El precio no puede ser negativo."
    return {
        "codigo": codigo, "nombre": nombre, "categoria_id": to_int(f.get("categoria_id")) or None,
        "unidad": f.get("unidad", "und").strip() or "und", "mayor": mayor,
        "minimo": max(to_float(f.get("stock_minimo")), 0),
    }, None


def _codigo_repetido(db, codigo, excluir_id=None):
    """True si otro producto ya usa ese código (el código lo escribe el usuario, debe ser único)."""
    fila = db.execute("SELECT id FROM productos WHERE codigo = ?", (codigo,)).fetchone()
    return bool(fila) and fila["id"] != excluir_id


def _guardar_imagen(archivo, imagen_actual=None):
    """Guarda imágenes de producto con nombre aleatorio y extensiones permitidas."""
    if not archivo or not archivo.filename:
        return imagen_actual, None
    ext = Path(archivo.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return imagen_actual, "Formato de imagen no permitido. Usa JPG, PNG, WEBP o GIF."
    carpeta = Path(current_app.static_folder) / "uploads" / "productos"
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"{uuid.uuid4().hex}{ext}"
    archivo.save(carpeta / nombre)
    return f"uploads/productos/{nombre}", None


@bp.route("/productos/nuevo", methods=["GET", "POST"])
@roles_required("admin")
def producto_nuevo():
    """Crea un producto en el almacén central (con stock inicial opcional que queda en el kardex).

    Solo puede hacerlo el administrador."""
    cats = query("SELECT * FROM categorias WHERE activo=1 ORDER BY nombre")
    if request.method == "POST":
        d, error = _leer_producto(request.form)
        if d and g.user["rol"] != "admin":
            d["minimo"] = 0   # la alerta mínima de stock es cosa del admin: el especial deja la general
        db = get_db()
        if not error and _codigo_repetido(db, d["codigo"]):
            error = f"El código {d['codigo']} ya está asignado a otro producto."
        imagen, error_img = _guardar_imagen(request.files.get("imagen"))
        error = error or error_img
        if error:
            aviso(error, "error")
            return render_template("productos/form.html", p=request.form, categorias=cats, nuevo=True)
        with db:
            cur = db.execute(
                """INSERT INTO productos (codigo, nombre, imagen, categoria_id, unidad, precio_mayor_usd,
                   stock_minimo, creado) VALUES (?,?,?,?,?,?,?,?)""",
                (d["codigo"], d["nombre"], imagen, d["categoria_id"], d["unidad"], d["mayor"],
                 d["minimo"], ahora()))
        producto_id = cur.lastrowid
        inicial = to_float(request.form.get("stock_inicial"))
        if inicial > 0:
            registrar_movimiento_manual(producto_id, "entrada", inicial, "Stock inicial", g.user["id"])
        audit("producto_creado", f"{d['codigo']} por {g.user['usuario']}")
        aviso("Producto creado.")
        return redirect(url_for("catalogo.productos"))
    return render_template("productos/form.html", p={"unidad": "und"}, categorias=cats, nuevo=True)


@bp.route("/productos/<int:pid>/editar", methods=["GET", "POST"])
@roles_required("admin")
def producto_editar(pid):
    """Edita datos y precios de un producto (el stock solo cambia con movimientos).

    Solo puede hacerlo el administrador. Los precios quedan iguales en todo el sistema y, si cambian,
    el producto entra 48 horas en la lista de "precios actualizados"."""
    p = query("SELECT * FROM productos WHERE id = ?", (pid,), one=True)
    if p is None:
        return redirect(url_for("catalogo.productos"))
    cats = query("SELECT * FROM categorias WHERE activo=1 ORDER BY nombre")
    if request.method == "POST":
        d, error = _leer_producto(request.form)
        # El código es manual y editable (las ventas y el kardex se enlazan por id, no por código); debe seguir siendo único.
        if not error and _codigo_repetido(get_db(), d["codigo"], pid):
            error = f"El código {d['codigo']} ya está asignado a otro producto."
        imagen, error_img = _guardar_imagen(request.files.get("imagen"), p["imagen"])
        if request.form.get("quitar_imagen"):
            imagen = None
        error = error or error_img
        if error:
            aviso(error, "error")
            return render_template("productos/form.html", p=request.form, categorias=cats, nuevo=False, pid=pid)
        db = get_db()
        es_admin_ = g.user["rol"] == "admin"
        # Solo el admin cambia la alerta mínima y activa/desactiva; el especial conserva lo que ya había.
        minimo = d["minimo"] if es_admin_ else p["stock_minimo"]
        activo = (1 if request.form.get("activo") else 0) if es_admin_ else p["activo"]
        db.execute(
            """UPDATE productos SET codigo=?, nombre=?, imagen=?, categoria_id=?, unidad=?,
               precio_mayor_usd=?, stock_minimo=?, activo=? WHERE id=?""",
            (d["codigo"], d["nombre"], imagen, d["categoria_id"], d["unidad"], d["mayor"], minimo, activo, pid))
        # Si cambió algún precio, se anota para la lista temporal de 48 horas.
        cambio = registrar_cambio_precio(db, pid, g.user["id"], p["precio_mayor_usd"], d["mayor"])
        db.commit()
        audit("producto_editado", f"{d['codigo']} por {g.user['usuario']}; mayor {p['precio_mayor_usd']}→{d['mayor']}"
                                  + ("; precio actualizado 48 h" if cambio else ""))
        aviso("Producto actualizado.")
        return redirect(url_for("catalogo.productos"))
    return render_template("productos/form.html", p=p, categorias=cats, nuevo=False, pid=pid)


@bp.route("/categorias", methods=["GET", "POST"])
@roles_required("admin")
def categorias():
    """Lista y crea categorías del catálogo (solo nombre; el código lo escribe el admin en cada producto)."""
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        if not nombre:
            aviso("Escribe un nombre para la categoría.", "error")
        elif query("SELECT 1 FROM categorias WHERE nombre = ? COLLATE NOCASE", (nombre,), one=True):
            aviso("Esa categoría ya existe.", "error")
        else:
            db = get_db()
            db.execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre,))
            db.commit()
            audit("categoria_creada", nombre)
            aviso("Categoría creada.")
        return redirect(url_for("catalogo.categorias"))
    lista = query("""SELECT c.*, (SELECT COUNT(*) FROM productos p WHERE p.categoria_id = c.id AND p.activo=1) AS n
                       FROM categorias c ORDER BY c.nombre""")
    return render_template("productos/categorias.html", categorias=lista)


@bp.route("/categorias/<int:cid>/estado", methods=["POST"])
@roles_required("admin")
def categoria_estado(cid):
    """Activa o desactiva una categoría."""
    db = get_db()
    db.execute("UPDATE categorias SET activo = 1 - activo WHERE id = ?", (cid,))
    db.commit()
    audit("categoria_estado", f"id {cid}")
    return redirect(url_for("catalogo.categorias"))


@bp.route("/inventario/movimiento", methods=["GET", "POST"])
@roles_required("admin")
def movimiento():
    """Registra una entrada de mercancía, una merma o un ajuste por conteo físico."""
    prods = query("SELECT id, codigo, nombre, stock, unidad FROM productos WHERE activo=1 ORDER BY nombre")
    if request.method == "POST":
        try:
            registrar_movimiento_manual(
                to_int(request.form.get("producto_id")), request.form.get("tipo", ""),
                to_float(request.form.get("cantidad")), request.form.get("motivo", "").strip(), g.user["id"])
            audit("movimiento_stock", f"{request.form.get('tipo')} prod={request.form.get('producto_id')}")
            aviso("Movimiento registrado en el kardex.")
            return redirect(url_for("catalogo.kardex", producto=request.form.get("producto_id")))
        except ValueError as e:
            aviso(str(e), "error")
    return render_template("productos/movimiento.html", productos=prods,
                           seleccionado=to_int(request.args.get("producto")))


@bp.route("/kardex")
@roles_required("admin")
def kardex():
    """Historial de movimientos del almacén (todos o de un producto)."""
    pid = to_int(request.args.get("producto"))
    hasta = request.args.get("hasta") or ahora()[:10]
    desde = request.args.get("desde") or (datetime.strptime(hasta, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    sql = """SELECT m.*, p.codigo, p.nombre AS producto, p.unidad, u.nombre AS usuario, v.numero AS venta
               FROM movimientos_stock m JOIN productos p ON p.id = m.producto_id
               LEFT JOIN usuarios u ON u.id = m.usuario_id LEFT JOIN ventas v ON v.id = m.venta_id
               WHERE substr(m.fecha,1,10) BETWEEN ? AND ?"""
    args = [desde, hasta]
    if pid:
        sql += " AND m.producto_id = ?"; args.append(pid)
    sql += " ORDER BY m.id DESC LIMIT 1000"
    prods = query("SELECT id, codigo, nombre FROM productos ORDER BY nombre")
    return render_template("productos/kardex.html", movs=query(sql, args), productos=prods, pid=pid, desde=desde, hasta=hasta)


@bp.route("/productos/<int:pid>/eliminar", methods=["POST"])
@roles_required("admin")
def producto_eliminar(pid):
    """Permite al admin retirar un producto sin romper ventas/Kardex históricos."""
    p = query("SELECT * FROM productos WHERE id=?", (pid,), one=True)
    if not p:
        aviso("Producto no encontrado.", "error")
        return redirect(url_for("catalogo.productos"))
    db = get_db()
    tiene_historial = query("SELECT 1 FROM venta_detalles WHERE producto_id=? LIMIT 1", (pid,), one=True) or query(
        "SELECT 1 FROM movimientos_stock WHERE producto_id=? LIMIT 1", (pid,), one=True)
    if tiene_historial:
        db.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))
        aviso("El producto tiene historial; se desactivó para conservar las ventas y el Kardex.")
        audit("producto_desactivado", p["codigo"])
    else:
        db.execute("DELETE FROM productos WHERE id=?", (pid,))
        aviso("Producto eliminado.")
        audit("producto_eliminado", p["codigo"])
    db.commit()
    return redirect(url_for("catalogo.productos"))
