"""HTTP-клієнт фіскального сервера ДПС (лише команди, лише читання).

ВАЖЛИВО. Дві константи нижче я взяв з пам'яті, а не з офіційного опису
API, бо опис недоступний автоматично. Перевірте їх в описі (етап 1,
«формат команд») і виправте, якщо відрізняються:
  COMMAND_KEY    ключ із назвою команди в JSON запиту
  TIMESTAMP_KEY  ключ із часом сервера у відповіді ServerState
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from .errors import DpsError, DpsHttpError, NetworkError, NoDataError
from .throttle import RateLimiter
from .whitelist import CMD_PATH, assert_allowed

COMMAND_KEY = "Command"  # ПЕРЕВІРИТИ З ОПИСОМ API
TIMESTAMP_KEY = "Timestamp"  # ПЕРЕВІРИТИ З ОПИСОМ API

DEFAULT_SERVERS = (
    "https://fs.tax.gov.ua:8643/fs",
    "http://fs.tax.gov.ua:8609/fs",
)


def _describe_connection_error(exc: BaseException, timeout: float) -> str:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return f"немає відповіді за {timeout:g} с"
    if isinstance(reason, ssl.SSLError):
        return f"помилка захищеного з'єднання ({reason.__class__.__name__})"
    if isinstance(reason, ConnectionRefusedError):
        return "з'єднання відхилено"
    if isinstance(reason, socket.gaierror):
        return "адресу не вдалося знайти (перевірте інтернет і DNS)"
    return f"помилка з'єднання ({reason})"


class DpsClient:
    def __init__(
        self,
        servers: Sequence[str] = DEFAULT_SERVERS,
        *,
        timeout: float = 10.0,
        limiter: RateLimiter | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not servers:
            raise ValueError("Потрібна принаймні одна адреса сервера")
        self.servers = [s.rstrip("/") for s in servers]
        self.timeout = timeout
        self._limiter = limiter if limiter is not None else RateLimiter()
        self._opener = opener
        self.last_server: str | None = None

    def send(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> Any:
        """Надсилає команду й повертає розібрану JSON-відповідь.

        Без body формується простий JSON (так іде ServerState). Для
        підписаних команд (етап 2) передайте готові підписані байти в body.
        Перевірка білого списку йде першою: заборонена команда в мережу не йде.
        """
        assert_allowed(command, CMD_PATH)
        if body is None:
            body = json.dumps({COMMAND_KEY: command, **(params or {})}).encode("utf-8")

        problems: list[str] = []
        for base in self.servers:
            url = base + CMD_PATH
            self._limiter.wait()
            request = urllib.request.Request(url, data=body, method="POST")
            request.add_header("Content-Type", content_type)
            try:
                with self._opener(request, timeout=self.timeout) as resp:
                    status = resp.status
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                # Сервер відповів, тож інша адреса цього не виправить.
                try:
                    text = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    text = ""
                raise DpsHttpError(exc.code, text) from None
            except (urllib.error.URLError, OSError) as exc:
                problems.append(f"{url}: {_describe_connection_error(exc, self.timeout)}")
                continue

            self.last_server = base
            if status == 204:
                raise NoDataError()
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                raise DpsError("Сервер відповів не у форматі JSON.") from None

        raise NetworkError("Жодна адреса сервера ДПС не відповіла:\n  " + "\n  ".join(problems))

    def server_state(self) -> Any:
        """Команда стану сервера: йде без підпису."""
        return self.send("ServerState")
