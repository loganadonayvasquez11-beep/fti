"""Lógica de negocio: ventas, aprobación, stock, deudas y abonos.

Todas las funciones que modifican datos lanzan ValueError con un mensaje
en español cuando una regla de negocio no se cumple; las rutas lo muestran al usuario.
"""
from flask import g

from db import get_db, query
from datetime import datetime, timedelta

from utils import ahora, hoy, parse_decimal_input, sumar_dias, to_float, to_int


# =================================================================== config
def get_config(clave, defecto=""):
    """Lee un parámetro de la tabla de configuración."""
    fila = query("SELECT valor FROM configuracion WHERE clave = ?", (clave,), one=True)
    return fila["valor"] if fila and fila["valor"] is not None else defecto


def set_config(clave, valor):
    """Guarda (o actualiza) un parámetro de configuración."""
    db = get_db()
    db.execute(
        "INSERT INTO configuracion(clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, str(valor)),
    )
    db.commit()


def tasa_actual():
    """El sistema factura solo en dólares: no hay conversión, la tasa siempre es 1."""
    return 1.0


def audit(accion, detalle=""):
    """Registra una acción en la auditoría con el usuario actual y guarda de inmediato."""
    db = get_db()
    uid = g.user["id"] if g.get("user") else None
    db.execute(
        "INSERT INTO auditoria (fecha, usuario_id, accion, detalle) VALUES (?,?,?,?)",
        (ahora(), uid, accion, detalle),
    )
    db.commit()


def a_usd(monto, moneda, tasa):
    """Todo se maneja en dólares: solo redondea el monto (moneda y tasa se conservan por compatibilidad)."""
    return round(monto, 2)


# ============================================================ reglas de vendedor
def msg_sin_stock(vendedor_id, nombre, stock):
    """Mensaje de falta de existencia. El vendedor normal NO ve cantidades: solo si se agotó o no alcanza."""
    if es_vendedor_especial(vendedor_id):
        return f"Stock insuficiente de «{nombre}» (disponible: {stock:g})."
    if stock <= 0:
        return f"«{nombre}» se agotó: no hay más. Elige otro producto para continuar con la compra."
    return f"«{nombre}» no tiene existencias suficientes para esa cantidad. Reduce la cantidad o elige otro producto."


def es_vendedor_especial(vendedor_id):
    """True si el vendedor especial puede aprobar ventas y armar pedidos."""
    fila = query("SELECT tipo_vendedor FROM usuarios WHERE id = ?", (vendedor_id,), one=True)
    return bool(fila) and fila["tipo_vendedor"] == "especial"


def precio_lista(prod):
    """Precio base mayor de un producto en dólares."""
    return round(prod["precio_mayor_usd"], 2)


def resumen_venta(vendedor_id, borrador, carrito):
    """Calcula SIN guardar lo que sería la venta (para la pantalla de confirmación).

    borrador: dict con cliente_id, tipo_pago, metodo_pago, cuota_inicial,
              descuento, nota y precios {producto_id: precio escrito a mano}.
    carrito : {producto_id: cantidad}.
    Lanza ValueError si falta algo (cliente, productos).
    """
    db = get_db()
    cliente = db.execute(
        "SELECT * FROM clientes WHERE id = ? AND vendedor_id = ? AND activo = 1",
        (to_int(borrador.get("cliente_id")), vendedor_id)).fetchone()
    if cliente is None:
        raise ValueError("Selecciona un cliente válido de tu cartera.")
    moneda = "USD"
    tipo_pago = borrador.get("tipo_pago", "contado")
    tasa = tasa_actual()
    lineas = []
    for pid, cant in carrito.items():
        prod = db.execute("SELECT * FROM productos WHERE id = ? AND activo = 1", (to_int(pid),)).fetchone()
        if prod is None:
            raise ValueError("Uno de los productos ya no está disponible en el catálogo.")
        cant = to_float(cant)
        if not cant.is_integer() or cant <= 0:
            raise ValueError(f"La cantidad de «{prod['nombre']}» debe ser un número entero mayor que cero.")
        lista = precio_lista(prod)
        precio_guardado = (borrador.get("precios") or {}).get(str(pid), "")
        escrito = "" if precio_guardado is None else str(precio_guardado).strip()
        precio = parse_decimal_input(escrito, "El precio", lista) if escrito else lista
        lineas.append({"producto": prod, "cantidad": cant, "precio_lista": lista, "precio_unit": round(precio, 2),
                       "modificado": abs(precio - lista) > 0.004, "subtotal": round(cant * precio, 2),
                       "excede": cant > prod["stock"] + 1e-9, "agotado": prod["stock"] <= 0})
    if not lineas:
        raise ValueError("Tu carrito está vacío.")
    subtotal = round(sum(l["subtotal"] for l in lineas), 2)
    descuento = round(parse_decimal_input(borrador.get("descuento"), "El descuento", 0), 2)
    total = round(subtotal - descuento, 2)
    total_usd = a_usd(total, moneda, tasa)
    cuota = round(parse_decimal_input(borrador.get("cuota_inicial"), "La cuota inicial", 0), 2) if tipo_pago == "credito" else 0.0
    return {
        "cliente": cliente, "lineas": lineas, "tipo_pago": tipo_pago,
        "moneda": moneda, "tasa": tasa, "subtotal": subtotal, "descuento": descuento, "total": total,
        "total_usd": total_usd, "cuota_inicial": cuota, "metodo_pago": borrador.get("metodo_pago", ""),
        "nota": borrador.get("nota", ""),
        "dias": dias_credito_para(cliente, total_usd) if tipo_pago == "credito" else 0,
        "auto": tipo_pago == "contado" and get_config("contado_requiere_aprobacion", "0") != "1",
    }


