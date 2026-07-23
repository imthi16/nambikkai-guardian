"""Application-layer security hardening: headers, CORS, rate limits, body caps.

These controls are defense in depth on top of the authorization, tenant
isolation, and upload-validation boundaries enforced elsewhere. They are wired
onto the app in `app.main.create_app` via `configure_security`.
"""

from app.security.middleware import configure_security

__all__ = ["configure_security"]
