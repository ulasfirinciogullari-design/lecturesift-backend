"""Safe classification of Resend delivery failures.

The provider's raw message can contain operational details and is never returned
to the client. This module reduces known responses to a stable, non-secret code
that tells operators which dashboard setting needs attention.
"""

from __future__ import annotations


_GENERIC_TYPES = {
    "",
    "internalservererror",
    "internal_server_error",
    "applicationerror",
    "application_error",
    "resend_error",
}


def classify_resend_error(
    *,
    status: int | None,
    error_type: str | None,
    message: str | None,
) -> str:
    normalized_type = (error_type or "").strip()
    compact_type = normalized_type.casefold().replace(" ", "")
    normalized_message = " ".join((message or "").casefold().split())

    if "api key is invalid" in normalized_message or "invalid api key" in normalized_message:
        return "invalid_api_key"
    if "missing api key" in normalized_message:
        return "missing_api_key"
    if "restricted to only send emails" in normalized_message:
        return "restricted_api_key"
    if "only send testing emails to your own email address" in normalized_message:
        return "testing_recipient_restricted"
    if "domain" in normalized_message and (
        "is not verified" in normalized_message
        or "isn't verified" in normalized_message
        or "not verified" in normalized_message
    ):
        return "domain_not_verified"
    if "domain has been registered already" in normalized_message:
        return "domain_registered_elsewhere"
    if "access denied" in normalized_message and "1010" in normalized_message:
        return "access_denied_1010"
    if "permission" in normalized_message and "domain" in normalized_message:
        return "api_key_domain_mismatch"

    if compact_type not in _GENERIC_TYPES:
        return normalized_type[:80]
    if status == 403:
        return "forbidden_unknown"
    if status == 401:
        return "authentication_unknown"
    if status is not None:
        return f"http_{status}"
    return "unknown_delivery_error"
