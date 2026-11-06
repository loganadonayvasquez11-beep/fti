"""Shared responses and helpers for downloadable and viewable PDF documents."""
import re
import unicodedata

from flask import render_template, request, send_file, url_for


def nombre_pdf_cliente(cliente, tipo):
    """Build a Windows-safe PDF name while preserving the customer's name."""
    nombre = unicodedata.normalize("NFKC", f"{cliente} - {tipo}")
    nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", nombre)
    nombre = re.sub(r"\s+", " ", nombre).strip(" .-")
    return f"{nombre[:180].rstrip(' .-') or 'cliente'}.pdf"


def responder_pdf(archivo, nombre):
    """Offer download/view options, or return the PDF with the selected disposition."""
    accion = request.args.get("action")
    if accion in ("download", "view"):
        return send_file(
            archivo,
            mimetype="application/pdf",
            as_attachment=accion == "download",
            download_name=nombre,
        )

    argumentos = dict(request.view_args or {})
    return render_template(
        "pdf/opciones.html",
        nombre=nombre,
        url_descargar=url_for(request.endpoint, **argumentos, action="download"),
        url_visualizar=url_for(request.endpoint, **argumentos, action="view"),
    )
