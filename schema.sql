-- ============================================================
--  ESQUEMA DE LA BASE DE DATOS
--  Todos los montos monetarios internos se guardan en USD
--  (columna *_usd) y las ventas/abonos guardan además la moneda
--  original y la tasa (Bs por 1 USD) usada en ese momento.
-- ============================================================
PRAGMA foreign_keys = ON;

-- Usuarios del sistema: administradores y vendedores (los clientes NO inician sesión)
CREATE TABLE IF NOT EXISTS usuarios (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario    TEXT NOT NULL UNIQUE,
    nombre     TEXT NOT NULL,
    clave_hash TEXT NOT NULL,
    rol        TEXT NOT NULL CHECK (rol IN ('admin', 'vendedor')),
    telefono   TEXT DEFAULT '',
    email      TEXT DEFAULT '',
    activo     INTEGER NOT NULL DEFAULT 1,
    creado     TEXT NOT NULL,
    -- Tipo de vendedor: 'especial' puede aprobar ventas y armar pedidos.
    tipo_vendedor TEXT NOT NULL DEFAULT 'normal' CHECK (tipo_vendedor IN ('especial', 'normal'))
);

-- Suscripciones por dispositivo para enviar Web Push a cada usuario.
CREATE TABLE IF NOT EXISTS notificaciones_push (
    endpoint      TEXT PRIMARY KEY,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    clave_publica TEXT NOT NULL,
    clave_auth    TEXT NOT NULL,
    creado        TEXT NOT NULL
);

-- Parámetros generales (datos de la empresa, tasa de cambio, reglas)
CREATE TABLE IF NOT EXISTS configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

-- Categorías del catálogo (solo las crea el admin)
CREATE TABLE IF NOT EXISTS categorias (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    prefijo TEXT UNIQUE,
    activo INTEGER NOT NULL DEFAULT 1
);

-- Productos del almacén central (solo los gestiona el admin)
CREATE TABLE IF NOT EXISTS productos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo           TEXT NOT NULL UNIQUE,
    nombre           TEXT NOT NULL,
    imagen           TEXT DEFAULT NULL,
    categoria_id     INTEGER REFERENCES categorias(id),
    unidad           TEXT NOT NULL DEFAULT 'und',
    costo_usd        REAL NOT NULL DEFAULT 0,
    precio_mayor_usd REAL NOT NULL DEFAULT 0,
    stock            REAL NOT NULL DEFAULT 0,
    stock_minimo     REAL NOT NULL DEFAULT 0,
    vendidos         REAL NOT NULL DEFAULT 0,
    activo           INTEGER NOT NULL DEFAULT 1,
    creado           TEXT NOT NULL
);

-- Clientes: pertenecen a la cartera de un vendedor.
-- tipo_pago define si compra de contado o a crédito (deudor).
CREATE TABLE IF NOT EXISTS clientes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    vendedor_id        INTEGER NOT NULL REFERENCES usuarios(id),
    nombre             TEXT NOT NULL,
    documento          TEXT DEFAULT '',
    telefono           TEXT DEFAULT '',
    direccion          TEXT DEFAULT '',
    tipo_pago          TEXT NOT NULL DEFAULT 'contado' CHECK (tipo_pago IN ('contado', 'credito')),
    dias_credito       INTEGER NOT NULL DEFAULT 0,
    limite_credito_usd REAL NOT NULL DEFAULT 0,      -- 0 = sin límite
    activo             INTEGER NOT NULL DEFAULT 1,
    creado             TEXT NOT NULL,
    -- Semáforo del cliente: solo el admin lo cambia; los vendedores solo lo ven. NULL = sin color.
    color              TEXT DEFAULT NULL CHECK (color IN ('verde', 'amarillo', 'rojo'))
);

