"""OpenAI 호환 엔드포인트 (Groq / OpenRouter 무료 모델 / 로컬 Ollama 등).

Gemini 무료 한도를 다 썼을 때의 예비 공급자로 쓰기 좋다.
"""
from __future__ import annotations

import os

from ..http import client
from ..logutil import get_logger
from .base import LLMError, LLMProvider

log = get_logger(__name__)


class OpenAICompatProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, model: str | None = None, **kwargs):
        model = model or os.getenv("OPENAI_COMPAT_MODEL", "llama-3.3-70b-versatile")
        super().__init__(model, **kwargs)
        self.base_url = os.getenv(
            "OPENAI_COMPAT_BASE_URL", "https://api.groq.com/openai/v1"
        ).rstrip("/")
        self.api_key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip()
        # 로컬 Ollama 처럼 키가 필요 없는 경우도 허용
        if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            raise LLMError("OPENAI_COMPAT_API_KEY 가 없습니다.")

    def _complete(self, system: str, prompt: str, *, json_mode: bool) -> str:
        payload: dict = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with client(timeout=180.0) as http:
            response = http.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            if response.status_code == 429:
                raise LLMError("무료 티어 한도 초과(429).")
            if response.status_code >= 400:
                raise LLMError(f"{self.base_url} {response.status_code}: {response.text[:400]}")
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"응답에 choices 가 없습니다: {str(data)[:300]}")
        return choices[0].get("message", {}).get("content", "") or ""
