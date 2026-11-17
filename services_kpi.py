"""Cálculos de ventas, cobros y rankings para los KPI mensuales."""
import re
import unicodedata
from collections import defaultdict

from db import query


_COLOR_NOMBRES = {
    "azul": "Azul", "azules": "Azul", "blue": "Azul",
    "blanco": "Blanco", "blanca": "Blanco", "white": "Blanco",
    "negro": "Negro", "negra": "Negro", "black": "Negro",
    "rojo": "Rojo", "roja": "Rojo", "red": "Rojo",
    "verde": "Verde", "green": "Verde",
    "amarillo": "Amarillo", "amarilla": "Amarillo", "yellow": "Amarillo",
    "rosa": "Rosado", "rosado": "Rosado", "rosada": "Rosado", "pink": "Rosado",
    "fucsia": "Fucsia", "magenta": "Fucsia",
    "morado": "Morado", "morada": "Morado", "purple": "Morado", "violeta": "Violeta",
    "naranja": "Naranja", "orange": "Naranja",
    "gris": "Gris", "gray": "Gris", "grey": "Gris",
    "marron": "Marrón", "cafe": "Marrón", "brown": "Marrón",
    "beige": "Beige", "turquesa": "Turquesa", "cyan": "Turquesa",
    "dorado": "Dorado", "dorada": "Dorado", "gold": "Dorado",
    "plateado": "Plateado", "plateada": "Plateado", "silver": "Plateado",
    "transparente": "Transparente", "clear": "Transparente",
    "multicolor": "Multicolor",
}

_COLOR_CODIGOS = {
    "az": "Azul", "azl": "Azul", "bl": "Blanco", "bco": "Blanco",
    "ng": "Negro", "ne": "Negro", "neg": "Negro", "ro": "Rojo",
    "roj": "Rojo", "ve": "Verde", "vd": "Verde", "ver": "Verde",
    "am": "Amarillo", "ama": "Amarillo", "rs": "Rosado", "ros": "Rosado",
    "fu": "Fucsia", "mo": "Morado", "mor": "Morado", "vi": "Violeta",
    "na": "Naranja", "nar": "Naranja", "gr": "Gris", "gri": "Gris",
    "ma": "Marrón", "mar": "Marrón", "be": "Beige", "tur": "Turquesa",
    "do": "Dorado", "dor": "Dorado", "pl": "Plateado", "pla": "Plateado",
}

_COLOR_HEX = {
    "Azul": "#2563eb", "Blanco": "#f8fafc", "Negro": "#111827",
    "Rojo": "#dc2626", "Verde": "#16a34a", "Amarillo": "#eab308",
    "Rosado": "#ec4899", "Fucsia": "#d946ef", "Morado": "#8b5cf6",
    "Violeta": "#7c3aed", "Naranja": "#f97316", "Gris": "#6b7280",
    "Marrón": "#78350f", "Beige": "#d6c3a1", "Turquesa": "#14b8a6",
    "Dorado": "#d4af37", "Plateado": "#c0c0c0", "Transparente": "#e5e7eb",
    "Multicolor": "#6366f1", "Producto": "#6b7280",
}


def _tokens(texto):
    normalizado = unicodedata.normalize("NFKD", str(texto or "").casefold())
    sin_acentos = "".join(
        caracter for caracter in normalizado if not unicodedata.combining(caracter)
    )
    return re.findall(r"[a-z0-9]+", sin_acentos)


def identificar_color(nombre, codigo):
    """Infere el color del nombre; usa el código solo si el nombre no lo indica."""
    for token in _tokens(nombre):
        if token in _COLOR_NOMBRES:
            return _COLOR_NOMBRES[token]
    for token in _tokens(codigo):
        if token in _COLOR_NOMBRES:
            return _COLOR_NOMBRES[token]
        if token in _COLOR_CODIGOS:
            return _COLOR_CODIGOS[token]
    return "Producto"


def _marcar_extremos(filas, campo, sufijo=""):
    if not filas:
        return
    maximo = max(float(fila[campo]) for fila in filas)
    if maximo <= 0:
        return
    minimo = min(float(fila[campo]) for fila in filas)
    clave_mas = f"mas_{sufijo}" if sufijo else "mas"
    clave_menos = f"menos_{sufijo}" if sufijo else "menos"
    for fila in filas:
        fila[clave_mas] = float(fila[campo]) == maximo
        fila[clave_menos] = float(fila[campo]) == minimo


