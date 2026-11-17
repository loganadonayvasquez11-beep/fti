"""Módulos del administrador: cartera de vendedores, configuración, reportes y auditoría."""
import calendar
from flask import Blueprint, current_app, g, redirect, render_template, request, url_for, send_file, abort
from werkzeug.security import generate_password_hash

from db import get_db, query
from pdf_responses import responder_pdf
from services import audit, get_config, set_config
from utils import ahora, aviso, hoy, roles_required, to_float, to_int
from archivos import (generar_archivo_diario, generar_pdf_historico_periodo,
                      resolver_ruta_archivo_historico)
from pathlib import Path
from datetime import datetime, timedelta

bp = Blueprint("admin", __name__)

HISTORIAL_FILTROS = (
    ("todo", "Todo"),
    ("ventas", "Ventas"),
    ("cobros", "Cobros"),
    ("deudas", "Deudas"),
    ("inventario", "Inventario"),
    ("auditoria", "Auditoría"),
)


def _historial_filtro_actual():
    tipo = request.args.get("tipo", "todo")
    if tipo not in {valor for valor, _ in HISTORIAL_FILTROS}:
        abort(400, "El filtro del historial no es válido.")
    return tipo


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
        usuario = f.get("usuario", "").strip().lower()
        nombre = f.get("nombre", "").strip()
        if not usuario or not nombre:
            aviso("Usuario y nombre son obligatorios.", "error")
        elif query("SELECT 1 FROM usuarios WHERE usuario = ? AND id != ?", (usuario, vid), one=True):
            aviso("Ese nombre de usuario ya existe.", "error")
        elif f.get("clave") and len(f.get("clave")) < 6:
            aviso("La nueva clave debe tener al menos 6 caracteres.", "error")
        else:
            db = get_db()
            db.execute("UPDATE usuarios SET usuario=?, nombre=?, telefono=?, email=?, tipo_vendedor=? WHERE id=?",
                       (usuario, nombre, f.get("telefono", "").strip(), f.get("email", "").strip(),
                        _tipo_vendedor(f), vid))
            if _tipo_vendedor(f) != u["tipo_vendedor"]:
                audit("vendedor_tipo", f"{u['usuario']}: {u['tipo_vendedor']} -> {_tipo_vendedor(f)}")
            if f.get("clave"):
                db.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (generate_password_hash(f.get("clave")), vid))
            db.commit()
            audit("vendedor_editado", f"{u['usuario']} -> {usuario}")
            aviso("Vendedor actualizado.")
            return redirect(url_for("admin.vendedor_detalle", vid=vid))
        u = {**dict(u), **f, "usuario": f.get("usuario", "").strip()}
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

def _historial_anios():
    """Lista los años con actividad en la base para navegar por período."""
    consulta = """
        SELECT DISTINCT substr(fecha,1,4) AS anio
        FROM (
            SELECT fecha FROM ventas
            UNION ALL SELECT fecha FROM deudas
            UNION ALL SELECT fecha FROM abonos
            UNION ALL SELECT fecha FROM movimientos_stock
            UNION ALL SELECT fecha FROM auditoria
        )
        WHERE fecha IS NOT NULL
        ORDER BY anio DESC
    """
    anios = {int(fila["anio"]) for fila in query(consulta)}
    anios.add(int(hoy()[:4]))
    return sorted(anios, reverse=True)


def _historial_meses(anio):
    """Devuelve los meses con actividad del año y el total vendido acumulado."""
    nombres_meses = (
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    )
    rows = query(
        """
        SELECT substr(fecha,1,7) AS periodo,
               COALESCE(SUM(CASE WHEN estado='aprobada' THEN total_usd ELSE 0 END), 0) AS vendido
        FROM ventas
        WHERE substr(fecha,1,4) = ?
        GROUP BY substr(fecha,1,7)
        ORDER BY periodo DESC
        """,
        (str(anio),),
    )
    mapa = {fila["periodo"]: float(fila["vendido"]) for fila in rows}
    cobros = query(
        """SELECT substr(fecha,1,7) AS periodo, SUM(monto) AS cobrado
             FROM (
                 SELECT COALESCE(fecha_aprobacion, fecha) AS fecha, total_usd AS monto
                   FROM ventas
                  WHERE estado='aprobada' AND tipo_pago='contado' AND pagada=1
                 UNION ALL
                 SELECT fecha, monto_usd AS monto FROM abonos WHERE anulado=0
             ) pagos
            WHERE substr(fecha,1,4)=?
            GROUP BY substr(fecha,1,7)""",
        (str(anio),),
    )
    mapa_cobros = {fila["periodo"]: float(fila["cobrado"]) for fila in cobros}
    meses = []
    for numero in range(1, 13):
        periodo = f"{anio}-{numero:02d}"
        meses.append({
            "periodo": periodo,
            "label": nombres_meses[numero - 1],
            "vendido": mapa.get(periodo, 0.0),
            "cobrado": mapa_cobros.get(periodo, 0.0),
        })
    return list(reversed(meses))


