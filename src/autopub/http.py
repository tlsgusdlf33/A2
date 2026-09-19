"""공용 HTTP 클라이언트.

사내/에이전트 프록시 환경을 고려해 CA 번들 환경변수를 존중한다.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def _verify() -> Any:
    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        bundle = os.getenv(var)
        if bundle and os.path.exists(bundle):
            return bundle
    return True


def client(timeout: float = 20.0, **kwargs: Any) -> httpx.Client:
    headers = {"User-Agent": DEFAULT_UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    headers.update(kwargs.pop("headers", {}) or {})
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
        verify=_verify(),
        **kwargs,
    )


def get_text(url: str, *, timeout: float = 20.0, **kwargs: Any) -> str:
    with client(timeout=timeout) as http:
        response = http.get(url, **kwargs)
        response.raise_for_status()
        return response.text


def get_json(url: str, *, timeout: float = 20.0, **kwargs: Any) -> Any:
    with client(timeout=timeout) as http:
        response = http.get(url, **kwargs)
        response.raise_for_status()
        return response.json()
