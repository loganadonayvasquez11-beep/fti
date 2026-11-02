"""Generación de documentos en PDF (ReportLab): nota de venta, recibo de abono y estado de cuenta."""
from io import BytesIO
from reportlab.platypus import Image, Table
from reportlab.lib.units import cm
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas as ReportLabCanvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from db import query
from services import get_config, estado_vencimiento
from utils import ahora, fmt_fecha, fmt_fechahora, fmt_money, fmt_num, fmt_qty

AZUL = colors.HexColor("#850b0b")
GRIS = colors.HexColor("#555555")
GRIS_CLARO = colors.HexColor("#f0f0f0")
ESCALA_IMPRESION = 1.04


class CanvasEscalaImpresion(ReportLabCanvas):
    """Amplía el contenido un 4% y lo centra sin cambiar el tamaño de papel."""

    def showPage(self):
        ancho, alto = self._pagesize
        despl_x = (1 - ESCALA_IMPRESION) * ancho / 2
        despl_y = (1 - ESCALA_IMPRESION) * alto / 2
        self._code.insert(0, "q")
        self._code.insert(
            1,
            f"{ESCALA_IMPRESION} 0 0 {ESCALA_IMPRESION} {despl_x} {despl_y} cm",
        )
        self._code.append("Q")
        super().showPage()

_base = getSampleStyleSheet()
ESTILO = {
    "normal": ParagraphStyle("n", parent=_base["Normal"], fontSize=9, leading=12),
    "pequeno": ParagraphStyle("p", parent=_base["Normal"], fontSize=8, leading=10, textColor=GRIS),
    "titulo": ParagraphStyle("t", parent=_base["Title"], fontSize=16, leading=19, textColor=AZUL, alignment=0),
    "empresa": ParagraphStyle("e", parent=_base["Normal"], fontSize=13, leading=16, fontName="Helvetica-Bold"),
    "der": ParagraphStyle("d", parent=_base["Normal"], fontSize=9, alignment=2),
}


def _p(texto, estilo="normal"):
    """Párrafo ReportLab con el estilo indicado (escapa caracteres especiales)."""
    texto = str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(texto, ESTILO[estilo])


def _encabezado(titulo, numero):
    """Bloque superior con logo y datos de la empresa a la izquierda."""
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

    empresa = [
        _p(get_config("empresa_nombre", "Mi Empresa"), "empresa"),
        _p(f"RIF: {get_config('empresa_rif')}" if get_config("empresa_rif") else "", "pequeno"),
        _p(get_config("empresa_direccion"), "pequeno"),
        _p(get_config("empresa_telefono"), "pequeno"),
    ]
    izquierda = Table([[logo, empresa]], colWidths=[30 * mm, 80 * mm], hAlign="CENTER")
    izquierda.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    estilo_titulo_derecha = ParagraphStyle(
        "encabezado_titulo_derecha",
        parent=ESTILO["titulo"],
        alignment=2
    )
    derecha = Table([
        [Paragraph(titulo, estilo_titulo_derecha)],
        [_p(numero, "der")],
    ], colWidths=[80 * mm], hAlign="CENTER")
    derecha.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    encabezado = Table([[izquierda, derecha]], colWidths=[110 * mm, 80 * mm], hAlign="CENTER")
    encabezado.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, AZUL),
    ]))
    return encabezado


