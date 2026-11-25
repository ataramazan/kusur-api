from collections import defaultdict, deque
import hmac
import threading
import time

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from config import Settings, get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_rapidapi_secret_header = APIKeyHeader(name="X-RapidAPI-Proxy-Secret", auto_error=False)


class SlidingWindowRateLimiter:
    """İstemci başına dakikalık kayan pencere hız sınırlayıcı."""

    def __init__(self, window_seconds: int = 60) -> None:
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, client_id: str, limit: int) -> tuple[bool, int, int]:
        """İsteğe izin verilip verilmediğini, kalan hakkı ve bekleme süresini döner."""
        if limit <= 0:
            return True, 0, 0

        now = time.monotonic()
        cutoff = now - self._window_seconds

        with self._lock:
            timestamps = self._hits[client_id]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= limit:
                retry_after = max(1, int(self._window_seconds - (now - timestamps[0])) + 1)
                return False, 0, retry_after

            timestamps.append(now)
            remaining = max(0, limit - len(timestamps))
            return True, remaining, 0


rate_limiter = SlidingWindowRateLimiter()


def _matches_any(candidate: str | None, valid_values: list[str]) -> bool:
    if not candidate:
        return False
    return any(hmac.compare_digest(candidate, valid) for valid in valid_values)


def authenticate_client(
    request: Request,
    settings: Settings = Depends(get_settings),
    api_key: str | None = Security(_api_key_header),
    rapidapi_secret: str | None = Security(_rapidapi_secret_header),
) -> str:
    """X-API-Key veya RapidAPI proxy sırrı ile istemci erişimini doğrular."""
    has_auth_rules = bool(settings.api_access_keys or settings.rapidapi_proxy_secret)

    if not has_auth_rules:
        if settings.environment == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Sunucu güvenlik yapılandırması eksik: API_ACCESS_KEYS veya RAPIDAPI_PROXY_SECRET tanımlanmalı.",
            )
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"

    if settings.rapidapi_proxy_secret and rapidapi_secret:
        if hmac.compare_digest(rapidapi_secret, settings.rapidapi_proxy_secret):
            rapid_user = request.headers.get("X-RapidAPI-User", "rapidapi")
            return f"rapidapi:{rapid_user}"

    if settings.api_access_keys and _matches_any(api_key, settings.api_access_keys):
        assert api_key is not None
        return f"key:{api_key[:6]}"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Geçersiz veya eksik API anahtarı.",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def enforce_rate_limit(
    client_id: str = Depends(authenticate_client),
    settings: Settings = Depends(get_settings),
) -> tuple[str, int]:
    """Kimliği doğrulanmış istemci için hız sınırını uygular."""
    allowed, remaining, retry_after = rate_limiter.check(
        client_id=client_id,
        limit=settings.rate_limit_per_minute,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Dakikalık istek limitini aştınız. Lütfen biraz bekleyip tekrar deneyin.",
            headers={"Retry-After": str(retry_after)},
        )
    return client_id, remaining
