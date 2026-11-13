"""Envío de notificaciones Web Push a los usuarios indicados."""
import json
from urllib.parse import urlsplit

from flask import current_app
from pywebpush import WebPushException, webpush
from requests.exceptions import RequestException

from db import get_db, query
from utils import ahora


def endpoint_push_valido(endpoint):
    """Limita los destinos push a servidores conocidos para evitar SSRF."""
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    hosts_validos = (
        host == "fcm.googleapis.com"
        or host.endswith(".push.services.mozilla.com")
        or host == "web.push.apple.com"
        or host == "notify.windows.com"
        or host.endswith(".notify.windows.com")
    )
    return parsed.scheme == "https" and hosts_validos and not parsed.username and not parsed.password


def enviar_a_usuarios(usuario_ids, payload):
    """Envía un aviso a cada dispositivo activo de los usuarios indicados."""
    ids = sorted({int(uid) for uid in usuario_ids if uid})
    if not ids:
        return
    clave_privada = current_app.config.get("VAPID_PRIVATE_KEY", "")
    asunto = current_app.config.get("VAPID_SUBJECT", "")
    if not clave_privada or not asunto:
        current_app.logger.warning("Web Push no configurado; no se pudo enviar la notificación.")
        return
    placeholders = ",".join("?" for _ in ids)
    suscripciones = query(
        f"SELECT endpoint, usuario_id, clave_publica, clave_auth "
        f"FROM notificaciones_push WHERE usuario_id IN ({placeholders})",
        ids,
    )
    for suscripcion in suscripciones:
        try:
            webpush(
                subscription_info={
                    "endpoint": suscripcion["endpoint"],
                    "keys": {
                        "p256dh": suscripcion["clave_publica"],
                        "auth": suscripcion["clave_auth"],
                    },
                },
                data=json.dumps(payload),
                vapid_private_key=clave_privada,
                vapid_claims={"sub": asunto},
                ttl=60,
                timeout=10,
            )
        except (WebPushException, RequestException) as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code in (404, 410):
                get_db().execute(
                    "DELETE FROM notificaciones_push WHERE endpoint = ?",
                    (suscripcion["endpoint"],),
                )
                get_db().commit()
            else:
                current_app.logger.error(
                    "Falló el envío Web Push al usuario %s: %s",
                    suscripcion["usuario_id"],
                    exc,
                )


def avisar_rol(rol, titulo, mensaje, url, tag):
    usuarios = query("SELECT id FROM usuarios WHERE rol = ? AND activo = 1", (rol,))
    enviar_a_usuarios(
        [usuario["id"] for usuario in usuarios],
        {"title": titulo, "body": mensaje, "url": url, "tag": tag},
    )


def cerrar_aviso(usuario_ids, tag):
    enviar_a_usuarios(usuario_ids, {"closeTag": tag})


def cerrar_aviso_especiales(tag):
    usuarios = query(
        "SELECT id FROM usuarios WHERE rol='vendedor' AND tipo_vendedor='especial' AND activo=1"
    )
    cerrar_aviso([usuario["id"] for usuario in usuarios], tag)


def cerrar_aviso_rol(rol, tag):
    usuarios = query("SELECT id FROM usuarios WHERE rol = ? AND activo = 1", (rol,))
    cerrar_aviso([usuario["id"] for usuario in usuarios], tag)


def guardar_suscripcion(usuario_id, suscripcion):
    endpoint = suscripcion.get("endpoint", "")
    claves = suscripcion.get("keys") or {}
    clave_publica, clave_auth = claves.get("p256dh", ""), claves.get("auth", "")
    if (not isinstance(endpoint, str) or len(endpoint) > 2048
            or not endpoint_push_valido(endpoint)
            or not isinstance(clave_publica, str) or not 1 <= len(clave_publica) <= 200
            or not isinstance(clave_auth, str) or not 1 <= len(clave_auth) <= 100):
        raise ValueError("La suscripción de notificaciones no es válida.")
    db = get_db()
    db.execute(
        """INSERT INTO notificaciones_push
           (endpoint, usuario_id, clave_publica, clave_auth, creado)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (endpoint) DO UPDATE SET usuario_id=excluded.usuario_id,
             clave_publica=excluded.clave_publica, clave_auth=excluded.clave_auth,
             creado=excluded.creado""",
        (endpoint, usuario_id, clave_publica, clave_auth, ahora()),
    )
    db.commit()
