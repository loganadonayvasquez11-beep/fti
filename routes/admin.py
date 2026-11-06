"""Módulos del administrador: cartera de vendedores, configuración, reportes y auditoría."""
from flask import Blueprint, current_app, g, redirect, render_template, request, url_for, send_file, abort
from werkzeug.security import generate_password_hash

from db import get_db, query
from pdf_responses import responder_pdf
from services import audit, get_config, set_config
from utils import ahora, aviso, hoy, roles_required, to_float, to_int
from archivos import (generar_archivo_diario, limpiar_archivos_historicos_vencidos,
                      resolver_ruta_archivo_historico)
from pathlib import Path
from datetime import datetime, timedelta

bp = Blueprint("admin", __name__)


# ============================================================ cartera de vendedores
@bp.route("/vendedores")
@roles_required("admin")
def vendedores():
    """Cartera del admin: todos sus vendedores con cifras de clientes, ventas y deuda por cobrar."""
    mes = hoy()[:7]
    filas = query(
        """SELECT u.*,
                  (SELECT COUNT(*) FROM clientes c WHERE c.vendedor_id = u.id AND c.activo = 1) AS n_clientes,
                  (SELECT COUNT(DISTINCT d.cliente_id) FROM v_deudas d
                    WHERE d.vendedor_id = u.id AND d.estado = 'activa' AND d.saldo_usd > 0.005) AS n_deudores,
                  COALESCE((SELECT SUM(saldo_usd) FROM v_deudas d WHERE d.vendedor_id = u.id AND d.estado='activa'), 0) AS por_cobrar,
                  COALESCE((SELECT SUM(saldo_usd) FROM v_deudas d WHERE d.vendedor_id = u.id AND d.estado='activa'
                             AND d.vencimiento < ?), 0) AS vencido,
                  COALESCE((SELECT SUM(v.total_usd) FROM ventas v JOIN clientes c ON c.id = v.cliente_id
                             WHERE c.vendedor_id = u.id AND v.estado='aprobada'
                               AND substr(v.fecha,1,7) = ?), 0) AS vendido_mes,
                  (SELECT COUNT(*) FROM ventas v JOIN clientes c ON c.id = v.cliente_id
                    WHERE c.vendedor_id = u.id AND v.estado='pendiente') AS pendientes
             FROM usuarios u WHERE u.rol = 'vendedor' ORDER BY u.activo DESC, u.nombre""", (hoy(), mes))
    return render_template("admin/vendedores.html", vendedores=filas)


@bp.route("/vendedores/nuevo", methods=["GET", "POST"])
@roles_required("admin")
def vendedor_nuevo():
    """Agrega un vendedor a la cartera del admin."""
    if request.method == "POST":
        f = request.form
        usuario = f.get("usuario", "").strip().lower()
        nombre, clave = f.get("nombre", "").strip(), f.get("clave", "")
        if not usuario or not nombre:
            aviso("Usuario y nombre son obligatorios.", "error")
        elif len(clave) < 6:
            aviso("La clave debe tener al menos 6 caracteres.", "error")
        elif query("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,), one=True):
            aviso("Ese nombre de usuario ya existe.", "error")
        else:
            db = get_db()
            cur = db.execute(
                "INSERT INTO usuarios (usuario, nombre, clave_hash, rol, telefono, email, creado, tipo_vendedor) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (usuario, nombre, generate_password_hash(clave), "vendedor", f.get("telefono", "").strip(),
                 f.get("email", "").strip(), ahora(), _tipo_vendedor(f)))
            db.commit()
            vendedor_id = cur.lastrowid
            audit("vendedor_creado", usuario)
            aviso("Vendedor agregado a tu cartera.")
            return redirect(url_for("admin.vendedor_detalle", vid=vendedor_id))
    return render_template("admin/vendedor_form.html", u=request.form, nuevo=True)


def _tipo_vendedor(f):
    """Lee el tipo de vendedor del formulario: 'especial' o 'normal' (cualquier otro valor = normal)."""
    return "especial" if f.get("tipo_vendedor") == "especial" else "normal"


