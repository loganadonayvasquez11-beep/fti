# Sistema de Inventario y Ventas a Crédito

Flask + SQLite + CSS (estilo Windows 11 modo oscuro) con muy poco JavaScript.

La aplicación guarda por defecto la base en `instance/inventario.db`. Esa carpeta y sus datos no se versionan. Puedes cambiar la ruta mediante la variable de entorno `DATABASE_PATH`.

## Instalación y uso local

Instala las dependencias desde PowerShell en la carpeta del sistema:

```powershell
pip install -r requirements.txt
```

Define `INITIAL_ADMIN_PASSWORD` al crear una base vacía y arranca la aplicación:

```powershell
$env:INITIAL_ADMIN_PASSWORD = "elige-una-clave-segura"
python app.py
Remove-Item Env:INITIAL_ADMIN_PASSWORD
```

La clave solo se necesita cuando todavía no existe un administrador. No uses una contraseña real de otros servicios.

## Cómo arrancarlo en la red local

Abre PowerShell en esta carpeta y ejecuta:

```powershell
python app.py
```

La primera vez, si Windows pregunta por el acceso de red, permite Python **solo en redes privadas**. No habilites redes públicas ni crees una regla para Internet.

Para entrar desde un teléfono o desde otro PC:

1. Conecta el dispositivo a la misma red Wi-Fi que el PC servidor (no a una red de invitados que aísle los dispositivos).
2. En el PC, ejecuta `ipconfig` en PowerShell y busca la **Dirección IPv4** del adaptador Wi-Fi, por ejemplo `192.168.1.25`.
3. En el navegador del dispositivo, abre `http://192.168.1.25:5000`, sustituyendo la dirección de ejemplo por la del PC.
4. Inicia sesión con la cuenta del sistema. Para uso compartido, crea una cuenta por persona y cambia cualquier contraseña inicial.

El servidor debe permanecer abierto en el PC mientras se use la aplicación. La dirección IP puede cambiar al reiniciar el router. La aplicación escucha en todas las interfaces del PC, así que el firewall de Windows debe limitar el acceso a la red privada de confianza. No la publiques en Internet ni configures reenvío de puertos en el router.

Si quieres conservar las sesiones iniciadas al reiniciar el servidor, define una variable de entorno `SECRET_KEY` persistente y privada antes de arrancarlo. Si no se define, se genera una clave aleatoria al iniciar y las sesiones se cierran cuando el proceso se reinicia. Se puede cambiar el puerto con la variable `PORT` (5000 por defecto).

Para inicializar la base sin arrancar el servidor, define `INITIAL_ADMIN_PASSWORD` y ejecuta `flask --app app init-db`. No se proporciona una contraseña predeterminada. Para cargar datos de prueba con `--demo`, define también `DEMO_USER_PASSWORD`; no uses cuentas de demostración en producción. Configura una `SECRET_KEY` privada antes de publicar la app.
Después entra a **Configuración** y coloca los datos de tu empresa (salen en los PDF).

## Despliegue en PythonAnywhere

1. Crea un entorno virtual con una versión de Python compatible con `requirements.txt` e instala las dependencias con `pip install -r requirements.txt`.
2. En la configuración WSGI de PythonAnywhere, configura una clave privada para `SECRET_KEY` y la ruta permanente del archivo SQLite:

   ```python
   import os
   os.environ["SECRET_KEY"] = "reemplaza-por-una-clave-larga-y-aleatoria"
   os.environ["DATABASE_PATH"] = "/home/tu_usuario/fti/instance/inventario.db"
   ```

   No guardes esos valores en Git. La carpeta `instance` debe existir y ser escribible por la aplicación.
3. Migra primero los datos PostgreSQL desde el PC que puede acceder a la base. Conserva la base original y genera una copia SQLite:

   ```powershell
   $env:POSTGRESQL_SOURCE_URL = $env:DATABASE_URL
   flask --app app migrate-postgresql-to-sqlite --destination instance/inventario.db
   Remove-Item Env:POSTGRESQL_SOURCE_URL
   ```

   La migración solo lee PostgreSQL, valida cantidades, contenido, integridad y relaciones, y rechaza destinos con tablas existentes. Si no tienes `DATABASE_URL` configurada, define `POSTGRESQL_SOURCE_URL` con la URL privada de PostgreSQL. No publiques ni compartas esa URL.
4. Sube el archivo `instance/inventario.db` generado a PythonAnywhere, a la ruta configurada en `DATABASE_PATH`. No ejecutes `init-db` sobre una base que ya contiene datos.
5. En la configuración WSGI agrega la carpeta del proyecto a `sys.path` y expón la aplicación:

   ```python
   import sys

   project_home = "/home/tu_usuario/fti"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   from app import app as application
   ```

6. Sube también las imágenes en `static/uploads/productos/` y los PDFs históricos de `instance/archivos_historicos/`, si los necesitas. Configura los archivos estáticos y recarga la aplicación desde el panel.

SQLite guarda la base junto a los archivos de la aplicación y es adecuada para un uso de bajo volumen; permite una escritura a la vez. Conserva copias de seguridad regulares y no ejecutes la aplicación en varios servidores compartiendo el mismo archivo. No ejecutes `init-db --demo` en producción.

## Reglas de negocio