def _historial_dias(periodo):
    """Devuelve los días del mes con ventas o actividad para navegar por el mes."""
    actividad = query(
        """
        SELECT substr(fecha,1,10) AS dia,tipo
        FROM (
            SELECT fecha,'ventas' AS tipo FROM ventas
            UNION ALL SELECT COALESCE(fecha_aprobacion,fecha),'cobros'
              FROM ventas WHERE estado='aprobada' AND tipo_pago='contado' AND pagada=1
            UNION ALL SELECT fecha,'cobros' FROM abonos
            UNION ALL SELECT fecha,'deudas' FROM deudas
            UNION ALL SELECT fecha,'inventario' FROM movimientos_stock
            UNION ALL SELECT fecha,'auditoria' FROM auditoria
        ) actividad
        WHERE fecha IS NOT NULL AND substr(fecha,1,7)=?
        ORDER BY dia DESC,tipo
        """,
        (periodo,),
    )
    tipos_por_dia = {}
    for fila in actividad:
        tipos_por_dia.setdefault(fila["dia"], set()).add(fila["tipo"])
    ventas = query(
        """SELECT substr(fecha,1,10) AS dia, SUM(total_usd) AS vendido
             FROM ventas WHERE estado='aprobada' AND substr(fecha,1,7)=?
            GROUP BY substr(fecha,1,10)""",
        (periodo,),
    )
    cobros = query(
        """SELECT substr(fecha,1,10) AS dia, SUM(monto) AS cobrado
             FROM (
                 SELECT COALESCE(fecha_aprobacion, fecha) AS fecha, total_usd AS monto
                   FROM ventas
                  WHERE estado='aprobada' AND tipo_pago='contado' AND pagada=1
                 UNION ALL
                 SELECT fecha, monto_usd AS monto FROM abonos WHERE anulado=0
             ) pagos
            WHERE substr(fecha,1,7)=?
            GROUP BY substr(fecha,1,10)""",
        (periodo,),
    )
    ventas_por_dia = {fila["dia"]: float(fila["vendido"]) for fila in ventas}
    cobros_por_dia = {fila["dia"]: float(fila["cobrado"]) for fila in cobros}
    ultimo_dia = calendar.monthrange(int(periodo[:4]), int(periodo[5:7]))[1]
    lista = []
    for dia in range(1, ultimo_dia + 1):
        fecha = f"{periodo}-{dia:02d}"
        lista.append({
            "dia": dia,
            "fecha": fecha,
            "vendido": ventas_por_dia.get(fecha, 0.0),
            "cobrado": cobros_por_dia.get(fecha, 0.0),
            "activo": fecha in tipos_por_dia,
            "tipos": tipos_por_dia.get(fecha, set()),
            "label": datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y"),
        })
    return list(reversed(lista))


def _historial_cobros(desde, hasta):
    """Lista ventas de contado pagadas y abonos según la fecha real del cobro."""
    return query(
        """SELECT fecha, cliente, vendedor, registrado_por, tipo, referencia, monto_usd, metodo, anulado
             FROM (
                 SELECT COALESCE(v.fecha_aprobacion, v.fecha) AS fecha, c.nombre AS cliente,
                        u.nombre AS vendedor, COALESCE(a.nombre, u.nombre) AS registrado_por,
                        'Venta de contado' AS tipo, v.numero AS referencia,
                        v.total_usd AS monto_usd, v.metodo_pago AS metodo, 0 AS anulado
                   FROM ventas v JOIN clientes c ON c.id=v.cliente_id
                   JOIN usuarios u ON u.id=v.vendedor_id
                   LEFT JOIN usuarios a ON a.id=v.aprobado_por
                  WHERE v.estado='aprobada' AND v.tipo_pago='contado' AND v.pagada=1
                    AND substr(COALESCE(v.fecha_aprobacion, v.fecha),1,10) BETWEEN ? AND ?
                 UNION ALL
                 SELECT a.fecha, c.nombre AS cliente, owner.nombre AS vendedor,
                        collector.nombre AS registrado_por, 'Abono' AS tipo,
                        COALESCE(NULLIF(a.referencia,''), v.numero, d.concepto) AS referencia,
                        a.monto_usd, a.metodo, a.anulado
                   FROM abonos a JOIN deudas d ON d.id=a.deuda_id
                   JOIN clientes c ON c.id=d.cliente_id
                   JOIN usuarios owner ON owner.id=d.vendedor_id
                   LEFT JOIN usuarios collector ON collector.id=a.registrado_por
                   LEFT JOIN ventas v ON v.id=d.venta_id
                  WHERE substr(a.fecha,1,10) BETWEEN ? AND ?
             ) cobros
            ORDER BY fecha, referencia""",
        (desde, hasta, desde, hasta),
    )


