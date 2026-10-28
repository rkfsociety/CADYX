"""Authentication and bind-safety helpers for the CADYX HTTP host."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import secrets
from pathlib import Path

_MIN_TOKEN_LENGTH = 32


class AuthenticationConfigError(ValueError):
    """Raised when HTTP authentication configuration is unsafe or invalid."""


def is_loopback_host(host: str) -> bool:
    """Return whether a bind host is explicitly limited to loopback."""
    normalized = host.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def normalize_auth_token(token: str | None) -> str | None:
    """Validate a configured reusable token without logging or transforming it."""
    if token is None:
        return None
    if token != token.strip() or any(character.isspace() for character in token):
        raise AuthenticationConfigError("CADYX authentication tokens must not contain whitespace.")
    if len(token) < _MIN_TOKEN_LENGTH:
        raise AuthenticationConfigError(
            f"CADYX authentication tokens must be at least {_MIN_TOKEN_LENGTH} characters long."
        )
    return token


def load_auth_token(environment_token: str | None, token_file: str | None) -> str | None:
    """Load the authentication token from exactly one configured source."""
    if environment_token is not None and token_file:
        raise AuthenticationConfigError(
            "Configure only one authentication token source: CADYX_AUTH_TOKEN or --auth-token-file."
        )
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AuthenticationConfigError(
                f"Could not read CADYX authentication token file: {token_file}"
            ) from exc
        # Accept one optional line ending; extra blank lines remain invalid whitespace.
        token = token.removesuffix("\n").removesuffix("\r")
        return normalize_auth_token(token)
    return normalize_auth_token(environment_token)


def validate_bind_auth(host: str, auth_token: str | None) -> None:
    """Reject non-loopback binds that do not have authentication configured."""
    if not is_loopback_host(host) and auth_token is None:
        raise AuthenticationConfigError(
            "Refusing non-loopback CADYX bind without authentication. "
            "Set CADYX_AUTH_TOKEN or pass --auth-token-file."
        )


def authorization_matches(header_value: str | None, auth_token: str | None) -> bool:
    """Validate Bearer or browser-friendly Basic credentials for one configured token."""
    if auth_token is None:
        return True
    if not header_value:
        return False

    scheme, separator, credentials = header_value.partition(" ")
    if not separator:
        return False
    credentials = credentials.strip()

    if scheme.lower() == "bearer":
        return secrets.compare_digest(credentials, auth_token)

    if scheme.lower() != "basic":
        return False

    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    username, separator, password = decoded.partition(":")
    if not separator or username != "cadyx":
        return False
    return secrets.compare_digest(password, auth_token)
