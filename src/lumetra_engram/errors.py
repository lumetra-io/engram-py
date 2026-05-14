from __future__ import annotations

from typing import Any


class EngramError(Exception):
    """Raised for any non-2xx response from the Engram API.

    The original HTTP status is on ``.status`` and the parsed response body
    (if JSON) or raw text is on ``.body``.
    """

    def __init__(self, message: str, status: int, body: Any) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
