"""fop_info: картка ФОП з фіскального сервера ДПС (лише читання).

Зараз реалізовано етап 1: python fop_info.py --server-state
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dps.client import DEFAULT_SERVERS, TIMESTAMP_KEY, DpsClient
from dps.errors import DpsError
from dps.throttle import RateLimiter

CONFIG_ENV = "FOP_INFO_CONFIG"


def load_config(path: str | None) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "servers": list(DEFAULT_SERVERS),
        "timeout_seconds": 10,
        "max_requests_per_second": 2,
    }
    path = path or os.environ.get(CONFIG_ENV)
    if path:
        try:
            with open(path, "rb") as fh:
                cfg.update(tomllib.load(fh))
        except FileNotFoundError:
            raise SystemExit(f"Конфіг не знайдено: {path}")
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"Конфіг {path} не розібрано: {exc}")
    return cfg


def parse_server_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def clock_difference(server_time: datetime, now: datetime | None = None) -> timedelta:
    """Час сервера мінус час ПК. Якщо в часі сервера немає пояснення пояса,
    порівнюємо з місцевим часом ПК (це припущення)."""
    if server_time.tzinfo is not None:
        return server_time - (now or datetime.now(timezone.utc))
    return server_time - (now or datetime.now())


def _find_timestamp(response: Any) -> Any:
    if isinstance(response, dict):
        for key, value in response.items():
            if key.lower() == TIMESTAMP_KEY.lower():
                return value
    return None


def run_server_state(client: DpsClient) -> int:
    response = client.server_state()
    scheme = "без шифрування" if (client.last_server or "").startswith("http://") else "захищено"
    print(f"Адреса: {client.last_server} ({scheme})")
    raw = _find_timestamp(response)
    server_time = parse_server_time(raw)
    if server_time is None:
        print(f"Не вдалося знайти або розібрати час сервера (поле {TIMESTAMP_KEY!r}). Відповідь: {response}")
        return 1
    diff = clock_difference(server_time)
    seconds = diff.total_seconds()
    side = "сервер попереду" if seconds > 0 else "сервер відстає" if seconds < 0 else "збігається"
    print(f"Час сервера: {server_time.isoformat()}")
    print(f"Різниця з годинником ПК: {seconds:+.1f} с ({side})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Картка ФОП з фіскального сервера ДПС (лише читання)")
    parser.add_argument("--config", help=f"шлях до config.toml (або змінна {CONFIG_ENV})")
    parser.add_argument("--server-state", action="store_true", help="етап 1: стан сервера без підпису")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    try:
        client = DpsClient(
            cfg["servers"],
            timeout=float(cfg["timeout_seconds"]),
            limiter=RateLimiter(float(cfg["max_requests_per_second"])),
        )
    except ValueError as exc:
        print(f"Помилка конфігурації: {exc}", file=sys.stderr)
        return 2

    try:
        if args.server_state:
            return run_server_state(client)
        print("Картка ФОП ще не реалізована (етапи 2–4). Поки доступно: --server-state", file=sys.stderr)
        return 2
    except DpsError as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