- **Admin**: dueño del almacén central (productos, precios al mayor, entradas, mermas, ajustes, kardex),
  de la cartera de vendedores (agregar / quitar con traspaso de clientes y deudas); aprueba, rechaza y anula ventas.
- **Vendedor**: tiene su cartera de clientes (puede agregar), genera ventas con productos del catálogo,
  vende **al mayor**, contado o crédito y puede **cambiar el precio unitario** de cada línea.
- **Vendedor especial**: conserva sus funciones de venta y armado, puede aprobar o rechazar las ventas pendientes de todos los vendedores y anular ventas aprobadas, pero no puede crear ni editar productos, cambiar precios ni modificar el stock.
- **Contado**: la venta se aprueba y se cobra sola, y descuenta el stock en el acto
  (en Configuración puedes exigir también tu aprobación).
- **Crédito**: queda *pendiente*, **no descuenta stock** hasta que el admin o un vendedor especial la aprueba; entonces nace la deuda activa
  con vencimiento = fecha + días de crédito (los días varían por cliente y por venta).
- **Deudas**: el admin puede ponerlas, editarlas, quitarlas y reactivarlas a su gusto. Los abonos son en $; al llegar a saldo 0 la deuda pasa a pagada.
- Los precios modificados por el vendedor quedan marcados y aparecen en **Reportes**.
- Todo queda en **Auditoría**. Los clientes **no** inician sesión.

## PDF

Nota de venta, recibo de abono y estado de cuenta del cliente (botones en cada pantalla).

## Estructura

```
app.py          fábrica de la app, filtros, seguridad CSRF
config.py       configuración (SECRET_KEY y ruta de SQLite)
db.py / schema.sql   conexión SQLite y esquema
migrar_postgresql_sqlite.py   copia PostgreSQL a SQLite sin alterar el origen
configurar_postgresql.py   utilidad antigua para instalaciones PostgreSQL
services.py     reglas de negocio (ventas, aprobación, stock, deudas, abonos)
pdf.py          generación de PDF (ReportLab)
utils.py        fechas, formatos, login/roles/CSRF
routes/         un módulo por área (auth, panel, clientes, catalogo, ventas, deudas, admin)
templates/      pantallas HTML (Jinja)
static/         css/style.css y js/venta.js (formulario de venta)
```


## Cambios añadidos

- Productos con foto opcional (JPG/PNG/WEBP/GIF, máximo 5 MB), imagen de catálogo ampliada y nombre visible; si falta o falla la imagen, se muestra el nombre como alternativa.
- El sistema no solicita ni muestra costo; los reportes de valorización usan el precio de venta al mayor.
- El administrador puede retirar productos. Si existen ventas o movimientos, se desactivan para conservar el historial.
- Sección **Archivos históricos** para generar y descargar el PDF del día con Kardex/movimientos, detalle de cada artículo vendido (cliente, vendedor e importes), deudas pendientes, abonos y auditoría.
- Se conserva un único archivo, con nombre `historial_DD-MM-AAAA.pdf`. Si se genera de nuevo durante el mismo día se actualiza; al cambiar el día, el PDF anterior se elimina. La limpieza se ejecuta a medianoche y también al abrir la sección o generar el PDF.
- Los registros originales de ventas, inventario, deudas y auditoría no se eliminan; solo se retiran los PDF vencidos. Se puede generar desde la interfaz o ejecutar `python -m flask --app app archive-daily`. En despliegues con varios workers, se recomienda desactivar el scheduler integrado (`ENABLE_ARCHIVE_SCHEDULER=0`) para evitar ejecuciones duplicadas.

## Tipos de vendedor, plazos, colores y alerta de stock

- **Vendedor especial** (los vendedores que ya existían pasaron a este tipo): vende al mayor en dólares; además, puede aprobar/rechazar ventas pendientes, anular ventas aprobadas y armar pedidos, pero no gestionar productos, precios o stock.
- **Vendedor normal**: vende solo **al mayor** y en **dólares**. El administrador define el tipo al crear o editar al vendedor.
- **Plazo de crédito**: ningún vendedor puede escribirlo. Se asigna solo según el total en USD: menos de 250 $ → **15 días**, menos de 600 $ → **20 días**, 600 $ o más → **30 días**. Solo el admin puede cambiarlo: fijándolo en la ficha del cliente (Editar cliente → "Días de crédito fijos"; vacío = automático) o editando la deuda ya aprobada.
- **Color del cliente** (verde / amarillo / rojo / sin color): solo el admin lo cambia, desde la ficha del cliente. Los vendedores, especiales o normales, solo lo ven.
- **Alerta de stock (admin)**: Configuración → "Alerta mínima general de stock" (10 por defecto). Si un producto tiene su propio "Stock mínimo", se usa ese. El panel muestra cuántos productos están en alerta y cuáles (con su mínimo), el menú Productos muestra un contador y la lista marca cada producto en alerta.
- Al arrancar, la aplicación actualiza sola una base de datos existente y conserva los registros e importes históricos de ventas, inventario, deudas y auditoría.
- La actualización al esquema de venta al mayor elimina los campos de precios detal de productos e historial de cambios, y elimina el tipo de precio de las ventas.
- Los respaldos anteriores no se reescriben; al restaurar uno, la aplicación aplica esta actualización al arrancar.
-configuracion y herramientas accesibles y super necesarias para dicha administracion