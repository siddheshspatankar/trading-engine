"""
Security utilities: JWT authentication, password hashing, API key validation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from src.core.exceptions import TradingEngineError
from src.infrastructure.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthenticationError(TradingEngineError):
    """Raised when JWT validation or password verification fails."""


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """
    Create a signed JWT access token.

    Args:
        subject: The subject claim (typically user ID or service name).
        extra_claims: Additional claims to embed in the token.

    Returns:
        Signed JWT string.
    """
    settings = get_settings()
    expire = datetime.utcnow() + timedelta(minutes=settings.security.jwt_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        settings.security.jwt_secret_key.get_secret_value(),
        algorithm=settings.security.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.

    Raises:
        AuthenticationError: If the token is invalid or expired.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.security.jwt_secret_key.get_secret_value(),
            algorithms=[settings.security.jwt_algorithm],
        )
        return payload
    except JWTError as exc:
        raise AuthenticationError(f"Token validation failed: {exc}") from exc
