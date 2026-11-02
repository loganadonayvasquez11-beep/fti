# Sistema de Inventario y Ventas a Crédito

Flask + PostgreSQL obligatorio + CSS (estilo Windows 11 modo oscuro) con muy poco JavaScript.

## PostgreSQL para uso en red

Los teléfonos y otros equipos **no se conectan directamente a la base de datos**: se conectan a Flask en el PC servidor. La aplicación requiere PostgreSQL y no crea ni utiliza archivos SQLite.

1. Instala PostgreSQL en el PC servidor. Durante la instalación elige una contraseña fuerte para el usuario `postgres`; no la compartas.
2. Instala las dependencias desde PowerShell en la carpeta del sistema:

   ```powershell
   pip install -r requirements.txt
   ```

3. En PowerShell, ejecuta el asistente de configuración:

   ```powershell
   python configurar_postgresql.py
   ```

   El asistente pide la contraseña de `postgres` sin mostrarla, crea un usuario de aplicación sin permisos de superusuario y la base `fti_db`, y guarda la conexión localmente para tu usuario de Windows. Si `fti_db` ya existe, se detiene sin modificarla.
4. Cierra y vuelve a abrir PowerShell, y luego inicia el servidor:

   ```powershell
   python app.py
   ```

   `DATABASE_URL` y `SECRET_KEY` quedan configuradas para tu usuario; no compartas esos valores.

PostgreSQL debe aceptar conexiones solo desde el PC servidor (`127.0.0.1`); **no abras su puerto en el router ni en el firewall para los teléfonos**. Para el acceso Wi-Fi, permite solo el servidor web de Flask en la red privada, siguiendo los pasos de abajo.

## Cómo arrancarlo en la red local

En el PC que tiene la base de datos, abre PowerShell en esta carpeta y ejecuta:

```bash
python app.py
```

La primera vez, si Windows pregunta por el acceso de red, permite Python **solo en redes privadas**. No habilites redes públicas ni crees una regla para Internet. `DATABASE_URL` debe estar disponible para el proceso; si falta, Flask se detiene en vez de usar SQLite.

Para entrar desde un teléfono o desde otro PC:

1. Conecta el dispositivo a la misma red Wi-Fi que el PC servidor (no a una red de invitados que aísle los dispositivos).
2. En el PC, ejecuta `ipconfig` en PowerShell y busca la **Dirección IPv4** del adaptador Wi-Fi, por ejemplo `192.168.1.25`.
3. En el navegador del dispositivo, abre `http://192.168.1.25:5000`, sustituyendo la dirección de ejemplo por la del PC.
4. Inicia sesión con la cuenta del sistema. Para uso compartido, crea una cuenta por persona y cambia cualquier contraseña inicial.

El servidor debe permanecer abierto en el PC mientras se use la aplicación. La dirección IP puede cambiar al reiniciar el router. La aplicación escucha en todas las interfaces del PC, así que el firewall de Windows debe limitar el acceso a la red privada de confianza. No la publiques en Internet ni configures reenvío de puertos en el router.

Si quieres conservar las sesiones iniciadas al reiniciar el servidor, define una variable de entorno `SECRET_KEY` persistente y privada antes de arrancarlo. Si no se define, se genera una clave aleatoria al iniciar y las sesiones se cierran cuando el proceso se reinicia. Se puede cambiar el puerto con la variable `PORT` (5000 por defecto).

Al crear una base nueva, define `INITIAL_ADMIN_PASSWORD` en el entorno antes de ejecutar `flask --app app init-db`. No se proporciona una contraseña predeterminada. Para cargar datos de prueba con `--demo`, define también `DEMO_USER_PASSWORD`; no uses cuentas de demostración en producción. Cambia las claves desde el sistema y configura una `SECRET_KEY` privada antes de usarlo en serio.
Después entra a **Configuración** y coloca los datos de tu empresa (salen en los PDF).

## Despliegue en PythonAnywhere

1. Crea un entorno virtual con una versión de Python compatible con `requirements.txt` e instala las dependencias con `pip install -r requirements.txt`.
2. Configura `DATABASE_URL` y `SECRET_KEY` en el archivo WSGI de PythonAnywhere, que está fuera del repositorio. Usa valores aleatorios/fuertes y no los guardes en Git.
3. Antes de publicar el sitio, en una consola Bash solicita las credenciales de forma oculta e inicializa una base vacía:

   ```bash
   cd ~/fti
   source ~/.virtualenvs/fti/bin/activate
   read -s -p "DATABASE_URL: " DATABASE_URL; export DATABASE_URL; echo
   read -s -p "Clave inicial de admin: " INITIAL_ADMIN_PASSWORD; export INITIAL_ADMIN_PASSWORD; echo
   flask --app app init-db
   unset INITIAL_ADMIN_PASSWORD
   ```

4. En la configuración WSGI de PythonAnywhere, agrega la carpeta del proyecto a `sys.path` y expón la aplicación:

   ```python
   import sys

   project_home = "/home/tu_usuario/fti"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   from app import app as application
   ```

5. Configura los archivos estáticos y recarga la aplicación desde el panel de PythonAnywhere. El repositorio incluye el catálogo `productos_nuevos.json` y las imágenes de `static/uploads/productos/`; las imágenes nuevas que agregues a esa carpeta se versionan. Las bases de datos, respaldos, otros archivos subidos y datos de clientes/ventas quedan fuera del repositorio y deben restaurarse o importarse por un procedimiento separado y seguro.

No ejecutes `init-db --demo` en producción. Si importas una base que ya tiene administrador, `INITIAL_ADMIN_PASSWORD` no cambia las credenciales de las cuentas existentes.

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
config.py       configuración (SECRET_KEY y DATABASE_URL obligatoria)
db.py / schema.sql   conexión PostgreSQL y esquema
migrar_postgresql.py utilidad opcional para importar una base SQLite externa
configurar_postgresql.py   prepara PostgreSQL local
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