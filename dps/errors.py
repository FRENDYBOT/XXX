"""Помилки зв'язку з ДПС. Тексти українською, без трасування стека."""

from __future__ import annotations

_HTTP_TEXT = {
    400: "Некоректний запит (400). Перевірте формат команди.",
    401: "Не авторизовано (401): сервер не прийняв підпис або ключ.",
    403: "Доступ заборонено (403): цей ключ не має прав на цю дію або цей ПРРО.",
    404: "Не знайдено (404): перевірте адресу сервера й номер ПРРО.",
    429: "Забагато запитів (429): зменшіть частоту.",
}


class DpsError(Exception):
    """Базова помилка. str(e) придатний для показу користувачу."""


class NoDataError(DpsError):
    """HTTP 204: даних немає."""

    def __init__(self) -> None:
        super().__init__("Даних немає (204): сервер відповів успішно, але нічого не повернув.")


class NetworkError(DpsError):
    """Жодна адреса не відповіла."""


class DpsHttpError(DpsError):
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body
        text = _HTTP_TEXT.get(status)
        if text is None:
            text = f"Сервер повернув помилку {status}."
        if body:
            text += f" Текст від сервера: {body[:300]}"
        super().__init__(text)
