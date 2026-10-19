"""Server-side verification of Google Identity Services ID tokens.

Disabled unless GOOGLE_CLIENT_ID is configured. The token is verified by
Google's tokeninfo endpoint, which checks the signature against Google's
certificates and the expiry; the audience, issuer, expiry and verified-email
claims are then checked here again. The token travels in a POST body, never
in a URL, and is never logged or stored.
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
        # A form body keeps the token out of URLs and therefore out of proxy logs.
        response = httpx.post(GOOGLE_TOKENINFO_URL, data={"id_token": credential}, timeout=10.0)
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
    """Return the verified email, subject, hosted domain and names of a token."""
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
    raw_email = str(claims.get("email") or "").strip()
    sub = str(claims.get("sub") or "").strip()
    hosted_domain = str(claims.get("hd") or "").strip()
    if (
        str(claims.get("aud") or "") != client_id
        or str(claims.get("iss") or "") not in GOOGLE_ISSUERS
        or expires_at <= int(time.time())
        # tokeninfo returns the boolean claim as a string.
        or str(claims.get("email_verified", "")).lower() != "true"
        # Checked before casefold(), which can map non-ASCII text onto
        # another address.
        or not raw_email
        or not raw_email.isascii()
        or not sub
        or len(sub) > 255
        or not sub.isascii()
        or not sub.isprintable()
        or not hosted_domain.isascii()
    ):
        raise BillingAuthenticationError(_INVALID)
    return {
        "email": raw_email.casefold(),
        "sub": sub,
        "hd": hosted_domain.casefold(),
        "given_name": str(claims.get("given_name") or "")[:200],
        "family_name": str(claims.get("family_name") or "")[:200],
    }