# MODIFICA ESTA FUNCIÓN COMPLETA:
def _encabezado_nota(titulo, numero):
    """Encabezado corporativo con logo y título dinámico."""
    logo_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "static",
        "logo_pdf.png"
    )

    if os.path.isfile(logo_path):
        logo = Image(
            logo_path,
            width=28 * mm,
            height=23 * mm,
            kind="proportional"
        )
        logo.hAlign = "LEFT"
    else:
        logo = Spacer(28 * mm, 20 * mm)

    empresa = [
        _p(get_config("empresa_nombre", "FT Import"), "empresa"),
        _p(
            f"RIF: {get_config('empresa_rif')}"
            if get_config("empresa_rif") else "",
            "pequeno"
        ),
        _p(get_config("empresa_direccion") or "", "pequeno"),
        _p(get_config("empresa_telefono") or "", "pequeno"),
    ]

    izquierda = Table(
        [[logo, empresa]],
        colWidths=[30 * mm, 80 * mm],
        hAlign="CENTER"
    )
    izquierda.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    estilo_titulo_derecha = ParagraphStyle(
        "nota_titulo_derecha",
        parent=ESTILO["titulo"],
        alignment=2
    )

    # AQUÍ SE USA EL TÍTULO QUE LE PASES POR PARÁMETRO:
    derecha = Table([
        [Paragraph(titulo, estilo_titulo_derecha)],
        [_p(f"Número: {numero}" if numero else "", "der")],
    ], colWidths=[80 * mm], hAlign="CENTER")

    derecha.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    encabezado = Table(
        [[izquierda, derecha]],
        colWidths=[110 * mm, 80 * mm],
        hAlign="CENTER"
    )
    encabezado.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, AZUL),
    ]))
    return encabezado