def _vendedor_o_none(vid):
    """Busca un usuario con rol vendedor."""
    return query("SELECT * FROM usuarios WHERE id = ? AND rol = 'vendedor'", (vid,), one=True)


@bp.route("/vendedores/<int:vid>")
@roles_required("admin")
def vendedor_detalle(vid):
    """Ficha del vendedor: sus clientes, sus deudores con saldo y sus ventas recientes."""
    u = _vendedor_o_none(vid)
    if u is None:
        return redirect(url_for("admin.vendedores"))
    clientes = query(
        """SELECT c.*, COALESCE((SELECT SUM(saldo_usd) FROM v_deudas d WHERE d.cliente_id = c.id AND d.estado='activa'),0) AS deuda_usd,
                  (SELECT MIN(vencimiento) FROM v_deudas d WHERE d.cliente_id = c.id AND d.estado='activa' AND d.saldo_usd > 0.005) AS prox_venc
             FROM clientes c WHERE c.vendedor_id = ? AND c.activo = 1 ORDER BY deuda_usd DESC, c.nombre""", (vid,))
    ventas = query("""SELECT v.*, c.nombre AS cliente FROM ventas v JOIN clientes c ON c.id = v.cliente_id
                       WHERE c.vendedor_id = ? ORDER BY v.id DESC LIMIT 10""", (vid,))
    otros = query("SELECT id, nombre FROM usuarios WHERE rol='vendedor' AND activo=1 AND id != ? ORDER BY nombre", (vid,))
    totales = query(
        """SELECT COALESCE(SUM(saldo_usd),0) AS por_cobrar,
                  COALESCE(SUM(CASE WHEN vencimiento < ? THEN saldo_usd ELSE 0 END),0) AS vencido
             FROM v_deudas WHERE vendedor_id = ? AND estado='activa'""", (hoy(), vid), one=True)
    return render_template("admin/vendedor_detalle.html", u=u, clientes=clientes, ventas=ventas, otros=otros, tot=totales)


@bp.route("/vendedores/<int:vid>/editar", methods=["GET", "POST"])
@roles_required("admin")
def vendedor_editar(vid):
    """Edita datos del vendedor y, opcionalmente, le cambia la clave."""
    u = _vendedor_o_none(vid)
    if u is None:
        return redirect(url_for("admin.vendedores"))
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        if not nombre:
            aviso("El nombre es obligatorio.", "error")
        elif f.get("clave") and len(f.get("clave")) < 6:
            aviso("La nueva clave debe tener al menos 6 caracteres.", "error")
        else:
            db = get_db()
            db.execute("UPDATE usuarios SET nombre=?, telefono=?, email=?, tipo_vendedor=? WHERE id=?",
                       (nombre, f.get("telefono", "").strip(), f.get("email", "").strip(), _tipo_vendedor(f), vid))
            if _tipo_vendedor(f) != u["tipo_vendedor"]:
                audit("vendedor_tipo", f"{u['usuario']}: {u['tipo_vendedor']} -> {_tipo_vendedor(f)}")
            if f.get("clave"):
                db.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (generate_password_hash(f.get("clave")), vid))
            db.commit()
            audit("vendedor_editado", u["usuario"])
            aviso("Vendedor actualizado.")
            return redirect(url_for("admin.vendedor_detalle", vid=vid))
    return render_template("admin/vendedor_form.html", u=u, nuevo=False)