-- Ventas generadas por los vendedores; el admin las aprueba.
CREATE TABLE IF NOT EXISTS ventas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    numero           TEXT,
    fecha            TEXT NOT NULL,
    cliente_id       INTEGER NOT NULL REFERENCES clientes(id),
    vendedor_id      INTEGER NOT NULL REFERENCES usuarios(id),
    tipo_pago        TEXT NOT NULL CHECK (tipo_pago IN ('contado', 'credito')),
    dias_credito     INTEGER NOT NULL DEFAULT 0,
    moneda           TEXT NOT NULL CHECK (moneda IN ('USD', 'VES')),
    tasa             REAL NOT NULL DEFAULT 1,        -- Bs por 1 USD al momento de la venta
    metodo_pago      TEXT DEFAULT '',
    subtotal         REAL NOT NULL DEFAULT 0,        -- en la moneda de la venta
    descuento_porcentaje INTEGER NOT NULL DEFAULT 0,
    descuento        REAL NOT NULL DEFAULT 0,
    total            REAL NOT NULL DEFAULT 0,
    total_usd        REAL NOT NULL DEFAULT 0,
    estado           TEXT NOT NULL DEFAULT 'pendiente'
                     CHECK (estado IN ('pendiente', 'aprobada', 'rechazada', 'anulada')),
    pagada           INTEGER NOT NULL DEFAULT 0,     -- 1 si fue de contado y ya está cobrada
    nota             TEXT DEFAULT '',
    aprobado_por     INTEGER REFERENCES usuarios(id),
    fecha_aprobacion TEXT,
    motivo           TEXT DEFAULT ''                 -- motivo de rechazo o anulación
);

CREATE TABLE IF NOT EXISTS venta_detalles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id       INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    producto_id    INTEGER NOT NULL REFERENCES productos(id),
    cantidad       REAL NOT NULL,
    precio_lista   REAL NOT NULL,                    -- precio mayor base en la moneda de la venta
    precio_unit    REAL NOT NULL,                    -- precio realmente cobrado
    modificado     INTEGER NOT NULL DEFAULT 0,       -- 1 si el vendedor cambió el precio base
    subtotal       REAL NOT NULL
);

-- Presupuestos guardados por vendedor hasta su eventual conversión en venta.
CREATE TABLE IF NOT EXISTS presupuestos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    vendedor_id    INTEGER NOT NULL REFERENCES usuarios(id),
    cliente_id     INTEGER NOT NULL REFERENCES clientes(id),
    creado         TEXT NOT NULL,
    actualizado    TEXT NOT NULL,
    tipo_pago      TEXT NOT NULL DEFAULT 'contado' CHECK (tipo_pago IN ('contado', 'credito')),
    metodo_pago    TEXT DEFAULT '',
    cuota_inicial  REAL NOT NULL DEFAULT 0,
    subtotal       REAL NOT NULL DEFAULT 0,
    descuento_porcentaje INTEGER NOT NULL DEFAULT 0,
    descuento      REAL NOT NULL DEFAULT 0,
    total          REAL NOT NULL DEFAULT 0,
    nota           TEXT DEFAULT '',
    venta_id       INTEGER REFERENCES ventas(id)
);

CREATE TABLE IF NOT EXISTS presupuesto_detalles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    presupuesto_id    INTEGER NOT NULL REFERENCES presupuestos(id) ON DELETE CASCADE,
    producto_id       INTEGER NOT NULL REFERENCES productos(id),
    cantidad          REAL NOT NULL,
    precio_lista      REAL NOT NULL,
    precio_unit       REAL NOT NULL,
    modificado        INTEGER NOT NULL DEFAULT 0,
    subtotal          REAL NOT NULL
);

