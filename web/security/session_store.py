"""Redis-backed сессионное middleware для распределённого хранения сессий с TTL."""

import base64
import json
import logging
import secrets
from typing import Any

from itsdangerous import BadSignature, Signer
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


class RedisSessionMiddleware:
    """Распределённое хранилище сессий на базе Redis.

    - При доступном Redis: хранит состояние сессии в Redis по ключу `session:{session_id}`
      с установкой TTL (по умолчанию 24 часа), а в cookie клиента помещает подписанный ID сессии.
      Любой воркер Uvicorn может прозрачно читать и изменять сессию.
    - При недоступном Redis (или локальном запуске без Redis): автоматически переключается
      на подписанную cookie-сессию Starlette с локальной сериализацией, обеспечивая zero-downtime.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret_key: str,
        session_cookie: str = "session",
        max_age: int | None = None,
        path: str = "/",
        same_site: str = "lax",
        https_only: bool = False,
    ) -> None:
        self.app = app
        self.signer = Signer(secret_key)
        self.session_cookie = session_cookie
        self.max_age = max_age or settings.SESSION_TTL
        self.path = path
        self.security_flags = f"path={path}; samesite={same_site}"
        if https_only:
            self.security_flags += "; secure"
        self.security_flags += "; httponly"

    async def _load_session(
        self, raw_cookie: str | None, redis: Any
    ) -> tuple[str | None, dict[str, Any]]:
        if not raw_cookie:
            return None, {}
        try:
            unsigned = self.signer.unsign(raw_cookie.encode("utf-8")).decode("utf-8")
            if unsigned.startswith("s_"):
                session_id = unsigned[2:]
                if redis is not None:
                    val = await redis.get(f"session:{session_id}")
                    if val:
                        return session_id, json.loads(val)
                return session_id, {}
            return None, json.loads(base64.b64decode(unsigned).decode("utf-8"))
        except (BadSignature, Exception) as exc:
            logger.debug("Не удалось восстановить сессию: %s", exc)
            return None, {}

    async def _persist_session(
        self, current_session: dict[str, Any], session_id: str | None, redis: Any
    ) -> tuple[str, str | None]:
        cookie_val = ""
        if redis is not None:
            if not session_id:
                session_id = secrets.token_urlsafe(32)
            serialized = json.dumps(current_session, ensure_ascii=False, default=str)
            try:
                await redis.set(f"session:{session_id}", serialized, ex=self.max_age)
                cookie_val = self.signer.sign(f"s_{session_id}".encode()).decode("utf-8")
            except Exception as e:
                logger.warning("Ошибка записи сессии в Redis, fallback на cookie: %s", e)
                session_id = None

        if not cookie_val:
            b64_data = base64.b64encode(json.dumps(current_session).encode("utf-8")).decode(
                "utf-8"
            )
            cookie_val = self.signer.sign(b64_data.encode("utf-8")).decode("utf-8")

        return cookie_val, session_id

    async def _refresh_session_ttl(self, session_id: str | None, redis: Any) -> None:
        """Ошибка #8 — скользящее продление сессии (sliding expiration).

        При каждом авторизованном входящем запросе обновляем TTL ключа сессии
        в Redis (EXPIRE session:{id}), предотвращая принудительное разлогинивание
        активно работающего администратора: таймаут отсчитывается от последней
        активности, а не от момента входа.
        """
        if redis is None or not session_id:
            return
        try:
            await redis.expire(f"session:{session_id}", self.max_age)
        except Exception as e:
            logger.debug("Не удалось продлить TTL сессии %s: %s", session_id, e)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        raw_cookie = connection.cookies.get(self.session_cookie)
        redis = await get_redis_client()
        session_id, data = await self._load_session(raw_cookie, redis)

        # Sliding expiration: продлеваем TTL только для живых сессий
        # (с данными) при каждом входящем запросе.
        if session_id and data:
            await self._refresh_session_ttl(session_id, redis)

        scope["session"] = data
        initial_hash = hash(json.dumps(data, sort_keys=True, default=str))

        async def send_wrapper(message: Message) -> None:
            nonlocal session_id
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                current_session = scope.get("session", {})

                if not current_session and (raw_cookie or session_id):
                    if redis is not None and session_id:
                        try:
                            await redis.delete(f"session:{session_id}")
                        except Exception as e:
                            logger.debug("Не удалось удалить сессию из Redis: %s", e)
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}=; max-age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT; {self.security_flags}",
                    )
                elif current_session and (
                    hash(json.dumps(current_session, sort_keys=True, default=str)) != initial_hash
                    or not raw_cookie
                ):
                    cookie_val, session_id = await self._persist_session(
                        current_session, session_id, redis
                    )
                    cookie_header = (
                        f"{self.session_cookie}={cookie_val}; "
                        f"max-age={self.max_age}; {self.security_flags}"
                    )
                    headers.append("Set-Cookie", cookie_header)

            await send(message)

        await self.app(scope, receive, send_wrapper)