def dias_credito_automatico(total_usd):
    """Plazo de pago por defecto según el monto de la venta en USD.

    Menos de 250 $ → 15 días; menos de 600 $ → 20 días; 600 $ o más → 30 días.
    """
    if total_usd < 250:
        return 15
    if total_usd < 600:
        return 20
    return 30


def dias_credito_para(cliente, total_usd):
    """Días de crédito de una venta: los que el admin fijó al cliente, o los automáticos por monto."""
    fijos = to_int(cliente["dias_credito"])
    return fijos if fijos > 0 else dias_credito_automatico(total_usd)


# ===================================================================== stock
def stock_alerta_defecto():
    """Alerta mínima general de stock (se aplica a los productos que no tienen mínimo propio)."""
    return max(to_float(get_config("stock_alerta_defecto", "10"), 10.0), 0.0)


# Mínimo efectivo de un producto: el suyo si lo tiene; si no, la alerta general (parámetro ?).
SQL_MINIMO_EFECTIVO = "COALESCE(NULLIF(stock_minimo, 0), ?)"


def productos_en_alerta(limite=None):
    """Productos activos cuyo stock llegó o bajó de su mínimo de alerta, del más crítico al menos."""
    sql = ("SELECT id, codigo, nombre, stock, unidad, " + SQL_MINIMO_EFECTIVO + " AS minimo "
           "FROM productos WHERE activo = 1 AND stock <= " + SQL_MINIMO_EFECTIVO + " ORDER BY stock ASC")
    d = stock_alerta_defecto()
    args = [d, d]
    if limite:
        sql += " LIMIT ?"
        args.append(limite)
    return query(sql, args)


