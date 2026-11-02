"""Punto de entrada de la aplicación Flask (fábrica de la app + comandos CLI)."""
import os
from datetime import timedelta, timezone

import click
from flask import Flask, g, render_template, request, session

import db
from config import Config
from services import (contar_por_armar, estado_vencimiento, get_config, productos_en_alerta,
                      vendedores_especiales)
from utils import (csrf_field, csrf_token, fmt_fecha, fmt_fechahora, fmt_money, fmt_num,
                   fmt_qty, hoy, verificar_csrf)


def create_app():
    """Crea y configura la aplicación: base de datos, filtros, seguridad y rutas."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if not app.config.get("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL no está configurada. Esta aplicación requiere PostgreSQL y no usa SQLite."
        )
    db.init_app(app)

    # Actualiza el esquema existente y elimina los campos de venta al detal.
    with app.app_context():
        connection = db.get_db()
        if db.table_exists(connection, "usuarios"):
            db.migrar_roles_y_colores(connection)
            db.migrar_solo_mayor(connection)

    # ----------------------------------------------------------- filtros Jinja
    app.add_template_filter(fmt_num, "num")            # {{ 1234.5|num }} -> 1.234,50
    app.add_template_filter(fmt_qty, "qty")            # cantidades sin ceros sobrantes
    app.add_template_filter(fmt_fecha, "fecha")
    app.add_template_filter(fmt_fechahora, "fechahora")
    app.add_template_filter(fmt_money, "money")        # {{ 10|money }} -> $ 10,00
    # visible también dentro de los macros importados (los macros no ven el contexto)
    app.add_template_global(estado_vencimiento, "estado_deuda")
    app.add_template_global(vendedores_especiales, "vendedores_especiales")   # para elegir quién arma un pedido

    # --------------------------------------------------- antes de cada petición
    @app.before_request
    def cargar_usuario():
        """Carga el usuario de la sesión en g.user y valida el token CSRF en los POST."""
        uid = session.get("uid")
        g.user = db.query("SELECT * FROM usuarios WHERE id = ? AND activo = 1", (uid,), one=True) if uid else None
        if request.method == "POST":
            verificar_csrf()

    # -------------------------------------------- variables disponibles en plantillas
    @app.context_processor
    def globales():
        """Inyecta en todas las plantillas: nombre de empresa, CSRF y utilidades."""
        datos = {
            "csrf_field": csrf_field, "csrf_token": csrf_token,
            "estado_deuda": estado_vencimiento, "hoy": hoy(),
        }
        if g.get("user") is not None:
            datos["empresa"] = get_config("empresa_nombre", "Inventario")
            es_esp = g.user["rol"] == "vendedor" and g.user["tipo_vendedor"] == "especial"
            # admin y vendedores especiales pueden subir/editar productos y precios
            datos["puede_ver_stock"] = g.user["rol"] == "admin" or es_esp
            datos["es_especial"] = es_esp
            if g.user["rol"] == "vendedor":
                datos["n_carrito"] = len(session.get("carrito", {}))
            if g.user["rol"] == "admin" or es_esp:
                datos["n_por_armar"] = contar_por_armar(g.user)
            if g.user["rol"] == "admin" or es_esp:
                datos["n_pendientes"] = db.query(
                    "SELECT COUNT(*) AS n FROM ventas WHERE estado='pendiente'", one=True)["n"]
            if g.user["rol"] == "admin":
                datos["n_stock_alerta"] = len(productos_en_alerta())
        return datos

    # --------------------------------------------------------------- rutas
    from routes import admin, armados, auth, catalogo, clientes, deudas, panel, ventas
    for modulo in (auth, panel, clientes, catalogo, ventas, deudas, admin, armados):
        app.register_blueprint(modulo.bp)

    # La limpieza diaria se puede desactivar con ENABLE_ARCHIVE_SCHEDULER=0.
    if os.environ.get("ENABLE_ARCHIVE_SCHEDULER", "1") == "1" and (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            zona_local = timezone(timedelta(hours=app.config.get("UTC_OFFSET_HORAS", -4)))
            scheduler = BackgroundScheduler(daemon=True, timezone=zona_local)
            def _limpiar_archivos_diarios_job():
                with app.app_context():
                    try:
                        from archivos import limpiar_archivos_historicos_vencidos
                        limpiar_archivos_historicos_vencidos()
                    except Exception:
                        app.logger.exception("Falló la limpieza diaria de archivos históricos")
            scheduler.add_job(_limpiar_archivos_diarios_job, "cron", hour=0, minute=0,
                              id="limpieza_archivo_historico_diaria",
                              replace_existing=True, max_instances=1)
            scheduler.start()
            app.extensions["archive_scheduler"] = scheduler
        except ImportError:
            app.logger.warning("APScheduler no instalado: los archivos vencidos se limpiarán al abrir la sección o generar el PDF.")

    # ----------------------------------------------------- páginas de error
    @app.errorhandler(403)
    def prohibido(e):
        """Página para accesos sin permiso."""
        return render_template("error.html", codigo=403, mensaje="No tienes permiso para ver esta página."), 403

    @app.errorhandler(404)
    def no_encontrado(e):
        """Página para rutas inexistentes."""
        return render_template("error.html", codigo=404, mensaje="No encontramos lo que buscas."), 404

    @app.errorhandler(400)
    def mala_peticion(e):
        """Página para peticiones inválidas (p. ej. token CSRF vencido)."""
        return render_template("error.html", codigo=400, mensaje=getattr(e, "description", "Petición inválida.")), 400

    # ------------------------------------------------------ comando de consola
    @app.cli.command("init-db")
    @click.option("--demo", is_flag=True, help="Carga datos de ejemplo.")
    def init_db_cmd(demo):
        """Crea la base de datos (flask --app app init-db [--demo])."""
        if not os.environ.get("INITIAL_ADMIN_PASSWORD"):
            raise click.ClickException(
                "Define INITIAL_ADMIN_PASSWORD antes de inicializar una base sin administrador."
            )
        if demo and not os.environ.get("DEMO_USER_PASSWORD"):
            raise click.ClickException(
                "Define DEMO_USER_PASSWORD para cargar los vendedores de demostración."
            )
        db.init_db(demo=demo)
        click.echo("Base de datos lista.")

    @app.cli.command("archive-daily")
    def archive_daily_cmd():
        """Genera o actualiza manualmente el PDF histórico de hoy."""
        from archivos import generar_archivo_diario
        fila, creado = generar_archivo_diario(None)
        click.echo(("PDF generado: " if creado else "PDF actualizado: ") + fila["ruta"])

    @app.cli.command("migrate-sqlite-to-postgresql")
    @click.option("--source", required=True, help="Archivo SQLite de origen; nunca se modifica.")
    def migrate_sqlite_to_postgresql_cmd(source):
        """Copia los datos SQLite a una base PostgreSQL vacía."""
        if not app.config.get("DATABASE_URL"):
            raise click.ClickException("Define DATABASE_URL para conectar con PostgreSQL.")
        from migrar_postgresql import migrate_sqlite_to_postgresql
        with app.app_context():
            migrate_sqlite_to_postgresql(source, db.get_db())
        click.echo("Migración terminada. SQLite se conservó sin cambios.")

    return app


app = create_app()

if __name__ == "__main__":
    # PostgreSQL se inicializa de forma idempotente.
    if app.config.get("DATABASE_URL"):
        with app.app_context():
            db.init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
