"""Білий список команд. Усе поза ним програма не надсилає.

DeviceRegister прив'язує ПРРО до комп'ютера і зламає роботу справжньої
каси, а /doc створює документи (чеки, зміни). Тому перевірка стоїть
перед відправкою, в одному місці, і працює за точним збігом назви.
"""

from __future__ import annotations

ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "ServerState",
        "Objects",
        "Operators",
        "TransactionsRegistrarState",
        "Shifts",
        "ZRep",
    }
)

# Єдиний дозволений шлях запиту
CMD_PATH = "/cmd"


class ForbiddenCommandError(Exception):
    """Команда чи шлях поза білим списком. Запит не відправлено."""


def assert_allowed(command: object, path: str = CMD_PATH) -> None:
    if not isinstance(command, str) or command not in ALLOWED_COMMANDS:
        shown = command if isinstance(command, str) and len(command) <= 60 else "<некоректне значення>"
        raise ForbiddenCommandError(
            f"Команда {shown!r} не входить до білого списку: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )
    if path != CMD_PATH:
        raise ForbiddenCommandError(f"Дозволений лише шлях {CMD_PATH!r}, отримано {path!r}")