def mover_stock(db, producto_id, cantidad, tipo, motivo="", venta_id=None, usuario_id=None):
    """Aplica un movimiento al almacén central y lo registra en el kardex.

    cantidad lleva signo: positivo = entra, negativo = sale.
    No hace commit (lo hace quien la llama dentro de su transacción).
    """
    prod = db.execute("SELECT nombre, stock FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if prod is None:
        raise ValueError("Producto no encontrado.")
    nuevo = round(prod["stock"] + cantidad, 4)
    if nuevo < 0:
        raise ValueError(f"Stock insuficiente de «{prod['nombre']}» (disponible: {prod['stock']:g}).")
    db.execute("UPDATE productos SET stock = ? WHERE id = ?", (nuevo, producto_id))
    if tipo == "salida":          # una venta aprobada suma a 'vendidos'
        db.execute("UPDATE productos SET vendidos = vendidos + ? WHERE id = ?", (-cantidad, producto_id))
    elif tipo == "devolucion":    # anular una venta resta de 'vendidos'
        db.execute("UPDATE productos SET vendidos = MAX(vendidos - ?, 0) WHERE id = ?", (cantidad, producto_id))
    db.execute(
        """INSERT INTO movimientos_stock
           (producto_id, fecha, tipo, cantidad, stock_resultante, motivo, venta_id, usuario_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (producto_id, ahora(), tipo, cantidad, nuevo, motivo, venta_id, usuario_id),
    )


def registrar_movimiento_manual(producto_id, tipo, cantidad, motivo, usuario_id):
    """Entrada, ajuste o merma hecha a mano por el admin.

    - entrada: suma la cantidad.
    - merma: resta la cantidad.
    - ajuste: fija el stock a la cantidad indicada (conteo físico).
    """
    db = get_db()
    prod = db.execute("SELECT stock FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if prod is None:
        raise ValueError("Producto no encontrado.")
    if cantidad < 0 or (tipo != "ajuste" and cantidad == 0):
        raise ValueError("La cantidad debe ser mayor que cero.")
    if tipo == "entrada":
        delta = cantidad
    elif tipo == "merma":
        delta = -cantidad
    elif tipo == "ajuste":
        delta = cantidad - prod["stock"]
    else:
        raise ValueError("Tipo de movimiento inválido.")
    with db:
        mover_stock(db, producto_id, delta, tipo, motivo, None, usuario_id)


# ==================================================================== ventas
def saldo_cliente_usd(cliente_id):
    """Deuda viva del cliente (activas + pendientes de aprobación) en USD."""
    fila = query(
        "SELECT COALESCE(SUM(saldo_usd), 0) AS s FROM v_deudas "
        "WHERE cliente_id = ? AND estado IN ('activa', 'pendiente')",
        (cliente_id,), one=True,
    )
    return fila["s"]


def crear_venta(vendedor_id, form, items):
    """Registra una venta nueva hecha por un vendedor.

    form  : dict con cliente_id, tipo_pago, metodo_pago, cuota_inicial,
            descuento y nota (los días de crédito se calculan solos).
    items : lista de dicts {producto_id, cantidad, precio}.

    Reglas:
      - Contado: queda aprobada y pagada al instante y descuenta el stock
        (salvo que la configuración exija aprobación del admin).
      - Crédito: queda PENDIENTE, crea una deuda pendiente y NO toca el stock
        hasta que el admin la apruebe.
    Devuelve el id de la venta creada.
    """
    db = get_db()

    cliente = db.execute(
        "SELECT * FROM clientes WHERE id = ? AND vendedor_id = ? AND activo = 1",
        (to_int(form.get("cliente_id")), vendedor_id),
    ).fetchone()
    if cliente is None:
        raise ValueError("Selecciona un cliente válido de tu cartera.")

    tipo_pago = form.get("tipo_pago", "contado")
    moneda = "USD"
    if tipo_pago not in ("contado", "credito"):
        raise ValueError("Datos de la venta inválidos.")

    tasa = tasa_actual()
    factor = 1.0
    lineas, pedido = [], {}

    for it in items:
        prod = db.execute(
            "SELECT * FROM productos WHERE id = ? AND activo = 1", (to_int(it["producto_id"]),)
        ).fetchone()
        if prod is None:
            raise ValueError("Uno de los productos ya no está disponible en el catálogo.")
        cant = to_float(it["cantidad"])
        if not cant.is_integer() or cant <= 0:
            raise ValueError(f"La cantidad de «{prod['nombre']}» debe ser un número entero mayor que cero.")
        lista = round(prod["precio_mayor_usd"] * factor, 2)
        precio_raw = it.get("precio", "")
        precio_escrito = "" if precio_raw is None else str(precio_raw).strip()
        precio = parse_decimal_input(precio_escrito, "El precio", lista) if precio_escrito else lista
        if precio < 0:
            raise ValueError(f"Precio inválido para «{prod['nombre']}».")
        pedido[prod["id"]] = pedido.get(prod["id"], 0) + cant
        lineas.append({
            "producto_id": prod["id"], "cantidad": cant, "precio_lista": lista,
            "precio_unit": round(precio, 2), "modificado": 1 if abs(precio - lista) > 0.004 else 0,
            "subtotal": round(cant * precio, 2),
        })

    if not lineas:
        raise ValueError("Agrega al menos un producto a la venta.")

    # Se valida que el almacén tenga existencia (aunque en crédito no se descuente aún)
    for pid, cant in pedido.items():
        p = db.execute("SELECT nombre, stock FROM productos WHERE id = ?", (pid,)).fetchone()
        if cant > p["stock"] + 1e-9:
            raise ValueError(msg_sin_stock(vendedor_id, p["nombre"], p["stock"]))

    subtotal = round(sum(l["subtotal"] for l in lineas), 2)
    descuento = round(parse_decimal_input(form.get("descuento"), "El descuento", 0), 2)
    if descuento > subtotal:
        raise ValueError("El descuento no puede ser mayor que el subtotal.")
    total = round(subtotal - descuento, 2)
    total_usd = a_usd(total, moneda, tasa)

    # El plazo de crédito NO lo escribe el vendedor: lo fija el sistema según el monto
    # (o el admin en la ficha del cliente) y solo el admin puede cambiarlo después.
    dias = dias_credito_para(cliente, total_usd) if tipo_pago == "credito" else 0

    # La forma de pago y las condiciones pertenecen a esta venta, no al cliente.
    cuota_inicial = round(parse_decimal_input(form.get("cuota_inicial"), "La cuota inicial", 0), 2) if tipo_pago == "credito" else 0.0
    if cuota_inicial > total + 0.005:
        raise ValueError("La cuota inicial no puede superar el total de la venta.")
    cuota_inicial_usd = a_usd(cuota_inicial, moneda, tasa)
    if cuota_inicial_usd > total_usd + 0.005:
        raise ValueError("La cuota inicial no puede superar el total de la venta.")

    requiere_aprobacion = get_config("contado_requiere_aprobacion", "0") == "1"
    auto = tipo_pago == "contado" and not requiere_aprobacion
    estado = "aprobada" if auto else "pendiente"

    with db:
        cur = db.execute(
            """INSERT INTO ventas
               (fecha, cliente_id, vendedor_id, tipo_pago, dias_credito, moneda, tasa,
                metodo_pago, subtotal, descuento, total, total_usd, estado, pagada, nota,
                fecha_aprobacion)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ahora(), cliente["id"], vendedor_id, tipo_pago, dias, moneda, tasa,
             form.get("metodo_pago", "").strip(), subtotal, descuento, total, total_usd, estado,
             1 if auto else 0, form.get("nota", "").strip(), ahora() if auto else None),
        )
        venta_id = cur.lastrowid
        db.execute("UPDATE ventas SET numero = ? WHERE id = ?", (f"V-{venta_id:06d}", venta_id))
        for l in lineas:
            db.execute(
                """INSERT INTO venta_detalles
                   (venta_id, producto_id, cantidad, precio_lista, precio_unit, modificado, subtotal)
                   VALUES (?,?,?,?,?,?,?)""",
                (venta_id, l["producto_id"], l["cantidad"], l["precio_lista"],
                 l["precio_unit"], l["modificado"], l["subtotal"]),
            )
        if auto:   # contado automático: sale del stock de una vez
            for pid, cant in pedido.items():
                mover_stock(db, pid, -cant, "salida", f"Venta V-{venta_id:06d}", venta_id, vendedor_id)
        if tipo_pago == "credito":   # nace la deuda, pendiente hasta que el admin apruebe
            db.execute(
                """INSERT INTO deudas (cliente_id, vendedor_id, venta_id, concepto, monto_usd, fecha,
                   dias_credito, vencimiento, estado, creado_por)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (cliente["id"], vendedor_id, venta_id, f"Venta V-{venta_id:06d}", total_usd, hoy(),
                 dias, sumar_dias(hoy(), dias), "pendiente", vendedor_id),
            )
            deuda_id = db.execute("SELECT id FROM deudas WHERE venta_id = ?", (venta_id,)).fetchone()["id"]
            if cuota_inicial_usd > 0:
                db.execute(
                    """INSERT INTO abonos (deuda_id, fecha, monto, moneda, tasa, monto_usd, metodo, referencia,
                       nota, registrado_por) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (deuda_id, ahora(), cuota_inicial, moneda, tasa, cuota_inicial_usd,
                     form.get("metodo_pago", "").strip(), f"Cuota inicial V-{venta_id:06d}",
                     "Cuota inicial recibida al registrar la venta", vendedor_id),
                )
    audit("venta_creada", f"V-{venta_id:06d} total_usd={total_usd} estado={estado}; cuota_inicial_usd={cuota_inicial_usd}")
    return venta_id


