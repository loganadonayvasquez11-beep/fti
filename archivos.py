"""Archivo diario de reportes PDF. Los registros fuente se conservan para auditoría."""
from datetime import datetime
from pathlib import Path, PureWindowsPath
from io import BytesIO
import os

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

from db import get_db
from utils import ahora, hoy
from services import get_config
from pdf import CanvasEscalaImpresion

AZUL = colors.HexColor("#fa0303")
GRIS = colors.HexColor("#555555")
GRIS_CLARO = colors.HexColor("#f0f0f0")


def resolver_ruta_archivo_historico(ruta):
    """Resuelve rutas guardadas en otro sistema operativo dentro del archivo persistente."""
    carpeta = (Path(current_app.instance_path) / "archivos_historicos").resolve()
    ruta_original = str(ruta)
    ruta_local = Path(ruta_original)
    if ruta_local.is_absolute():
        resuelta = ruta_local.resolve()
        if carpeta in resuelta.parents:
            return resuelta
    nombre = PureWindowsPath(ruta_original).name if "\\" in ruta_original else ruta_local.name
    return (carpeta / nombre).resolve()


def limpiar_archivos_historicos_vencidos(fecha_actual=None):
    """Conserva únicamente el PDF diario vigente y sus metadatos."""
    db = get_db()
    fecha_actual = fecha_actual or hoy()
    fila_actual = db.execute(
        """SELECT id, ruta FROM archivos_historicos
           WHERE tipo='diario' AND substr(desde,1,10)=?
           ORDER BY generado DESC, id DESC LIMIT 1""",
        (fecha_actual,),
    ).fetchone()
    id_actual = fila_actual["id"] if fila_actual else None
    ruta_actual = resolver_ruta_archivo_historico(fila_actual["ruta"]) if fila_actual else None
    filas_vencidas = db.execute(
        "SELECT id, ruta FROM archivos_historicos WHERE id != ?",
        (id_actual if id_actual is not None else -1,),
    ).fetchall()
    db.execute(
        "DELETE FROM archivos_historicos WHERE id != ?",
        (id_actual if id_actual is not None else -1,),
    )
    db.commit()

    carpeta = (Path(current_app.instance_path) / "archivos_historicos").resolve()
    rutas = {resolver_ruta_archivo_historico(fila["ruta"]) for fila in filas_vencidas}
    rutas.update(carpeta.glob("historial_*.pdf"))
    for ruta in rutas:
        ruta = ruta.resolve()
        if ruta == ruta_actual or ruta.parent != carpeta or not ruta.name.startswith("historial_"):
            continue
        try:
            ruta.unlink()
        except FileNotFoundError:
            pass