-- Deudas: nacen de ventas a crédito o las crea el admin manualmente.
CREATE TABLE IF NOT EXISTS deudas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id   INTEGER NOT NULL REFERENCES clientes(id),
    vendedor_id  INTEGER NOT NULL REFERENCES usuarios(id),
    venta_id     INTEGER REFERENCES ventas(id),
    concepto     TEXT NOT NULL DEFAULT '',
    monto_usd    REAL NOT NULL,
    fecha        TEXT NOT NULL,
    dias_credito INTEGER NOT NULL DEFAULT 0,
    vencimiento  TEXT NOT NULL,
    estado       TEXT NOT NULL DEFAULT 'activa'
                 CHECK (estado IN ('pendiente', 'activa', 'pagada', 'anulada')),
    creado_por   INTEGER REFERENCES usuarios(id)
);

-- Abonos (pagos parciales o totales) sobre una deuda.
CREATE TABLE IF NOT EXISTS abonos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    deuda_id      INTEGER NOT NULL REFERENCES deudas(id),
    fecha         TEXT NOT NULL,
    monto         REAL NOT NULL,                     -- en la moneda en que se pagó
    moneda        TEXT NOT NULL CHECK (moneda IN ('USD', 'VES')),
    tasa          REAL NOT NULL DEFAULT 1,
    monto_usd     REAL NOT NULL,
    metodo        TEXT DEFAULT '',
    referencia    TEXT DEFAULT '',
    nota          TEXT DEFAULT '',
    registrado_por INTEGER REFERENCES usuarios(id),
    anulado       INTEGER NOT NULL DEFAULT 0
);

-- Kardex: cada entrada, salida o ajuste del almacén central.
CREATE TABLE IF NOT EXISTS movimientos_stock (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id      INTEGER NOT NULL REFERENCES productos(id),
    fecha            TEXT NOT NULL,
    tipo             TEXT NOT NULL CHECK (tipo IN ('entrada', 'salida', 'ajuste', 'merma', 'devolucion')),
    cantidad         REAL NOT NULL,                  -- con signo: + entra, - sale
    stock_resultante REAL NOT NULL,
    motivo           TEXT DEFAULT '',
    venta_id         INTEGER REFERENCES ventas(id),
    usuario_id       INTEGER REFERENCES usuarios(id)
);

-- Auditoría: quién hizo qué y cuándo.
CREATE TABLE IF NOT EXISTS auditoria (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha      TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuarios(id),
    accion     TEXT NOT NULL,
    detalle    TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_clientes_vendedor ON clientes(vendedor_id);
CREATE INDEX IF NOT EXISTS ix_ventas_vendedor   ON ventas(vendedor_id, estado);
CREATE INDEX IF NOT EXISTS ix_deudas_cliente    ON deudas(cliente_id, estado);
CREATE INDEX IF NOT EXISTS ix_abonos_deuda      ON abonos(deuda_id);
CREATE INDEX IF NOT EXISTS ix_mov_producto      ON movimientos_stock(producto_id);

-- Vista de deudas con nombres y saldo ya calculado (monto - abonos vigentes).
DROP VIEW IF EXISTS v_deudas;
CREATE VIEW v_deudas AS
SELECT d.*,
       c.nombre    AS cliente_nombre,
       c.documento AS cliente_documento,
       u.nombre    AS vendedor_nombre,
       COALESCE((SELECT SUM(a.monto_usd) FROM abonos a
                  WHERE a.deuda_id = d.id AND a.anulado = 0), 0) AS abonado_usd,
       d.monto_usd - COALESCE((SELECT SUM(a.monto_usd) FROM abonos a
                  WHERE a.deuda_id = d.id AND a.anulado = 0), 0) AS saldo_usd
  FROM deudas d
  JOIN clientes c ON c.id = d.cliente_id
  JOIN usuarios u ON u.id = d.vendedor_id;


-- Índice de archivos históricos PDF generados por períodos.
CREATE TABLE IF NOT EXISTS archivos_historicos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    tipo TEXT NOT NULL,
    desde TEXT NOT NULL,
    hasta TEXT NOT NULL,
    generado TEXT NOT NULL,
    ruta TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuarios(id),
    estado TEXT NOT NULL DEFAULT 'listo'
);