def _detalles_agrupados(db, venta_id):
    """Cantidad total por producto de una venta (suma líneas repetidas)."""
    filas = db.execute(
        "SELECT producto_id, SUM(cantidad) AS cant FROM venta_detalles WHERE venta_id = ? GROUP BY producto_id",
        (venta_id,),
    ).fetchall()
    return {f["producto_id"]: f["cant"] for f in filas}


def aprobar_venta(venta_id, admin_id):
    """El admin aprueba una venta pendiente: descuenta stock y activa la deuda (o marca el contado como pagado)."""
    db = get_db()
    v = db.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
    if v is None:
        raise ValueError("Venta no encontrada.")
    if v["estado"] != "pendiente":
        raise ValueError("Solo se pueden aprobar ventas pendientes.")
    with db:
        for pid, cant in _detalles_agrupados(db, venta_id).items():
            mover_stock(db, pid, -cant, "salida", f"Venta {v['numero']}", venta_id, admin_id)
        db.execute(
            "UPDATE ventas SET estado='aprobada', aprobado_por=?, fecha_aprobacion=?, pagada=? WHERE id=?",
            (admin_id, ahora(), 1 if v["tipo_pago"] == "contado" else 0, venta_id),
        )
        db.execute("UPDATE deudas SET estado='activa' WHERE venta_id=? AND estado='pendiente'", (venta_id,))
        for deuda in db.execute("SELECT id FROM deudas WHERE venta_id = ?", (venta_id,)).fetchall():
            recalcular_deuda(db, deuda["id"])
    audit("venta_aprobada", v["numero"])


def rechazar_venta(venta_id, admin_id, motivo):
    """El admin rechaza una venta pendiente: no toca el stock y anula la deuda pendiente."""
    db = get_db()
    v = db.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
    if v is None or v["estado"] != "pendiente":
        raise ValueError("Solo se pueden rechazar ventas pendientes.")
    with db:
        db.execute(
            "UPDATE ventas SET estado='rechazada', aprobado_por=?, fecha_aprobacion=?, motivo=? WHERE id=?",
            (admin_id, ahora(), motivo, venta_id),
        )
        db.execute("UPDATE abonos SET anulado=1 WHERE deuda_id IN (SELECT id FROM deudas WHERE venta_id=?) AND anulado=0", (venta_id,))
        db.execute("UPDATE deudas SET estado='anulada' WHERE venta_id=? AND estado='pendiente'", (venta_id,))
    audit("venta_rechazada", f"{v['numero']} — {motivo}; abonos iniciales asociados anulados")