def generar_archivo_diario(usuario_id=None):
    """Genera o actualiza el PDF con la actividad de hoy y el saldo actual de deudas."""
    db = get_db()
    ahora_dt = datetime.strptime(ahora(), "%Y-%m-%d %H:%M:%S")
    fecha_dia = ahora_dt.strftime("%Y-%m-%d")
    desde = f"{fecha_dia} 00:00:00"
    hasta = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")

    movs = db.execute("""SELECT m.fecha,p.codigo,p.nombre,m.tipo,m.cantidad,m.stock_resultante,m.motivo,u.nombre usuario
        FROM movimientos_stock m JOIN productos p ON p.id=m.producto_id LEFT JOIN usuarios u ON u.id=m.usuario_id
        WHERE m.fecha BETWEEN ? AND ? ORDER BY m.fecha,m.id""", (desde, hasta)).fetchall()
    aud = db.execute("""SELECT a.fecha,u.nombre usuario,a.accion,a.detalle FROM auditoria a
        LEFT JOIN usuarios u ON u.id=a.usuario_id WHERE a.fecha BETWEEN ? AND ? ORDER BY a.fecha,a.id""", (desde, hasta)).fetchall()
    ventas = db.execute("""SELECT v.fecha,v.numero,c.nombre cliente,u.nombre vendedor,v.estado,v.total_usd,
        v.moneda,d.cantidad,d.precio_unit,d.subtotal,p.codigo,p.nombre producto,p.unidad
        FROM ventas v JOIN clientes c ON c.id=v.cliente_id JOIN usuarios u ON u.id=v.vendedor_id
        JOIN venta_detalles d ON d.venta_id=v.id JOIN productos p ON p.id=d.producto_id
        WHERE v.fecha BETWEEN ? AND ? ORDER BY v.fecha,v.id,d.id""", (desde, hasta)).fetchall()
    deudas = db.execute("""SELECT d.fecha,c.nombre cliente,u.nombre vendedor,d.monto_usd,d.vencimiento,d.estado,
        COALESCE((SELECT SUM(a.monto_usd) FROM abonos a WHERE a.deuda_id=d.id AND a.anulado=0),0) abonado
        FROM deudas d JOIN clientes c ON c.id=d.cliente_id JOIN usuarios u ON u.id=c.vendedor_id
        WHERE d.fecha BETWEEN ? AND ? ORDER BY d.fecha,d.id""", (desde, hasta)).fetchall()
    deudas_pendientes = db.execute("""SELECT d.fecha,c.nombre cliente,u.nombre vendedor,d.concepto,d.vencimiento,
        d.monto_usd,
        COALESCE((SELECT SUM(a.monto_usd) FROM abonos a WHERE a.deuda_id=d.id AND a.anulado=0),0) abonado_usd,
        d.monto_usd - COALESCE((SELECT SUM(a.monto_usd) FROM abonos a
            WHERE a.deuda_id=d.id AND a.anulado=0),0) saldo_usd,d.estado
        FROM deudas d JOIN clientes c ON c.id=d.cliente_id JOIN usuarios u ON u.id=c.vendedor_id
        WHERE d.estado IN ('activa','pendiente')
          AND d.monto_usd - COALESCE((SELECT SUM(a.monto_usd) FROM abonos a
            WHERE a.deuda_id=d.id AND a.anulado=0),0) > 0.005
        ORDER BY d.vencimiento,c.nombre""").fetchall()
    abonos = db.execute("""SELECT a.fecha,c.nombre cliente,u.nombre vendedor,a.monto,a.moneda,a.monto_usd,a.metodo,
        a.referencia,a.anulado FROM abonos a JOIN deudas d ON d.id=a.deuda_id JOIN clientes c ON c.id=d.cliente_id
        LEFT JOIN usuarios u ON u.id=a.registrado_por WHERE a.fecha BETWEEN ? AND ? ORDER BY a.fecha,a.id""", (desde, hasta)).fetchall()

    styles = getSampleStyleSheet()
    
    # Estilos personalizados acordes al diseño corporativo
    ESTILO = {
        "pequeno": ParagraphStyle("p", parent=styles["Normal"], fontSize=8, leading=10, textColor=GRIS),
        "titulo": ParagraphStyle("t", parent=styles["Title"], fontSize=15, leading=18, textColor=AZUL, alignment=2),
        "empresa": ParagraphStyle("e", parent=styles["Normal"], fontSize=12, leading=15, fontName="Helvetica-Bold"),
    }

    def _p(texto, estilo="pequeno"):
        return Paragraph(str(texto), ESTILO.get(estilo, styles["Normal"]))

    # Encabezado corporativo con logo (estilo idéntico a los otros reportes)
    logo_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "static",
        "logo_pdf.png"
    )
    if os.path.isfile(logo_path):
        logo = Image(logo_path, width=28 * mm, height=23 * mm, kind="proportional")
        logo.hAlign = "LEFT"
    else:
        logo = Spacer(28 * mm, 20 * mm)

    empresa_info = [
        _p(get_config("empresa_nombre", "FT Import"), "empresa"),
        _p(f"RIF: {get_config('empresa_rif')}" if get_config("empresa_rif") else "", "pequeno"),
        _p(get_config("empresa_direccion") or "", "pequeno"),
        _p(get_config("empresa_telefono") or "", "pequeno"),
    ]

    izquierda = Table([[logo, empresa_info]], colWidths=[30 * mm, 120 * mm], hAlign="CENTER")
    izquierda.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    derecha = Table([
        [Paragraph("ARCHIVO HISTÓRICO DIARIO", ESTILO["titulo"])],
        [_p(f"Fecha: {ahora_dt.strftime('%d-%m-%Y')}", "pequeno")],
        [_p(f"Período: {desde} a {hasta}", "pequeno")],
        [_p(f"Generado: {ahora()}", "pequeno")],
    ], colWidths=[120 * mm], hAlign="CENTER")
    derecha.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    encabezado_tabla = Table([[izquierda, derecha]], colWidths=[150 * mm, 123 * mm], hAlign="CENTER")
    encabezado_tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, AZUL),
    ]))

    story = [encabezado_tabla, Spacer(1, 8 * mm)]

    def agregar_tabla(titulo, encabezados, filas):
        story.append(Paragraph(titulo, styles["Heading2"]))
        from xml.sax.saxutils import escape
        datos = [[Paragraph(escape(str(x)), styles["BodyText"]) for x in encabezados]]
        if filas:
            datos += [[Paragraph(escape("" if x is None else str(x)), styles["BodyText"]) for x in fila] for fila in filas]
        else:
            datos += [[Paragraph("Sin registros", styles["BodyText"])] + [""]*(len(encabezados)-1)]
        ancho_util = landscape(A4)[0] - 24*mm
        tabla = Table(datos, colWidths=[ancho_util / len(encabezados)] * len(encabezados), repeatRows=1,
                      hAlign="CENTER")
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), AZUL),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), .35, colors.grey),
            ("FONTSIZE", (0,0), (-1,-1), 7), ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, GRIS_CLARO]),
        ]))
        story.extend([tabla, Spacer(1, 5*mm)])

    agregar_tabla("Kardex y movimientos de almacén", ["Fecha/hora","Código","Producto","Tipo","Cantidad","Stock final","Motivo","Usuario"],
                  [(r["fecha"],r["codigo"],r["nombre"],r["tipo"],r["cantidad"],r["stock_resultante"],r["motivo"],r["usuario"]) for r in movs])
    agregar_tabla("Detalle de ventas del día", ["Fecha/hora","Venta","Producto","Cantidad","Cliente","Vendedor","Importe línea","Total USD","Estado"],
                  [(r["fecha"],r["numero"],f'{r["codigo"]} - {r["producto"]}',
                    f'{r["cantidad"]} {r["unidad"]}',r["cliente"],r["vendedor"],
                    f'{r["subtotal"]:.2f} {r["moneda"]}',f'{r["total_usd"]:.2f}',r["estado"]) for r in ventas])
    agregar_tabla("Deudas creadas en el período", ["Fecha/hora","Cliente","Vendedor","Monto USD","Vencimiento","Estado","Abonado USD"],
                  [(r["fecha"],r["cliente"],r["vendedor"],f'{r["monto_usd"]:.2f}',r["vencimiento"],r["estado"],f'{r["abonado"]:.2f}') for r in deudas])
    agregar_tabla("Clientes que deben (saldo actual)", ["Fecha","Cliente","Vendedor actual","Concepto","Vencimiento","Monto USD","Abonado USD","Saldo USD","Estado"],
                  [(r["fecha"],r["cliente"],r["vendedor"],r["concepto"],r["vencimiento"],
                    f'{r["monto_usd"]:.2f}',f'{r["abonado_usd"]:.2f}',f'{r["saldo_usd"]:.2f}',r["estado"])
                   for r in deudas_pendientes])
    agregar_tabla("Abonos y recibos registrados", ["Fecha/hora","Cliente","Vendedor","Importe USD","Método","Referencia","Anulado"],
                  [(r["fecha"],r["cliente"],r["vendedor"],f'{r["monto_usd"]:.2f}',r["metodo"],r["referencia"],"Sí" if r["anulado"] else "No") for r in abonos])
    agregar_tabla("Auditoría del sistema", ["Fecha/hora","Usuario","Acción","Detalle"],
                  [(r["fecha"],r["usuario"],r["accion"],r["detalle"]) for r in aud])

    salida = BytesIO()
    doc = SimpleDocTemplate(salida, pagesize=landscape(A4), rightMargin=12*mm, leftMargin=12*mm,
                            topMargin=12*mm, bottomMargin=12*mm, title="Archivo histórico diario")
    doc.build(story, canvasmaker=CanvasEscalaImpresion)
    contenido = salida.getvalue()
    if not contenido.startswith(b"%PDF-") or len(contenido) < 500:
        raise RuntimeError("No se pudo verificar el PDF generado; no se archivó el período.")

    carpeta = Path(current_app.instance_path) / "archivos_historicos"
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"historial_{ahora_dt.strftime('%d-%m-%Y')}.pdf"
    ruta = carpeta / nombre
    temporal = ruta.with_suffix(".pdf.tmp")
    try:
        temporal.write_bytes(contenido)
        temporal.replace(ruta)
    finally:
        if temporal.exists():
            temporal.unlink()

    existente = db.execute(
        """SELECT id FROM archivos_historicos
           WHERE tipo='diario' AND substr(desde,1,10)=?
           ORDER BY id DESC LIMIT 1""",
        (fecha_dia,),
    ).fetchone()
    if existente:
        archivo_id = existente["id"]
        db.execute("""UPDATE archivos_historicos
            SET titulo=?,tipo='diario',desde=?,hasta=?,generado=?,ruta=?,usuario_id=?,estado='listo'
            WHERE id=?""",
            ("Archivo histórico diario", desde, hasta, ahora(), str(ruta), usuario_id, archivo_id))
    else:
        cur = db.execute("""INSERT INTO archivos_historicos(titulo,tipo,desde,hasta,generado,ruta,usuario_id,estado)
            VALUES(?,?,?,?,?,?,?,'listo')""",
            ("Archivo histórico diario", "diario", desde, hasta, ahora(), str(ruta), usuario_id))
        archivo_id = cur.lastrowid
    db.commit()
    limpiar_archivos_historicos_vencidos(fecha_dia)
    fila = db.execute("SELECT * FROM archivos_historicos WHERE id=?", (archivo_id,)).fetchone()
    return dict(fila), existente is None