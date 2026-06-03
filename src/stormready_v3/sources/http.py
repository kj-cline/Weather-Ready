from __future__ import annotations

import json
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stormready_v3.config.settings import HTTP_TIMEOUT_SECONDS, HTTP_USER_AGENT


class JsonHttpClient(Protocol):
    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> dict: ...


class UrllibJsonClient:
    def __init__(self, *, timeout_seconds: float = HTTP_TIMEOUT_SECONDS, user_agent: str = HTTP_USER_AGENT) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> dict:
        merged_headers = {"User-Agent": self.user_agent}
        if headers:
            merged_headers.update(headers)
        request = Request(url, headers=merged_headers)
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))


def build_url(base_url: str, params: dict[str, object]) -> str:
    encoded = urlencode({key: value for key, value in params.items() if value is not None})
    joiner = "&" if "?" in base_url else "?"
    return f"{base_url}{joiner}{encoded}"