def anular_venta(venta_id, admin_id, motivo):
    """Anula una venta aprobada de forma coherente y conserva todo el historial.

    En una única transacción devuelve el stock, marca como anulados los abonos
    activos de las deudas de esa venta, anula las deudas y la venta. Los abonos
    no se borran: quedan visibles como anulados y no cuentan para el saldo.
    """
    db = get_db()
    v = db.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
    if v is None or v["estado"] != "aprobada":
        raise ValueError("Solo se pueden anular ventas aprobadas.")
    abonos = db.execute(
        """SELECT a.id, a.deuda_id FROM abonos a JOIN deudas d ON d.id = a.deuda_id
            WHERE d.venta_id = ? AND a.anulado = 0 ORDER BY a.id""", (venta_id,),
    ).fetchall()
    with db:
        # Restore inventory and the sold counter from the original sale details.
        for pid, cant in _detalles_agrupados(db, venta_id).items():
            mover_stock(db, pid, cant, "devolucion", f"Anulación {v['numero']}", venta_id, admin_id)
        # Void linked payments instead of deleting them; the debt view excludes them from totals.
        if abonos:
            db.execute(
                """UPDATE abonos SET anulado = 1 WHERE id IN
                   (SELECT a.id FROM abonos a JOIN deudas d ON d.id=a.deuda_id
                    WHERE d.venta_id = ? AND a.anulado = 0)""", (venta_id,)
            )
        db.execute("UPDATE ventas SET estado='anulada', pagada=0, motivo=? WHERE id=?", (motivo, venta_id))
        db.execute("UPDATE deudas SET estado='anulada' WHERE venta_id=?", (venta_id,))
    for a in abonos:
        audit("abono_anulado_por_venta", f"abono #{a['id']} deuda #{a['deuda_id']} venta #{v['numero']}")
    audit("venta_anulada", f"{v['numero']} — {motivo}; stock devuelto; abonos anulados={len(abonos)}; deudas anuladas")


# =================================================================== deudas
def recalcular_deuda(db, deuda_id):
    """Pasa la deuda a 'pagada' si el saldo es 0, o a 'activa' si vuelve a tener saldo.

    Solo afecta deudas activas/pagadas (no toca pendientes ni anuladas).
    """
    d = db.execute("SELECT estado, saldo_usd FROM v_deudas WHERE id = ?", (deuda_id,)).fetchone()
    if d is None or d["estado"] not in ("activa", "pagada"):
        return
    nuevo = "pagada" if d["saldo_usd"] <= 0.005 else "activa"
    if nuevo != d["estado"]:
        db.execute("UPDATE deudas SET estado = ? WHERE id = ?", (nuevo, deuda_id))


def registrar_abono(deuda_id, monto, moneda, metodo, referencia, nota, usuario):
    """Registra un abono sobre una deuda activa.

    El monto siempre es en dólares.
    Si el abono liquida el saldo, la deuda queda como pagada.
    Devuelve el id del abono.
    """
    db = get_db()
    d = db.execute("SELECT * FROM v_deudas WHERE id = ?", (deuda_id,)).fetchone()
    if d is None:
        raise ValueError("Deuda no encontrada.")
    if usuario["rol"] == "vendedor" and d["vendedor_id"] != usuario["id"]:
        raise ValueError("Esta deuda no pertenece a tu cartera.")
    if d["estado"] != "activa":
        raise ValueError("Solo se puede abonar a deudas activas (aprobadas y con saldo).")
    monto = parse_decimal_input(monto, "El monto del abono")
    moneda = "USD"   # el sistema solo maneja dólares
    if monto <= 0:
        raise ValueError("El monto debe ser mayor que cero.")
    tasa = tasa_actual()
    monto_usd = a_usd(monto, moneda, tasa)
    if monto_usd > d["saldo_usd"] + 0.01:
        raise ValueError(f"El abono supera el saldo pendiente ({d['saldo_usd']:.2f} USD).")
    if abs(monto_usd - d["saldo_usd"]) < 0.02:   # evita centavos sueltos por redondeo
        monto_usd = round(d["saldo_usd"], 2)
    with db:
        cur = db.execute(
            """INSERT INTO abonos (deuda_id, fecha, monto, moneda, tasa, monto_usd, metodo, referencia,
               nota, registrado_por) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (deuda_id, ahora(), round(monto, 2), moneda, tasa, monto_usd, metodo, referencia, nota, usuario["id"]),
        )
        recalcular_deuda(db, deuda_id)
    audit("abono_registrado", f"deuda #{deuda_id} monto={monto:.2f} {moneda}")
    return cur.fetchone()["id"]


def anular_abono(abono_id):
    """El admin anula un abono; la deuda vuelve a tener saldo si estaba pagada."""
    db = get_db()
    a = db.execute("SELECT * FROM abonos WHERE id = ?", (abono_id,)).fetchone()
    if a is None or a["anulado"]:
        raise ValueError("Abono no encontrado o ya anulado.")
    with db:
        db.execute("UPDATE abonos SET anulado = 1 WHERE id = ?", (abono_id,))
        recalcular_deuda(db, a["deuda_id"])
    audit("abono_anulado", f"abono #{abono_id} deuda #{a['deuda_id']}")


def crear_deuda_manual(admin_id, cliente_id, concepto, monto_usd, dias):
    """El admin pone una deuda a un cliente sin necesidad de una venta."""
    db = get_db()
    monto_usd = parse_decimal_input(monto_usd, "El monto de la deuda")
    c = db.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    if c is None:
        raise ValueError("Cliente no encontrado.")
    if monto_usd <= 0:
        raise ValueError("El monto debe ser mayor que cero.")
    with db:
        cur = db.execute(
            """INSERT INTO deudas (cliente_id, vendedor_id, venta_id, concepto, monto_usd, fecha,
               dias_credito, vencimiento, estado, creado_por) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cliente_id, c["vendedor_id"], None, concepto or "Deuda manual", round(monto_usd, 2), hoy(),
             dias, sumar_dias(hoy(), dias), "activa", admin_id),
        )
    deuda_id = cur.lastrowid
    audit("deuda_manual", f"deuda #{deuda_id} cliente={c['nombre']} monto_usd={monto_usd}")
    return deuda_id


