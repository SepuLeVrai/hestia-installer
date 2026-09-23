from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from installer.constants import (
    BOOTSTRAP_SESSION_TTL_SECONDS,
    BOOTSTRAP_TOKEN_MAX_ATTEMPTS,
    BOOTSTRAP_TOKEN_TTL_SECONDS,
)

_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class BootstrapToken:
    def __init__(
        self,
        plaintext: str,
        *,
        ttl_seconds: int = BOOTSTRAP_TOKEN_TTL_SECONDS,
        max_attempts: int = BOOTSTRAP_TOKEN_MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_attempts <= 0:
            raise ValueError("TTL et nombre de tentatives doivent être positifs")
        self._clock = clock
        self._created_at = clock()
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._attempts = 0
        self._consumed = False
        self._salt = secrets.token_bytes(16)
        self._digest = self._derive(plaintext)

    @classmethod
    def generate(
        cls,
        *,
        ttl_seconds: int = BOOTSTRAP_TOKEN_TTL_SECONDS,
        max_attempts: int = BOOTSTRAP_TOKEN_MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> tuple[str, "BootstrapToken"]:
        left = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(4))
        right = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(4))
        plaintext = f"HST-{left}-{right}"
        return plaintext, cls(
            plaintext,
            ttl_seconds=ttl_seconds,
            max_attempts=max_attempts,
            clock=clock,
        )

    def _derive(self, plaintext: str) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), self._salt, 120_000)

    @property
    def expired(self) -> bool:
        return (self._clock() - self._created_at) >= self._ttl

    @property
    def locked(self) -> bool:
        return self._attempts >= self._max_attempts

    @property
    def consumed(self) -> bool:
        return self._consumed

    @property
    def usable(self) -> bool:
        return not self.expired and not self.locked and not self.consumed

    def verify_and_consume(self, candidate: str) -> bool:
        if not self.usable:
            return False
        self._attempts += 1
        candidate_digest = self._derive(candidate)
        valid = hmac.compare_digest(candidate_digest, self._digest)
        if valid:
            self._consumed = True
            self._digest = b"\x00" * len(self._digest)
        return valid


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    csrf_token: str
    expires_at: float


class SessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = BOOTSTRAP_SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("TTL de session invalide")
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def _prune(self) -> None:
        now = self._clock()
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)

    def create(self) -> Session:
        self._prune()
        session = Session(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=self._clock() + self._ttl,
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        self._prune()
        return self._sessions.get(session_id)

    def delete(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def clear(self) -> None:
        self._sessions.clear()
