"""Configuración compartida de los puntos públicos de Tod4S3."""
from __future__ import annotations

import os

DEFAULT_PUBLIC_SITE_URL = "https://tod4s3pro-production.up.railway.app"
ANDROID_UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/yenihernandez971031-lang/tod4S3pro/main/web/android-update.json"
)


def public_site_url() -> str:
    """Devuelve la web canónica que debe aparecer en Telegram y en la aplicación."""
    configured = os.getenv("PUBLIC_SITE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
    return f"https://{railway_domain}" if railway_domain else f"{DEFAULT_PUBLIC_SITE_URL}/#inicio"


def public_site_origin() -> str:
    """Devuelve la web sin fragmento para enlaces HTTP y metadatos."""
    return public_site_url().split("#", 1)[0].rstrip("/")