def editar_deuda(deuda_id, concepto, monto_usd, dias):
    """El admin cambia el monto, concepto o plazo de una deuda (el vencimiento se recalcula desde su fecha)."""
    db = get_db()
    monto_usd = parse_decimal_input(monto_usd, "El monto de la deuda")
    d = db.execute("SELECT * FROM v_deudas WHERE id = ?", (deuda_id,)).fetchone()
    if d is None:
        raise ValueError("Deuda no encontrada.")
    if monto_usd <= 0:
        raise ValueError("El monto debe ser mayor que cero.")
    if monto_usd < d["abonado_usd"] - 0.005:
        raise ValueError(f"El monto no puede ser menor que lo ya abonado ({d['abonado_usd']:.2f} USD).")
    with db:
        db.execute(
            "UPDATE deudas SET concepto=?, monto_usd=?, dias_credito=?, vencimiento=? WHERE id=?",
            (concepto, round(monto_usd, 2), dias, sumar_dias(d["fecha"], dias), deuda_id),
        )
        recalcular_deuda(db, deuda_id)
    audit("deuda_editada", f"deuda #{deuda_id} monto_usd={monto_usd}")


def anular_deuda(deuda_id):
    """El admin quita (condona/anula) una deuda. Se puede reactivar después."""
    db = get_db()
    d = db.execute("SELECT * FROM deudas WHERE id = ?", (deuda_id,)).fetchone()
    if d is None:
        raise ValueError("Deuda no encontrada.")
    with db:
        db.execute("UPDATE deudas SET estado = 'anulada' WHERE id = ?", (deuda_id,))
    audit("deuda_anulada", f"deuda #{deuda_id}")


def reactivar_deuda(deuda_id):
    """El admin vuelve a poner una deuda anulada (si su venta no fue rechazada/anulada)."""
    db = get_db()
    d = db.execute("SELECT * FROM deudas WHERE id = ?", (deuda_id,)).fetchone()
    if d is None or d["estado"] != "anulada":
        raise ValueError("Solo se pueden reactivar deudas anuladas.")
    if d["venta_id"]:
        v = db.execute("SELECT estado FROM ventas WHERE id = ?", (d["venta_id"],)).fetchone()
        if v["estado"] != "aprobada":
            raise ValueError("La venta de origen no está aprobada; no se puede reactivar esta deuda.")
    with db:
        db.execute("UPDATE deudas SET estado = 'activa' WHERE id = ?", (deuda_id,))
        recalcular_deuda(db, deuda_id)
    audit("deuda_reactivada", f"deuda #{deuda_id}")


def estado_vencimiento(d):
    """Clasifica una deuda: 'pagada', 'anulada', 'pendiente', 'vencida', 'por_vencer' o 'al_dia'."""
    if d["estado"] in ("pagada", "anulada", "pendiente"):
        return d["estado"]
    h = hoy()
    if d["vencimiento"] < h:
        return "vencida"
    limite = sumar_dias(h, to_int(get_config("dias_por_vencer", "3"), 3))
    return "por_vencer" if d["vencimiento"] <= limite else "al_dia"


# ============================================= precios actualizados (lista de 48 h)
HORAS_PRECIOS_RECIENTES = 48


def registrar_cambio_precio(db, producto_id, usuario_id, mayor_ant, mayor_nuevo):
    """Guarda un cambio de precio; durante 48 horas el producto aparece en la lista de "precios actualizados".

    No hace commit (lo hace quien la llama). Si los precios no cambiaron no registra nada.
    """
    if abs(mayor_ant - mayor_nuevo) < 0.00001:
        return False
    ahora_txt = ahora()
    vence = (datetime.strptime(ahora_txt, "%Y-%m-%d %H:%M:%S") + timedelta(hours=HORAS_PRECIOS_RECIENTES)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """INSERT INTO precio_cambios (producto_id, usuario_id, fecha, expira, mayor_ant, mayor_nuevo)
           VALUES (?,?,?,?,?,?)""",
        (producto_id, usuario_id, ahora_txt, vence, mayor_ant, mayor_nuevo))
    return True