def _historial_detalle_periodo(desde_fecha, hasta_fecha):
    """Consulta ventas, cobros y demás actividad registrada dentro de un período."""
    desde_fecha = desde_fecha[:10]
    hasta_fecha = hasta_fecha[:10]
    movs = query(
        """SELECT m.fecha,p.codigo,p.nombre,m.tipo,m.cantidad,m.stock_resultante,m.motivo,u.nombre usuario
           FROM movimientos_stock m JOIN productos p ON p.id=m.producto_id LEFT JOIN usuarios u ON u.id=m.usuario_id
           WHERE substr(m.fecha,1,10) BETWEEN ? AND ? ORDER BY m.fecha,m.id""",
        (desde_fecha, hasta_fecha),
    )
    aud = query(
        """SELECT a.fecha,u.nombre usuario,a.accion,a.detalle FROM auditoria a
            LEFT JOIN usuarios u ON u.id=a.usuario_id
            WHERE substr(a.fecha,1,10) BETWEEN ? AND ? ORDER BY a.fecha,a.id""",
        (desde_fecha, hasta_fecha),
    )
    ventas = query(
        """SELECT v.fecha,v.numero,c.nombre cliente,u.nombre vendedor,v.estado,v.total_usd,
            v.moneda,d.cantidad,d.precio_unit,d.subtotal,p.codigo,p.nombre producto,p.unidad
            FROM ventas v JOIN clientes c ON c.id=v.cliente_id JOIN usuarios u ON u.id=v.vendedor_id
            JOIN venta_detalles d ON d.venta_id=v.id JOIN productos p ON p.id=d.producto_id
            WHERE substr(v.fecha,1,10) BETWEEN ? AND ? ORDER BY v.fecha,v.id,d.id""",
        (desde_fecha, hasta_fecha),
    )
    deudas = query(
        """SELECT d.fecha,c.nombre cliente,u.nombre vendedor,d.concepto,d.monto_usd,d.vencimiento,d.estado,
            COALESCE((SELECT SUM(a.monto_usd) FROM abonos a WHERE a.deuda_id=d.id AND a.anulado=0),0) abonado
            FROM deudas d JOIN clientes c ON c.id=d.cliente_id JOIN usuarios u ON u.id=d.vendedor_id
            WHERE substr(d.fecha,1,10) BETWEEN ? AND ? ORDER BY d.fecha,d.id""",
        (desde_fecha, hasta_fecha),
    )
    cobros = _historial_cobros(desde_fecha[:10], hasta_fecha[:10])
    total_ventas = query(
        """SELECT COALESCE(SUM(total_usd),0) AS total FROM ventas
            WHERE estado='aprobada' AND substr(fecha,1,10) BETWEEN ? AND ?""",
        (desde_fecha, hasta_fecha),
        one=True,
    )["total"]
    return {
        "movs": movs,
        "aud": aud,
        "ventas": ventas,
        "deudas": deudas,
        "cobros": cobros,
        "total_ventas": float(total_ventas),
        "total_cobrado": sum(float(c["monto_usd"]) for c in cobros if not c["anulado"]),
    }


def _historial_detalle(fecha):
    """Consulta la actividad de un día."""
    return _historial_detalle_periodo(fecha, fecha)


def _historial_filtro_vacio(detalle, tipo):
    claves = {
        "ventas": "ventas",
        "cobros": "cobros",
        "deudas": "deudas",
        "inventario": "movs",
        "auditoria": "aud",
    }
    if tipo == "todo":
        return not any(detalle[clave] for clave in claves.values())
    return not detalle[claves[tipo]]


# =========================================================== archivos históricos
@bp.route("/historiales")
@roles_required("admin")
def historiales():
    return render_template(
        "admin/historiales.html",
        archivos=[],
        nivel="anios",
        anios=_historial_anios(),
    )


@bp.route("/historiales/<int:anio>")
@roles_required("admin")
def historiales_anio(anio):
    archivos = query("SELECT * FROM archivos_historicos ORDER BY generado DESC LIMIT 500")
    meses = _historial_meses(anio)
    return render_template(
        "admin/historiales.html",
        archivos=archivos,
        detalle=None,
        nivel="anio",
        anio=anio,
        anios=_historial_anios(),
        meses=meses,
        mes=None,
        dia=None,
        total_ventas=sum(float(m["vendido"]) for m in meses),
        total_cobrado=sum(float(m["cobrado"]) for m in meses),
    )


