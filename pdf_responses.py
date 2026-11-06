"""Shared responses and helpers for downloadable and viewable PDF documents."""
import re
import unicodedata
from io import BytesIO

from flask import render_template, request, send_file, url_for
from pypdf import PdfReader


def nombre_pdf_cliente(cliente, tipo):
    """Build a Windows-safe PDF name while preserving the customer's name."""
    nombre = unicodedata.normalize("NFKC", f"{cliente} - {tipo}")
    nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", nombre)
    nombre = re.sub(r"\s+", " ", nombre).strip(" .-")
    return f"{nombre[:180].rstrip(' .-') or 'cliente'}.pdf"


def responder_pdf(archivo, nombre):
    """Open the PDF editor, or return the scaled PDF for preview or download."""
    accion = request.args.get("action")
    try:
        escala = max(25, min(200, int(request.args.get("scale", "100"))))
    except (TypeError, ValueError):
        escala = 100

    escalable = callable(archivo)
    if accion in ("preview", "download", "view"):
        if escalable:
            archivo = archivo(escala)
        if isinstance(archivo, (bytes, bytearray)):
            contenido = bytes(archivo)
        elif hasattr(archivo, "read"):
            if archivo.seekable():
                archivo.seek(0)
            contenido = archivo.read()
        else:
            contenido = archivo.read_bytes()
        paginas = len(PdfReader(BytesIO(contenido), strict=False).pages)
        respuesta = send_file(
            BytesIO(contenido),
            mimetype="application/pdf",
            as_attachment=accion == "download",
            download_name=nombre,
            conditional=False,
            etag=False,
        )
        respuesta.headers["X-PDF-Page-Count"] = str(paginas)
        return respuesta

    argumentos = dict(request.view_args or {})
    return render_template(
        "pdf/opciones.html",
        nombre=nombre,
        escalable=escalable,
        url_preview=url_for(request.endpoint, **argumentos, action="preview", scale=100) if escalable else None,
        url_descargar=url_for(request.endpoint, **argumentos, action="download", scale=100),
        url_visualizar=url_for(request.endpoint, **argumentos, action="view", scale=100),
    )
