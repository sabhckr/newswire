from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id with argon2-cffi defaults (RFC 9106 low-memory profile).
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.lower() == password or password.upper() == password:
        if not any(c.isdigit() for c in password) and not any(not c.isalnum() for c in password):
            problems.append("Use a mix of upper/lower case, digits or symbols")
    return problems


# Pre-computed hash used to keep login timing constant for unknown emails.
DUMMY_HASH = hash_password("newswire-timing-equaliser")