@bp.route("/historiales/<int:anio>/<int:mes>")
@roles_required("admin")
def historiales_mes(anio, mes):
    if mes < 1 or mes > 12:
    filtro_tipo = _historial_filtro_actual()
        abort(404)
    periodo = f"{anio}-{mes:02d}"
    fecha_filtro = request.args.get("fecha", "").strip()
    if fecha_filtro:
        try:
            fecha_seleccionada = datetime.strptime(fecha_filtro, "%Y-%m-%d")
        except ValueError:
            abort(400, "Selecciona una fecha válida.")
        if fecha_seleccionada.strftime("%Y-%m") != periodo:
            abort(400, "La fecha seleccionada debe pertenecer al mes abierto.")
        return redirect(url_for(
            "admin.historiales_dia",
            anio=anio,
            mes=mes,
            tipo=filtro_tipo,
            dia=fecha_seleccionada.day,
        ))
    archivos = query("SELECT * FROM archivos_historicos ORDER BY generado DESC LIMIT 500")
    dias_visibles = [
        dia for dia in dias
        if filtro_tipo == "todo" or filtro_tipo in dia["tipos"]
    ]
    dias = _historial_dias(periodo)
    vendido = sum(float(d["vendido"]) for d in dias if d["activo"])
    cobrado = sum(float(d["cobrado"]) for d in dias if d["activo"])
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    detalle_mes = _historial_detalle_periodo(
        f"{periodo}-01",
        f"{periodo}-{ultimo_dia:02d}",
    )
    return render_template(
        "admin/historiales.html",
        archivos=archivos,
        detalle=None,
        nivel="mes",
        anio=anio,
        mes=mes,
        dia=None,
        meses=_historial_meses(anio),
        anios=_historial_anios(),
        dias=dias,
        total_ventas=vendido,
        total_cobrado=cobrado,
        actividad=detalle_mes,
        filtro_tipo=filtro_tipo,
        filtros_historial=HISTORIAL_FILTROS,
        dias_visibles=dias_visibles,
        filtro_vacio=_historial_filtro_vacio(detalle_mes, filtro_tipo),
        fecha_min=f"{periodo}-01",
        fecha_max=f"{periodo}-{ultimo_dia:02d}",
    )


@bp.route("/historiales/<int:anio>/<int:mes>/pdf")
@roles_required("admin")
def historial_pdf_mes(anio, mes):
    if mes < 1 or mes > 12:
        abort(404)
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    desde = f"{anio}-{mes:02d}-01"
    hasta = f"{anio}-{mes:02d}-{ultimo_dia:02d}"
    contenido = generar_pdf_historico_periodo(desde, hasta)
    nombre = f"historial_{anio}_{mes:02d}.pdf"
    return responder_pdf(contenido, nombre)


@bp.route("/historiales/<int:anio>/<int:mes>/<int:dia>")
@roles_required("admin")
def historiales_dia(anio, mes, dia):
    if mes < 1 or mes > 12 or dia < 1:
        abort(404)
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    if dia > ultimo_dia:
        abort(404)
    filtro_tipo = _historial_filtro_actual()
    fecha = f"{anio}-{mes:02d}-{dia:02d}"
    detalle = _historial_detalle(fecha)
    archivos = query("SELECT * FROM archivos_historicos ORDER BY generado DESC LIMIT 500")
    return render_template(
        "admin/historiales.html",
        archivos=archivos,
        detalle=detalle,
        nivel="dia",
        anio=anio,
        mes=mes,
        dia=dia,
        fecha=fecha,
        meses=_historial_meses(anio),
        anios=_historial_anios(),
        dias=_historial_dias(f"{anio}-{mes:02d}"),
        total_ventas=detalle["total_ventas"],
        filtro_tipo=filtro_tipo,
        filtros_historial=HISTORIAL_FILTROS,
        actividad=detalle,
        filtro_vacio=_historial_filtro_vacio(detalle, filtro_tipo),
        total_cobrado=detalle["total_cobrado"],
    )


@bp.route("/historiales/<int:anio>/<int:mes>/<int:dia>/pdf")
@roles_required("admin")
def historial_pdf_dia(anio, mes, dia):
    if mes < 1 or mes > 12 or dia < 1:
        abort(404)
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    if dia > ultimo_dia:
        abort(404)
    fecha = f"{anio}-{mes:02d}-{dia:02d}"
    contenido = generar_pdf_historico_periodo(fecha, fecha)
    return responder_pdf(contenido, f"historial_{fecha}.pdf")


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
    return responder_pdf(ruta, ruta.name)
