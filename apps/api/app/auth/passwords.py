"""Argon2id password hashing.

Argon2id is the current OWASP first-choice password hash; the argon2-cffi
defaults track the library's recommended parameters, so we do not pin our own.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

# Verified against a real hash of an unguessable value whenever the account
# does not exist, so lookup failures cost the same as password failures.
_DUMMY_HASH = _hasher.hash("equalize-timing-for-unknown-accounts")


def hash_password(password: str) -> str:
    """Return an encoded Argon2id hash safe to store."""
    return _hasher.hash(password)


def verify_password(password_hash: str, candidate: str) -> bool:
    """Check a candidate password against a stored hash without raising."""
    try:
        return _hasher.verify(password_hash, candidate)
    except (VerifyMismatchError, InvalidHashError):
        return False


def burn_verification_time(candidate: str) -> None:
    """Spend one hash verification so missing users are not detectable by timing."""
    verify_password(_DUMMY_HASH, candidate)