def precios_recientes():
    """Productos con precio cambiado en las últimas 48 horas (uno por producto, el más reciente primero).

    Cada fila trae los precios de antes (los del primer cambio de la ventana) y los de ahora,
    quién hizo el último cambio, cuándo, y cuándo deja de mostrarse. Los vencidos se ignoran solos.
    """
    filas = query(
        """SELECT c.*, p.codigo, p.nombre, p.imagen, p.activo, u.nombre AS usuario
             FROM precio_cambios c JOIN productos p ON p.id = c.producto_id
             LEFT JOIN usuarios u ON u.id = c.usuario_id
            WHERE c.expira > ? ORDER BY c.id ASC""", (ahora(),))
    por_producto = {}
    for f in filas:
        if not f["activo"]:
            continue
        previo = por_producto.get(f["producto_id"])
        por_producto[f["producto_id"]] = {
            "producto_id": f["producto_id"], "codigo": f["codigo"], "nombre": f["nombre"], "imagen": f["imagen"],
            "mayor_ant": previo["mayor_ant"] if previo else f["mayor_ant"],
            "mayor_nuevo": f["mayor_nuevo"],
            "usuario": f["usuario"], "fecha": f["fecha"], "expira": f["expira"], "id": f["id"]}
    return sorted(por_producto.values(), key=lambda r: r["id"], reverse=True)


# ========================================================== pedidos para armar
def armado_desde_venta():
    """Id de venta desde el cual se exige armado (las ventas anteriores a esta función no lo piden)."""
    return to_int(get_config("armado_desde_venta", "0"))


def vendedores_especiales():
    """Vendedores especiales activos (los únicos que pueden armar pedidos)."""
    return query("SELECT id, nombre FROM usuarios WHERE rol='vendedor' AND tipo_vendedor='especial' AND activo=1 ORDER BY nombre")


def asignar_armado(venta_id, destino, admin_id):
    """El admin decide quién arma el pedido de una venta aprobada (la venta NO cambia de vendedor).

    destino: id de un vendedor especial, o 'cualquiera' (el pedido llega a todos los especiales y lo arma el primero
    que lo confirme). Si el armado ya estaba asignado y aún no se armó, se reasigna.
    """
    db = get_db()
    v = db.execute("SELECT id, numero, estado FROM ventas WHERE id = ?", (venta_id,)).fetchone()
    if v is None:
        raise ValueError("Venta no encontrada.")
    if v["estado"] != "aprobada":
        raise ValueError("Solo se puede asignar el armado de una venta aprobada.")
    if str(destino) == "cualquiera":
        asignado = None
    else:
        asignado = to_int(destino)
        ok = db.execute("SELECT 1 FROM usuarios WHERE id=? AND rol='vendedor' AND tipo_vendedor='especial' AND activo=1",
                        (asignado,)).fetchone()
        if not ok:
            raise ValueError("Elige un vendedor especial válido (o 'Cualquiera').")
    actual = db.execute("SELECT * FROM armados WHERE venta_id = ?", (venta_id,)).fetchone()
    with db:
        if actual is None:
            db.execute("INSERT INTO armados (venta_id, asignado_a, estado, asignado_por, fecha_asignacion) VALUES (?,?,?,?,?)",
                       (venta_id, asignado, "pendiente", admin_id, ahora()))
        elif actual["estado"] == "armado":
            raise ValueError("Ese pedido ya fue armado.")
        else:
            db.execute("UPDATE armados SET asignado_a=?, asignado_por=?, fecha_asignacion=? WHERE id=?",
                       (asignado, admin_id, ahora(), actual["id"]))
    audit("armado_asignado", f"{v['numero']} → {'cualquiera' if asignado is None else 'vendedor #' + str(asignado)}")


def confirmar_armado(armado_id, usuario):
    """Un vendedor especial confirma que armó el pedido. Gana el primero en confirmar (operación atómica)."""
    db = get_db()
    a = db.execute("SELECT a.*, v.numero, v.estado AS venta_estado FROM armados a JOIN ventas v ON v.id = a.venta_id "
                   "WHERE a.id = ?", (armado_id,)).fetchone()
    if a is None or a["venta_estado"] != "aprobada":
        raise ValueError("Ese pedido ya no está disponible.")
    if a["asignado_a"] is not None and a["asignado_a"] != usuario["id"]:
        raise ValueError("Ese pedido está asignado a otro vendedor.")
    with db:
        cur = db.execute(
            "UPDATE armados SET estado='armado', armado_por=?, fecha_armado=? WHERE id=? AND estado='pendiente'",
            (usuario["id"], ahora(), armado_id))
    if cur.rowcount == 0:
        quien = db.execute("SELECT u.nombre FROM armados a JOIN usuarios u ON u.id = a.armado_por WHERE a.id=?",
                           (armado_id,)).fetchone()
        raise ValueError(f"Ese pedido ya fue armado por {quien['nombre'] if quien else 'otro vendedor'}.")
    audit("pedido_armado", a["numero"])


def lineas_armado(venta_id):
    """Productos (con foto) y cantidades que hay que armar para una venta."""
    return query(
        """SELECT p.id, p.codigo, p.nombre, p.imagen, p.unidad, SUM(d.cantidad) AS cantidad
             FROM venta_detalles d JOIN productos p ON p.id = d.producto_id
            WHERE d.venta_id = ? GROUP BY p.id ORDER BY MIN(d.id)""", (venta_id,))


