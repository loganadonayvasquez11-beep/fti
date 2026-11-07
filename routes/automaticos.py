"""Endpoints protegidos para integraciones automáticas externas."""
import hmac
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, abort, current_app, request, send_file

from archivos import generar_archivo_diario, resolver_ruta_archivo_historico
from utils import hoy

bp = Blueprint("automaticos", __name__)


@bp.route("/integraciones/historico-diario.pdf")
def historico_diario_pdf():
    """Genera y entrega el PDF del día anterior a la automatización autorizada."""
    token_configurado = current_app.config.get("DAILY_ARCHIVE_TOKEN", "")
    token_recibido = request.headers.get("X-Archive-Token", "")
    if not token_configurado:
        abort(503)
    if not hmac.compare_digest(token_recibido, token_configurado):
        abort(401)

    fecha = datetime.strptime(hoy(), "%Y-%m-%d").date() - timedelta(days=1)
    fila, _ = generar_archivo_diario(fecha_reporte=fecha.strftime("%Y-%m-%d"))
    ruta = resolver_ruta_archivo_historico(fila["ruta"])
    carpeta = (Path(current_app.instance_path) / "archivos_historicos").resolve()
    if carpeta not in ruta.parents or not ruta.is_file():
        abort(404)
    respuesta = send_file(
        ruta,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=ruta.name,
        conditional=False,
        etag=False,
    )
    respuesta.headers["Cache-Control"] = "private, no-store"
    return respuesta