@bp.route("/vendedores/<int:vid>/estado", methods=["POST"])
@roles_required("admin")
def vendedor_estado(vid):
    """Gestiona el estado del vendedor y, si se solicita, traspasa su cartera."""
    u = _vendedor_o_none(vid)
    if u is None:
        return redirect(url_for("admin.vendedores"))
    db = get_db()
    if u["activo"]:
        accion = request.form.get("accion", "congelar")
        destino_form = request.form.get("destino", "").strip()
        try:
            destino = int(destino_form) if destino_form else 0
        except ValueError:
            destino = 0
        if accion not in ("traspasar", "congelar"):
            aviso("Selecciona una acción válida.", "error")
            return redirect(url_for("admin.vendedor_detalle", vid=vid))
        if accion == "traspasar" and not destino_form:
            aviso("Selecciona el vendedor que recibirá los clientes y deudas.", "error")
            return redirect(url_for("admin.vendedor_detalle", vid=vid))
        if destino_form:
            receptor = query(
                "SELECT id FROM usuarios WHERE id=? AND rol='vendedor' AND activo=1",
                (destino,), one=True)
            if not receptor or destino == vid:
                aviso("Selecciona un vendedor destino activo y diferente.", "error")
                return redirect(url_for("admin.vendedor_detalle", vid=vid))
        with db:
            if destino:
                db.execute(
                    """UPDATE ventas SET vendedor_id=? WHERE cliente_id IN
                       (SELECT id FROM clientes WHERE vendedor_id=?)""", (destino, vid))
                db.execute(
                    """UPDATE deudas SET vendedor_id=? WHERE cliente_id IN
                       (SELECT id FROM clientes WHERE vendedor_id=?)""", (destino, vid))
                db.execute("UPDATE clientes SET vendedor_id=? WHERE vendedor_id=?", (destino, vid))
            if accion == "congelar":
                db.execute("UPDATE usuarios SET activo=0 WHERE id=?", (vid,))
        if accion == "congelar":
            audit("vendedor_desactivado", f"{u['usuario']} traspaso_a={destino or 'ninguno'}")
            aviso("Vendedor congelado." + (" Sus clientes y deudas fueron traspasados." if destino else ""))
        else:
            audit("vendedor_cartera_traspasada", f"{u['usuario']} traspaso_a={destino}")
            aviso("Clientes y deudas traspasados. El vendedor sigue activo.")
    else:
        db.execute("UPDATE usuarios SET activo=1 WHERE id=?", (vid,))
        db.commit()
        audit("vendedor_reactivado", u["usuario"])
        aviso("Vendedor reactivado.")
    return redirect(url_for("admin.vendedor_detalle", vid=vid))


# =============================================================== configuración
@bp.route("/configuracion", methods=["GET", "POST"])
@roles_required("admin")
def configuracion():
    """Datos de la empresa y reglas generales (todo se factura en dólares)."""
    if request.method == "POST":
        f = request.form
        for clave in ("empresa_nombre", "empresa_rif", "empresa_direccion", "empresa_telefono"):
            set_config(clave, f.get(clave, "").strip())
        set_config("dias_por_vencer", max(to_int(f.get("dias_por_vencer"), 3), 0))
        set_config("stock_alerta_defecto", max(to_float(f.get("stock_alerta_defecto"), 10), 0))
        set_config("contado_requiere_aprobacion", "1" if f.get("contado_requiere_aprobacion") else "0")
        audit("configuracion", "datos de empresa y reglas actualizados")
        aviso("Configuración guardada.")
        return redirect(url_for("admin.configuracion"))
    cfg = {r["clave"]: r["valor"] for r in query("SELECT clave, valor FROM configuracion")}
    return render_template("admin/configuracion.html", cfg=cfg)