def contar_por_armar(usuario):
    """Pedidos pendientes: para un especial, sus pedidos y ventas sin asignar; para admin, todos."""
    desde = armado_desde_venta()
    if usuario["rol"] == "admin":
        pend = query("SELECT COUNT(*) AS n FROM armados a JOIN ventas v ON v.id=a.venta_id "
                     "WHERE a.estado='pendiente' AND v.estado='aprobada'", one=True)["n"]
        sin = query("SELECT COUNT(*) AS n FROM ventas v WHERE v.estado='aprobada' AND v.id > ? "
                    "AND NOT EXISTS (SELECT 1 FROM armados a WHERE a.venta_id=v.id)", (desde,), one=True)["n"]
        return pend + sin
    return query("SELECT COUNT(*) AS n FROM armados a JOIN ventas v ON v.id=a.venta_id "
                 "WHERE a.estado='pendiente' AND v.estado='aprobada' AND (a.asignado_a = ? OR a.asignado_a IS NULL)",
                 (usuario["id"],), one=True)["n"] + query(
        "SELECT COUNT(*) AS n FROM ventas v WHERE v.estado='aprobada' AND v.id > ? "
        "AND NOT EXISTS (SELECT 1 FROM armados a WHERE a.venta_id=v.id)",
        (desde,), one=True)["n"]


# ==================================================================== panel
def resumen_panel(usuario):
    """Cifras del panel principal. El admin ve todo; el vendedor solo lo suyo."""
    es_admin = usuario["rol"] == "admin"
    es_aprobador = es_admin or (usuario["rol"] == "vendedor" and usuario["tipo_vendedor"] == "especial")
    filtro_v = "" if es_admin else " AND vendedor_id = ?"
    args = () if es_admin else (usuario["id"],)
    mes = hoy()[:7]

    def uno(sql, extra=()):
        return query(sql, extra, one=True)

    r = {}
    if es_admin:
        r["ventas_mes"] = uno(
            """SELECT COALESCE(SUM(total_usd),0) AS t, COUNT(*) AS n FROM ventas
                WHERE estado='aprobada' AND substr(fecha,1,7)=?""", (mes,))
    else:
        r["ventas_mes"] = uno(
            """SELECT COALESCE(SUM(v.total_usd),0) AS t, COUNT(*) AS n
                 FROM ventas v JOIN clientes c ON c.id = v.cliente_id
                WHERE v.estado='aprobada' AND substr(v.fecha,1,7)=? AND c.vendedor_id=?""",
            (mes, usuario["id"]))
    if es_aprobador:
        r["pendientes"] = uno(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total_usd),0) AS t FROM ventas WHERE estado='pendiente'")
    else:
        r["pendientes"] = uno(
            """SELECT COUNT(*) AS n, COALESCE(SUM(v.total_usd),0) AS t
                 FROM ventas v JOIN clientes c ON c.id = v.cliente_id
                WHERE v.estado='pendiente' AND c.vendedor_id=?""", (usuario["id"],))
    r["por_cobrar"] = uno(
        f"SELECT COALESCE(SUM(saldo_usd),0) AS t, COUNT(*) AS n FROM v_deudas WHERE estado='activa'{filtro_v}", args)
    r["vencidas"] = uno(
        f"SELECT COALESCE(SUM(saldo_usd),0) AS t, COUNT(*) AS n FROM v_deudas "
        f"WHERE estado='activa' AND vencimiento < ?{filtro_v}", (hoy(),) + args)
    # La alerta de stock mínimo es solo para el admin.
    r["stock_bajo"] = productos_en_alerta(8) if es_admin else []
    r["n_stock_bajo"] = len(productos_en_alerta()) if es_admin else 0
    r["ultimas"] = query(
        f"""SELECT v.id, v.numero, v.fecha, v.total, v.moneda, v.estado, c.nombre AS cliente
              FROM ventas v JOIN clientes c ON c.id = v.cliente_id
             WHERE 1=1{" AND c.vendedor_id = ?" if not es_admin else ""}
             ORDER BY v.id DESC LIMIT 6""", () if es_admin else (usuario["id"],))
    r["proximas"] = query(
        f"""SELECT id, cliente_nombre, vencimiento, saldo_usd FROM v_deudas
             WHERE estado='activa'{filtro_v} ORDER BY vencimiento ASC LIMIT 6""", args)
    if es_admin:
        r["por_vendedor"] = query(
            """SELECT u.id, u.nombre,
                      COALESCE((SELECT SUM(v.total_usd) FROM ventas v
                                 JOIN clientes c ON c.id = v.cliente_id
                                WHERE c.vendedor_id=u.id AND v.estado='aprobada' AND substr(v.fecha,1,7)=?),0) AS vendido,
                      COALESCE((SELECT SUM(saldo_usd) FROM v_deudas
                                 WHERE vendedor_id=u.id AND estado='activa'),0) AS por_cobrar
                 FROM usuarios u WHERE u.rol='vendedor' AND u.activo=1 ORDER BY vendido DESC LIMIT 6""", (mes,))
    return r
