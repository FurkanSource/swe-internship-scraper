"""Bounded HTTP JSON client shared by official ATS providers."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from collections.abc import Mapping
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import __version__


class RequestsJsonClient:
    def __init__(
        self,
        timeout: tuple[float, float] = (4.0, 20.0),
        min_host_interval: float = 0.15,
        max_response_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.timeout = timeout
        self.min_host_interval = max(0.0, float(min_host_interval))
        self.max_response_bytes = int(max_response_bytes)
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self._local = threading.local()
        self._rate_lock = threading.Lock()
        self._next_request: dict[str, float] = {}

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if isinstance(session, requests.Session):
            return session
        session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=2,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": f"swe-internship-scraper/{__version__}",
            }
        )
        self._local.session = session
        return session

    def _wait_for_host(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).hostname or ""
        with self._rate_lock:
            now = time.monotonic()
            allowed = self._next_request.get(host, now)
            delay = max(0.0, allowed - now)
            self._next_request[host] = max(now, allowed) + self.min_host_interval
        if delay:
            time.sleep(delay)

    def _decode(self, response: requests.Response) -> Any:
        content = self._bounded_content(response)
        content_type = response.headers.get("Content-Type", "").casefold()
        if "json" not in content_type and content.lstrip()[:1] not in {b"{", b"["}:
            raise ValueError(
                f"expected JSON, received {content_type or 'unknown content type'}"
            )
        return json.loads(
            content.decode(response.encoding) if response.encoding else content
        )

    def _bounded_content(self, response: requests.Response) -> bytes:
        try:
            response.raise_for_status()
            content = bytearray()
            chunk_size = min(64 * 1024, self.max_response_bytes + 1)
            for chunk in response.iter_content(chunk_size=chunk_size):
                if len(content) + len(chunk) > self.max_response_bytes:
                    raise ValueError(
                        f"HTTP response exceeded {self.max_response_bytes} bytes"
                    )
                content.extend(chunk)
            return bytes(content)
        finally:
            response.close()

    def get_json(self, url: str, **kwargs: Any) -> Any:
        self._wait_for_host(url)
        kwargs["stream"] = True
        response = self._session().get(url, timeout=self.timeout, **kwargs)
        return self._decode(response)

    def post_json(self, url: str, payload: Mapping[str, Any], **kwargs: Any) -> Any:
        self._wait_for_host(url)
        kwargs["stream"] = True
        response = self._session().post(
            url, json=dict(payload), timeout=self.timeout, **kwargs
        )
        return self._decode(response)

    def get_text(self, url: str, **kwargs: Any) -> str:
        self._wait_for_host(url)
        kwargs["stream"] = True
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Accept", "text/html,application/xhtml+xml")
        response = self._session().get(url, timeout=self.timeout, headers=headers, **kwargs)
        content = self._bounded_content(response)
        response.encoding = response.encoding or "utf-8"
        return content.decode(response.encoding, errors="replace")