def _tabla(datos, anchos, alinear_der=(), cabecera=True):
    """Tabla con cabecera azul y filas con líneas suaves."""
    t = Table(datos, colWidths=anchos, repeatRows=1 if cabecera else 0, hAlign="CENTER")
    estilo = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if cabecera:
        estilo += [("BACKGROUND", (0, 0), (-1, 0), AZUL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                   ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    for col in alinear_der:
        estilo.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(estilo))
    return t


def _construir(elementos):
    """Arma el PDF en memoria y devuelve los bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm)
    doc.build(elementos, canvasmaker=CanvasEscalaImpresion)
    return buf.getvalue()


def _firmas():
    """Líneas de firma al pie del documento."""
    t = Table([[_p("_______________________"), _p("_______________________")],
               [_p("Vendedor", "pequeno"), _p("Recibí conforme (cliente)", "pequeno")]],
              colWidths=[95 * mm, 95 * mm], hAlign="CENTER")
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    return t


# ============================================================ nota de venta
def nota_venta(venta_id):
    """PDF de una venta: datos del cliente, detalle, totales y condiciones de pago."""
    v = query(
        """SELECT v.*, c.nombre AS cliente, c.documento, c.telefono, c.direccion, u.nombre AS vendedor
             FROM ventas v JOIN clientes c ON c.id = v.cliente_id JOIN usuarios u ON u.id = v.vendedor_id
            WHERE v.id = ?""", (venta_id,), one=True)
    if v is None:
        return None
    det = query(
        """SELECT d.*, p.codigo, p.nombre, p.unidad FROM venta_detalles d
             JOIN productos p ON p.id = d.producto_id WHERE d.venta_id = ? ORDER BY d.id""", (venta_id,))
    m = "USD"   # todo se factura en dólares
    el = [_encabezado_nota("NOTA DE ENTREGA", v["numero"]), Spacer(1, 10)]

    info = [
        [_p(f"Fecha: {fmt_fechahora(v['fecha'])}"), _p(f"Vendedor: {v['vendedor']}")],
        [_p(f"Cliente: {v['cliente']}"), _p(f"RIF: {v['documento'] or '—'}")],
        [_p(f"Dirección: {v['direccion'] or '—'}"), _p(f"Teléfono: {v['telefono'] or '—'}")],
    ]
    ti = Table(info, colWidths=[95 * mm, 95 * mm], hAlign="CENTER")
    ti.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, -1), GRIS_CLARO),
    ]))
    el += [ti, Spacer(1, 10)]

    filas = [["Código", "Producto", "Cant.", f"Precio ({m})", f"Subtotal ({m})"]]
    for d in det:
        filas.append([d["codigo"], _p(d["nombre"]), f"{fmt_qty(d['cantidad'])} {d['unidad']}",
                      fmt_num(d["precio_unit"]), fmt_num(d["subtotal"])])
    el.append(
        _tabla(
            filas,
            [27 * mm, 72 * mm, 26 * mm, 32 * mm, 33 * mm],
            alinear_der=(2, 3, 4)
        )
    )
    el.append(Spacer(1, 8))

    tot = [["Subtotal", fmt_money(v["subtotal"], m)]]
    if v["descuento"]:
        tot.append(["Descuento", "- " + fmt_money(v["descuento"], m)])
    tot.append(["TOTAL", fmt_money(v["total"], m)])
    tt = Table(tot, colWidths=[45 * mm, 45 * mm], hAlign="RIGHT")
    fila_total = 2 if v["descuento"] else 1
    tt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEABOVE", (0, fila_total), (-1, fila_total), 1, AZUL),
        ("BACKGROUND", (0, fila_total), (-1, fila_total), colors.HexColor("#eaf3fb")),
        ("FONTNAME", (0, fila_total), (-1, fila_total), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, fila_total), (-1, fila_total), AZUL),
    ]))
    el.append(tt)

    if v["tipo_pago"] == "credito":
        d = query("SELECT * FROM v_deudas WHERE venta_id = ?", (venta_id,), one=True)
        if d and d["estado"] != "anulada":
            el += [Spacer(1, 8),
                   Paragraph(f'<font color="#fa0303">Fecha de vencimiento:</font> {fmt_fecha(d["vencimiento"])}', ESTILO["pequeno"])]
                   
    if v["nota"]:
        el += [Spacer(1, 6), _p(f"Nota: {v['nota']}", "pequeno")]
    if v["motivo"]:
        el += [Spacer(1, 4), _p(f"Motivo: {v['motivo']}", "pequeno")]
        
    el += [Spacer(1, 22), _firmas()]
    return _construir(el)

# ========================================================== recibo de abono
# ========================================================== recibo de abono
def recibo_abono(abono_id):
    """PDF de un abono: monto pagado, equivalencias y saldo restante de la deuda."""
    a = query(
        """SELECT a.*, d.concepto, d.monto_usd AS deuda_total, d.cliente_id, c.nombre AS cliente,
                  c.documento, u.nombre AS vendedor, r.nombre AS recibido_por
             FROM abonos a JOIN deudas d ON d.id = a.deuda_id
             JOIN clientes c ON c.id = d.cliente_id JOIN usuarios u ON u.id = d.vendedor_id
             LEFT JOIN usuarios r ON r.id = a.registrado_por
            WHERE a.id = ?""", (abono_id,), one=True)
    if a is None:
        return None
    saldo = query("SELECT saldo_usd FROM v_deudas WHERE id = ?", (a["deuda_id"],), one=True)["saldo_usd"]
    
    # Se usa el encabezado corporativo con el logo y el título RECIBO DE PAGO
    el = [_encabezado_nota("RECIBO DE PAGO", f"R-{abono_id:06d}"), Spacer(1, 10)]
    
    if a["anulado"]:
        el += [_p("*** ESTE ABONO FUE ANULADO ***", "empresa"), Spacer(1, 6)]
        
    filas = [
        ["Fecha", fmt_fechahora(a["fecha"])],
        ["Cliente", f"{a['cliente']}  ({a['documento'] or 'sin documento'})"],
        ["Vendedor", a["vendedor"]],
        ["Concepto", f"{a['concepto']} (deuda #{a['deuda_id']})"],
        ["Método de pago", a["metodo"] or "—"],
        ["Referencia", a["referencia"] or "—"],
        ["Monto recibido", fmt_money(a["monto_usd"], "USD")],
        ["Total de la deuda", fmt_money(a["deuda_total"], "USD")],
        ["Saldo restante", fmt_money(max(saldo, 0), "USD")],
    ]
    t = _tabla(filas, [53 * mm, 137 * mm], cabecera=False)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), GRIS_CLARO), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("FONTNAME", (0, 6), (-1, 6), "Helvetica-Bold")]))
    el += [t, Spacer(1, 40), _firmas()]
    return _construir(el)


# =========================================================== estado de cuenta
# =========================================================== estado de cuenta
def estado_cuenta(cliente_id):
    """PDF con todas las deudas activas de un cliente, sus abonos y el saldo total."""
    c = query("SELECT c.*, u.nombre AS vendedor FROM clientes c JOIN usuarios u ON u.id = c.vendedor_id WHERE c.id = ?",
              (cliente_id,), one=True)
    if c is None:
        return None
    deudas = query("SELECT * FROM v_deudas WHERE cliente_id = ? AND estado IN ('activa','pagada') ORDER BY fecha",
                   (cliente_id,))

    # Obtener la última fecha de vencimiento de compra (activa o pagada)
    ultima_venc = query(
        "SELECT MAX(vencimiento) AS ultima FROM v_deudas WHERE cliente_id = ? AND estado IN ('activa','pagada')",
        (cliente_id,), one=True
    )
    fec_str = fmt_fecha(ultima_venc["ultima"]) if ultima_venc and ultima_venc["ultima"] else "—"

    # Encabezado corporativo con logo y mismo orden de campos
    el = [_encabezado_nota("ESTADO DE CUENTA", f"#{cliente_id}"), Spacer(1, 10)]

    info = [
        [_p(f"Fecha: {fec_str}"), _p(f"Vendedor: {c['vendedor']}")],
        [_p(f"Cliente: {c['nombre']}"), _p(f"RIF: {c['documento'] or '—'}")],
        [_p(f"Dirección: {c['direccion'] or '—'}"), _p(f"Teléfono: {c['telefono'] or '—'}")],
    ]
    ti = Table(info, colWidths=[95 * mm, 95 * mm], hAlign="CENTER")
    ti.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, -1), GRIS_CLARO),
    ]))
    el += [ti, Spacer(1, 10)]

    filas = [["Fecha", "Concepto", "Vence", "Monto $", "Abonado $", "Saldo $", "Estado"]]
    total = 0
    etiquetas = {"pagada": "Pagada", "vencida": "VENCIDA", "por_vencer": "Por vencer", "al_dia": "Al día"}
    for d in deudas:
        total += d["saldo_usd"] if d["estado"] == "activa" else 0
        filas.append([fmt_fecha(d["fecha"]), _p(d["concepto"]), fmt_fecha(d["vencimiento"]), fmt_num(d["monto_usd"]),
                      fmt_num(d["abonado_usd"]), fmt_num(max(d["saldo_usd"], 0)), etiquetas.get(estado_vencimiento(d), "")])
    if len(filas) == 1:
        filas.append(["—", "Sin deudas registradas", "", "", "", "", ""])
    el.append(_tabla(filas, [23 * mm, 55 * mm, 23 * mm, 23 * mm, 23 * mm, 21 * mm, 22 * mm], alinear_der=(3, 4, 5)))
    el += [Spacer(1, 8),
           _p(f"SALDO TOTAL PENDIENTE: {fmt_money(total, 'USD')}", "empresa")]
    return _construir(el)


# ============================================================ lista de mercancía
def lista_mercancia(productos):
    """PDF en lista de la mercancía: código, producto, categoría, unidad y precio al mayor.

    A propósito NO incluye precios ni cantidades en stock."""
    datos = [["Código", "Producto", "Categoría", "Unidad", "Precio mayor"]]
    for p in productos:
        datos.append([_p(p["codigo"]), _p(p["nombre"]), _p(p["categoria"] or "—"), _p(p["unidad"]),
                      _p(f"$ {fmt_num(p['precio_mayor_usd'])}", "der")])
    elementos = [_encabezado("Lista de mercancía", fmt_fecha(ahora())), Spacer(1, 6 * mm)]
    if len(datos) == 1:
        elementos.append(_p("No hay productos registrados."))
    else:
        elementos.append(_tabla(datos, [30 * mm, 74 * mm, 38 * mm, 19 * mm, 29 * mm], alinear_der=(4,)))
    return _construir(elementos)
