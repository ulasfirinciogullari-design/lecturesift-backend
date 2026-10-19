"""Server-side verification of Google Identity Services ID tokens.

Disabled unless GOOGLE_CLIENT_ID is configured. The token is verified by
Google's tokeninfo endpoint, which checks the signature against Google's
certificates and the expiry; the audience, issuer, expiry and verified-email
claims are then checked here again. Tokens are never logged or stored.
"""

from __future__ import annotations

import re
import time

import httpx

from . import config
from .billing_service import BillingAuthenticationError, BillingConfigurationError

GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
_CLIENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z._-]{8,200}\.apps\.googleusercontent\.com$")
_INVALID = "Google ile giriş doğrulanamadı. Tekrar dene."


class GoogleSignInUnavailable(BillingConfigurationError):
    """Google could not be reached; the credential was not judged invalid."""


def google_client_id() -> str:
    """Return the configured web client id, or "" when sign-in is disabled."""
    client_id = str(config.GOOGLE_CLIENT_ID or "").strip()
    return client_id if _CLIENT_ID_PATTERN.fullmatch(client_id) else ""


def _fetch_tokeninfo(credential: str) -> dict:
    try:
        response = httpx.get(GOOGLE_TOKENINFO_URL, params={"id_token": credential}, timeout=10.0)
    except httpx.HTTPError as exc:
        raise GoogleSignInUnavailable("Google'a şu anda ulaşılamıyor. Biraz sonra tekrar dene.") from exc
    if response.status_code >= 500:
        raise GoogleSignInUnavailable("Google'a şu anda ulaşılamıyor. Biraz sonra tekrar dene.")
    if response.status_code != 200:
        # Google answers 400 for a malformed, expired or badly signed token.
        raise BillingAuthenticationError(_INVALID)
    try:
        body = response.json()
    except ValueError as exc:
        raise BillingAuthenticationError(_INVALID) from exc
    if not isinstance(body, dict):
        raise BillingAuthenticationError(_INVALID)
    return body


def verify_google_id_token(credential: str) -> dict:
    """Return the verified email and profile names of a Google ID token."""
    client_id = google_client_id()
    if not client_id:
        raise BillingConfigurationError("Google ile giriş etkin değil.")
    selected = str(credential or "").strip()
    if not selected or len(selected) > 4096 or selected.count(".") != 2:
        raise BillingAuthenticationError(_INVALID)
    claims = _fetch_tokeninfo(selected)
    try:
        expires_at = int(str(claims.get("exp", "0")))
    except ValueError:
        expires_at = 0
    email = str(claims.get("email") or "").strip().casefold()
    if (
        str(claims.get("aud") or "") != client_id
        or str(claims.get("iss") or "") not in GOOGLE_ISSUERS
        or expires_at <= int(time.time())
        # tokeninfo returns the boolean claim as a string.
        or str(claims.get("email_verified", "")).lower() != "true"
        or not email
        or not str(claims.get("sub") or "")
    ):
        raise BillingAuthenticationError(_INVALID)
    return {
        "email": email,
        "sub": str(claims["sub"]),
        "given_name": str(claims.get("given_name") or "")[:200],
        "family_name": str(claims.get("family_name") or "")[:200],
    }