def _estrella(filas, campo, nombre_campo="nombre"):
    if not filas:
        return None
    maximo = max(float(fila[campo]) for fila in filas)
    if maximo <= 0:
        return None
    ganadores = sorted(
        (fila for fila in filas if float(fila[campo]) == maximo),
        key=lambda fila: str(fila[nombre_campo]).casefold(),
    )
    return {
        "nombre": ", ".join(str(fila[nombre_campo]) for fila in ganadores),
        "monto": round(maximo, 2),
    }


def resumen_kpis_mensuales(desde, hasta):
    """Devuelve cifras de ventas aprobadas y dinero recibido en el rango indicado."""
    productos = {}
    for fila in query("SELECT id, codigo, nombre FROM productos ORDER BY codigo"):
        color = identificar_color(fila["nombre"], fila["codigo"])
        productos[fila["id"]] = {
            "id": fila["id"],
            "codigo": fila["codigo"],
            "nombre": fila["nombre"],
            "color": color,
            "color_hex": _COLOR_HEX.get(color, _COLOR_HEX["Producto"]),
            "unidades": 0.0,
            "vendido_usd": 0.0,
            "cobrado_usd": 0.0,
            "ventas_detalle": [],
        }

    vendedores = {
        fila["id"]: {
            "id": fila["id"], "nombre": fila["nombre"], "ventas": 0,
            "unidades": 0.0, "vendido_usd": 0.0, "cobrado_usd": 0.0,
        }
        for fila in query("SELECT id, nombre FROM usuarios WHERE rol = 'vendedor' ORDER BY nombre")
    }
    clientes = {
        fila["id"]: {
            "id": fila["id"], "nombre": fila["nombre"], "ventas": 0,
            "unidades": 0.0, "vendido_usd": 0.0, "cobrado_usd": 0.0,
        }
        for fila in query("SELECT id, nombre FROM clientes ORDER BY nombre")
    }

    ventas = query(
        """SELECT vendedor_id, cliente_id, COUNT(*) AS n_ventas,
                  COALESCE(SUM(total_usd), 0) AS vendido_usd
             FROM ventas
            WHERE estado = 'aprobada' AND substr(fecha, 1, 10) BETWEEN ? AND ?
            GROUP BY vendedor_id, cliente_id""",
        (desde, hasta),
    )
    total_ventas = 0
    vendido_total = 0.0
    for fila in ventas:
        cantidad = int(fila["n_ventas"] or 0)
        monto = float(fila["vendido_usd"] or 0)
        total_ventas += cantidad
        vendido_total += monto
        if fila["vendedor_id"] in vendedores:
            vendedores[fila["vendedor_id"]]["ventas"] += cantidad
            vendedores[fila["vendedor_id"]]["vendido_usd"] += monto
        if fila["cliente_id"] in clientes:
            clientes[fila["cliente_id"]]["ventas"] += cantidad
            clientes[fila["cliente_id"]]["vendido_usd"] += monto

    lineas = query(
        """SELECT v.vendedor_id, v.cliente_id, vd.producto_id,
                  SUM(vd.cantidad) AS unidades,
                  SUM(CASE WHEN totales.subtotal_lineas > 0
                           THEN v.total_usd * vd.subtotal / totales.subtotal_lineas
                           ELSE 0 END) AS vendido_usd
             FROM ventas v
             JOIN venta_detalles vd ON vd.venta_id = v.id
             JOIN (SELECT venta_id, SUM(subtotal) AS subtotal_lineas
                     FROM venta_detalles GROUP BY venta_id) totales ON totales.venta_id = v.id
            WHERE v.estado = 'aprobada' AND substr(v.fecha, 1, 10) BETWEEN ? AND ?
            GROUP BY v.vendedor_id, v.cliente_id, vd.producto_id""",
        (desde, hasta),
    )
    ventas_detalle_producto = query(
        """SELECT vd.producto_id, v.id AS venta_id,
                  COALESCE(NULLIF(v.numero, ''), 'V-' || CAST(v.id AS TEXT)) AS numero,
                  substr(v.fecha, 1, 10) AS fecha, u.nombre AS vendedor, c.nombre AS cliente,
                  SUM(vd.cantidad) AS unidades,
                  SUM(CASE WHEN totales.subtotal_lineas > 0
                           THEN v.total_usd * vd.subtotal / totales.subtotal_lineas
                           ELSE 0 END) AS vendido_usd
             FROM venta_detalles vd
             JOIN ventas v ON v.id = vd.venta_id
             JOIN usuarios u ON u.id = v.vendedor_id
             JOIN clientes c ON c.id = v.cliente_id
             JOIN (SELECT venta_id, SUM(subtotal) AS subtotal_lineas
                     FROM venta_detalles GROUP BY venta_id) totales ON totales.venta_id = v.id
            WHERE v.estado = 'aprobada' AND substr(v.fecha, 1, 10) BETWEEN ? AND ?
            GROUP BY vd.producto_id, v.id, v.numero, v.fecha, u.nombre, c.nombre
            ORDER BY v.fecha DESC, v.id DESC""",
        (desde, hasta),
    )
    for fila in ventas_detalle_producto:
        if fila["producto_id"] in productos:
            productos[fila["producto_id"]]["ventas_detalle"].append(
                {
                    "fecha": fila["fecha"],
                    "numero": fila["numero"],
                    "vendedor": fila["vendedor"],
                    "cliente": fila["cliente"],
                    "unidades": round(float(fila["unidades"] or 0), 3),
                    "vendido_usd": round(float(fila["vendido_usd"] or 0), 2),
                }
            )
    ventas_producto_vendedor = defaultdict(float)
    ventas_detalle_vendedor = defaultdict(dict)
    for fila in lineas:
        pid = fila["producto_id"]
        vendedor_id = fila["vendedor_id"]
        cliente_id = fila["cliente_id"]
        unidades = float(fila["unidades"] or 0)
        monto = float(fila["vendido_usd"] or 0)
        if pid in productos:
            productos[pid]["unidades"] += unidades
            productos[pid]["vendido_usd"] += monto
        ventas_producto_vendedor[(vendedor_id, pid)] += unidades
        if vendedor_id in vendedores:
            vendedores[vendedor_id]["unidades"] += unidades
        if cliente_id in clientes:
            clientes[cliente_id]["unidades"] += unidades
        if vendedor_id in vendedores and cliente_id in clientes and pid in productos:
            detalle_producto = ventas_detalle_vendedor[vendedor_id].setdefault(
                pid,
                {
                    "id": pid,
                    "nombre": productos[pid]["nombre"],
                    "color": productos[pid]["color"],
                    "color_hex": productos[pid]["color_hex"],
                    "unidades": 0.0,
                    "vendido_usd": 0.0,
                    "clientes": {},
                },
            )
            detalle_producto["unidades"] += unidades
            detalle_producto["vendido_usd"] += monto
            detalle_cliente = detalle_producto["clientes"].setdefault(
                cliente_id,
                {
                    "nombre": clientes[cliente_id]["nombre"],
                    "unidades": 0.0,
                    "vendido_usd": 0.0,
                },
            )
            detalle_cliente["unidades"] += unidades
            detalle_cliente["vendido_usd"] += monto

    cobros = query(
        """SELECT vendedor_id, cliente_id, SUM(monto_usd) AS cobrado_usd
             FROM (
                 SELECT v.vendedor_id, v.cliente_id, v.total_usd AS monto_usd
                   FROM ventas v
                  WHERE v.estado = 'aprobada' AND v.tipo_pago = 'contado' AND v.pagada = 1
                    AND substr(COALESCE(v.fecha_aprobacion, v.fecha), 1, 10) BETWEEN ? AND ?
                 UNION ALL
                 SELECT d.vendedor_id, d.cliente_id, a.monto_usd AS monto_usd
                   FROM abonos a JOIN deudas d ON d.id = a.deuda_id
                  WHERE a.anulado = 0 AND substr(a.fecha, 1, 10) BETWEEN ? AND ?
             ) pagos
            GROUP BY vendedor_id, cliente_id""",
        (desde, hasta, desde, hasta),
    )
    cobrado_total = 0.0
    for fila in cobros:
        monto = float(fila["cobrado_usd"] or 0)
        cobrado_total += monto
        if fila["vendedor_id"] in vendedores:
            vendedores[fila["vendedor_id"]]["cobrado_usd"] += monto
        if fila["cliente_id"] in clientes:
            clientes[fila["cliente_id"]]["cobrado_usd"] += monto

    cobros_productos = query(
        """SELECT pagos.vendedor_id, lineas.producto_id,
                  SUM(CASE WHEN totales.subtotal_lineas > 0
                           THEN pagos.monto_usd * lineas.subtotal / totales.subtotal_lineas
                           ELSE 0 END) AS cobrado_usd
             FROM (
                 SELECT v.vendedor_id, v.id AS venta_id, v.total_usd AS monto_usd
                   FROM ventas v
                  WHERE v.estado = 'aprobada' AND v.tipo_pago = 'contado' AND v.pagada = 1
                    AND substr(COALESCE(v.fecha_aprobacion, v.fecha), 1, 10) BETWEEN ? AND ?
                 UNION ALL
                 SELECT d.vendedor_id, d.venta_id, a.monto_usd
                   FROM abonos a JOIN deudas d ON d.id = a.deuda_id
                  WHERE a.anulado = 0 AND d.venta_id IS NOT NULL
                    AND substr(a.fecha, 1, 10) BETWEEN ? AND ?
             ) pagos
             JOIN venta_detalles lineas ON lineas.venta_id = pagos.venta_id
             JOIN (SELECT venta_id, SUM(subtotal) AS subtotal_lineas
                     FROM venta_detalles GROUP BY venta_id) totales ON totales.venta_id = pagos.venta_id
            GROUP BY pagos.vendedor_id, lineas.producto_id""",
        (desde, hasta, desde, hasta),
    )
    cobros_por_producto_vendedor = defaultdict(float)
    for fila in cobros_productos:
        if fila["producto_id"] in productos:
            monto = float(fila["cobrado_usd"] or 0)
            productos[fila["producto_id"]]["cobrado_usd"] += monto
            cobros_por_producto_vendedor[(fila["vendedor_id"], fila["producto_id"])] += monto

    for fila in productos.values():
        fila["unidades"] = round(fila["unidades"], 3)
        fila["vendido_usd"] = round(fila["vendido_usd"], 2)
        fila["cobrado_usd"] = round(fila["cobrado_usd"], 2)
    productos_lista = sorted(
        productos.values(),
        key=lambda fila: (-fila["unidades"], -fila["vendido_usd"], fila["codigo"].casefold()),
    )
    productos_vendidos = [fila for fila in productos_lista if fila["unidades"] > 0]
    _marcar_extremos(productos_vendidos, "unidades")

    ventas_por_producto = defaultdict(list)
    for (vendedor_id, producto_id), unidades in ventas_producto_vendedor.items():
        if unidades > 0 and producto_id in productos:
            ventas_por_producto[vendedor_id].append((productos[producto_id], unidades))
    productos_cobrados_vendedor = defaultdict(list)
    for (vendedor_id, producto_id), monto in cobros_por_producto_vendedor.items():
        if monto > 0 and producto_id in productos:
            productos_cobrados_vendedor[vendedor_id].append((productos[producto_id], monto))

    for vendedor in vendedores.values():
        detalle_productos = []
        for producto_id, detalle in ventas_detalle_vendedor.get(vendedor["id"], {}).items():
            detalle["unidades"] = round(detalle["unidades"], 3)
            detalle["vendido_usd"] = round(detalle["vendido_usd"], 2)
            detalle["cobrado_usd"] = round(
                cobros_por_producto_vendedor.get((vendedor["id"], producto_id), 0.0), 2
            )
            detalle["clientes"] = sorted(
                (
                    {
                        "nombre": cliente["nombre"],
                        "unidades": round(cliente["unidades"], 3),
                        "vendido_usd": round(cliente["vendido_usd"], 2),
                    }
                    for cliente in detalle["clientes"].values()
                ),
                key=lambda fila: (-fila["unidades"], -fila["vendido_usd"], fila["nombre"].casefold()),
            )
            detalle_productos.append(detalle)
        vendedor["productos_detalle"] = sorted(
            detalle_productos,
            key=lambda fila: (-fila["unidades"], -fila["vendido_usd"], fila["nombre"].casefold()),
        )
        vendedor["productos_cobros_detalle"] = sorted(
            (
                {
                    "nombre": producto["nombre"],
                    "color": producto["color"],
                    "color_hex": producto["color_hex"],
                    "cobrado_usd": round(monto, 2),
                }
                for producto, monto in productos_cobrados_vendedor.get(vendedor["id"], ())
            ),
            key=lambda fila: (-fila["cobrado_usd"], fila["nombre"].casefold()),
        )

    for vendedor in vendedores.values():
        vendidos = ventas_por_producto.get(vendedor["id"], [])
        if vendidos:
            maximo = max(unidades for _, unidades in vendidos)
            minimo = min(unidades for _, unidades in vendidos)
            vendedor["producto_mas"] = ", ".join(
                sorted((p["nombre"] for p, unidades in vendidos if unidades == maximo), key=str.casefold)
            )
            vendedor["producto_mas_unidades"] = round(maximo, 3)
            vendedor["producto_menos"] = ", ".join(
                sorted((p["nombre"] for p, unidades in vendidos if unidades == minimo), key=str.casefold)
            )
            vendedor["producto_menos_unidades"] = round(minimo, 3)
        else:
            vendedor["producto_mas"] = "Sin ventas"
            vendedor["producto_menos"] = "Sin ventas"
            vendedor["producto_mas_unidades"] = 0.0
            vendedor["producto_menos_unidades"] = 0.0
        cobrados = productos_cobrados_vendedor.get(vendedor["id"], [])
        if cobrados and any(monto > 0 for _, monto in cobrados):
            maximo = max(monto for _, monto in cobrados)
            minimo = min(monto for _, monto in cobrados)
            vendedor["producto_mas_cobrado"] = ", ".join(
                sorted((p["nombre"] for p, monto in cobrados if monto == maximo), key=str.casefold)
            )
            vendedor["producto_mas_cobrado_usd"] = round(maximo, 2)
            vendedor["producto_menos_cobrado"] = ", ".join(
                sorted((p["nombre"] for p, monto in cobrados if monto == minimo), key=str.casefold)
            )
            vendedor["producto_menos_cobrado_usd"] = round(minimo, 2)
        else:
            vendedor["producto_mas_cobrado"] = "Sin cobros atribuibles"
            vendedor["producto_menos_cobrado"] = "Sin cobros atribuibles"
            vendedor["producto_mas_cobrado_usd"] = 0.0
            vendedor["producto_menos_cobrado_usd"] = 0.0

    for fila in list(vendedores.values()) + list(clientes.values()):
        fila["unidades"] = round(fila["unidades"], 3)
        fila["vendido_usd"] = round(fila["vendido_usd"], 2)
        fila["cobrado_usd"] = round(fila["cobrado_usd"], 2)

    vendedores_ventas = sorted(
        (fila for fila in vendedores.values() if fila["ventas"] > 0),
        key=lambda fila: (-fila["vendido_usd"], fila["nombre"].casefold()),
    )
    vendedores_cobros = sorted(
        (fila for fila in vendedores.values() if fila["cobrado_usd"] > 0),
        key=lambda fila: (-fila["cobrado_usd"], fila["nombre"].casefold()),
    )
    clientes_ventas = sorted(
        (fila for fila in clientes.values() if fila["ventas"] > 0),
        key=lambda fila: (-fila["vendido_usd"], fila["nombre"].casefold()),
    )
    clientes_cobros = sorted(
        (fila for fila in clientes.values() if fila["cobrado_usd"] > 0),
        key=lambda fila: (-fila["cobrado_usd"], fila["nombre"].casefold()),
    )
    _marcar_extremos(vendedores_ventas, "vendido_usd", "ventas")
    _marcar_extremos(vendedores_cobros, "cobrado_usd", "cobros")
    _marcar_extremos(clientes_ventas, "vendido_usd", "compras")
    _marcar_extremos(clientes_cobros, "cobrado_usd", "pagos")

    resumen = {
        "ventas": total_ventas,
        "productos": len(productos_lista),
        "productos_vendidos": sum(1 for fila in productos_lista if fila["unidades"] > 0),
        "unidades": round(sum(fila["unidades"] for fila in productos_lista), 3),
        "vendido_usd": round(vendido_total, 2),
        "cobrado_usd": round(cobrado_total, 2),
    }
    return {
        "resumen": resumen,
        "productos": productos_vendidos,
        "vendedores_ventas": vendedores_ventas,
        "vendedores_cobros": vendedores_cobros,
        "clientes_ventas": clientes_ventas,
        "clientes_cobros": clientes_cobros,
        "estrellas": {
            "vendedor": _estrella(vendedores_cobros, "cobrado_usd"),
            "producto": _estrella(productos_lista, "cobrado_usd"),
            "cliente": _estrella(clientes_cobros, "cobrado_usd"),
        },
    }
