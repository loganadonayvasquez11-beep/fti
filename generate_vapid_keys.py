"""Genera una clave VAPID para notificaciones Web Push."""
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main():
    ruta = Path(__file__).resolve().parent / "instance" / "vapid_private.pem"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists():
        raise FileExistsError(f"Ya existe {ruta}; no se sobrescribió.")

    clave = ec.generate_private_key(ec.SECP256R1())
    with ruta.open("xb") as archivo:
        archivo.write(clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        print(f"Advertencia: no se pudo restringir automáticamente el permiso de {ruta}.")

    publica = clave.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    valor_publico = base64.urlsafe_b64encode(publica).rstrip(b"=").decode("ascii")
    print(f"VAPID_PRIVATE_KEY={ruta}")
    print(f"VAPID_PUBLIC_KEY={valor_publico}")
    print("Guarda el archivo de clave privada en el servidor y no lo compartas.")


if __name__ == "__main__":
    main()