# ==================================================================== reportes
@bp.route("/reportes")
@roles_required("admin")
def reportes():
    """Reportes: ventas por vendedor, deuda por vendedor, ventas con precio modificado y stock valorizado."""
    desde = request.args.get("desde") or hoy()[:8] + "01"
    hasta = request.args.get("hasta") or hoy()
    por_vendedor = query(
        """SELECT u.nombre, COUNT(v.id) AS n, COALESCE(SUM(v.total_usd),0) AS total
             FROM usuarios u LEFT JOIN clientes c ON c.vendedor_id = u.id
              LEFT JOIN ventas v ON v.cliente_id = c.id AND v.estado='aprobada'
              AND substr(v.fecha,1,10) BETWEEN ? AND ?
            WHERE u.rol='vendedor' GROUP BY u.id ORDER BY total DESC""", (desde, hasta))
    top_productos = query(
        """SELECT p.codigo, p.nombre, SUM(d.cantidad) AS cant FROM venta_detalles d
             JOIN ventas v ON v.id = d.venta_id JOIN productos p ON p.id = d.producto_id
            WHERE v.estado='aprobada' AND substr(v.fecha,1,10) BETWEEN ? AND ?
            GROUP BY p.id ORDER BY cant DESC LIMIT 10""", (desde, hasta))
    modificadas = query(
        """SELECT v.id, v.numero, v.fecha, u.nombre AS vendedor, c.nombre AS cliente, p.nombre AS producto,
                  v.moneda, d.precio_lista, d.precio_unit
             FROM venta_detalles d JOIN ventas v ON v.id = d.venta_id JOIN clientes c ON c.id = v.cliente_id
             JOIN usuarios u ON u.id = c.vendedor_id JOIN productos p ON p.id = d.producto_id
            WHERE d.modificado = 1 AND v.estado IN ('pendiente','aprobada')
              AND substr(v.fecha,1,10) BETWEEN ? AND ? ORDER BY v.id DESC LIMIT 100""", (desde, hasta))
    valor = query("SELECT COALESCE(SUM(stock*precio_mayor_usd),0) AS mayor FROM productos WHERE activo=1", one=True)
    return render_template("admin/reportes.html", desde=desde, hasta=hasta, por_vendedor=por_vendedor,
                           top=top_productos, modificadas=modificadas, valor=valor)


# =================================================================== auditoría
@bp.route("/auditoria")
@roles_required("admin")
def auditoria():
    """Auditoría por período; por defecto muestra los últimos siete días."""
    desde = request.args.get("desde") or (datetime.strptime(hoy(), "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    hasta = request.args.get("hasta") or hoy()
    filas = query("""SELECT a.*, u.nombre AS usuario FROM auditoria a LEFT JOIN usuarios u ON u.id = a.usuario_id
                      WHERE substr(a.fecha,1,10) BETWEEN ? AND ? ORDER BY a.id DESC LIMIT 2000""", (desde, hasta))
    return render_template("admin/auditoria.html", filas=filas, desde=desde, hasta=hasta)


# =========================================================== archivos históricos
@bp.route("/historiales")
@roles_required("admin")
def historiales():
    limpiar_archivos_historicos_vencidos()
    archivos = query("SELECT * FROM archivos_historicos ORDER BY generado DESC LIMIT 500")
    return render_template("admin/historiales.html", archivos=archivos)


@bp.route("/historiales/generar", methods=["POST"])
@roles_required("admin")
def historial_generar():
    try:
        archivo, _ = generar_archivo_diario(g.user["id"])
    except Exception as exc:
        current_app.logger.exception("Falló la generación del archivo histórico")
        aviso("No se pudo generar el PDF; revisa los registros del servidor.", "error")
        return redirect(url_for("admin.historiales"))
    return redirect(url_for("admin.historial_descargar", archivo_id=archivo["id"]))


@bp.route("/historiales/<int:archivo_id>/descargar")
@roles_required("admin")
def historial_descargar(archivo_id):
    fila = query("SELECT * FROM archivos_historicos WHERE id=? AND estado='listo'", (archivo_id,), one=True)
    if not fila:
        abort(404)
    ruta = resolver_ruta_archivo_historico(fila["ruta"])
    carpeta = (Path(current_app.instance_path) / "archivos_historicos").resolve()
    if carpeta not in ruta.parents or not ruta.is_file():
        abort(404)
    from archivos import generar_archivo_diario
    return responder_pdf(
        lambda escala: generar_archivo_diario(
            usuario_id=g.user["id"], escala=escala,
            rango=(fila["desde"], fila["hasta"]), persistir=False,
        ),
        ruta.name,
    )
