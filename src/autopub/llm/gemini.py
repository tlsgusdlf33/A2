"""Google Gemini — 무료 티어 기본 공급자.

공식 REST 엔드포인트를 직접 호출한다. SDK 버전 변화에 영향받지 않고
의존성도 httpx 하나로 끝나서, 무료로 오래 굴리기에 가장 안정적이다.
무료 티어 한도(RPM/RPD)는 모델·시점마다 달라지므로
config 의 llm.min_interval_sec 로 속도를 조절한다.
"""
from __future__ import annotations

import os

from ..http import client
from ..logutil import get_logger
from .base import LLMError, LLMProvider

log = get_logger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str = "gemini-flash-latest", **kwargs):
        super().__init__(model, **kwargs)
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise LLMError(
                "GEMINI_API_KEY 가 없습니다. https://aistudio.google.com/apikey 에서 "
                "무료 키를 발급받아 .env 에 넣으세요."
            )

    def _complete(self, system: str, prompt: str, *, json_mode: bool) -> str:
        generation_config: dict = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_output_tokens,
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        url = f"{BASE_URL}/models/{self.model}:generateContent"
        with client(timeout=180.0) as http:
            response = http.post(
                url, json=payload, headers={"x-goog-api-key": self.api_key}
            )
            if response.status_code == 429:
                raise LLMError("Gemini 무료 티어 한도 초과(429). 간격을 늘리거나 내일 재시도하세요.")
            if response.status_code >= 400:
                raise LLMError(f"Gemini {response.status_code}: {response.text[:400]}")
            data = response.json()

        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback", {})
            raise LLMError(f"Gemini 응답에 candidate 가 없습니다: {feedback}")

        first = candidates[0]
        reason = first.get("finishReason")
        if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise LLMError(f"Gemini 안전 필터로 차단됨 (finishReason={reason})")

        parts = first.get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if reason == "MAX_TOKENS":
            log.warning("Gemini 응답이 max_output_tokens 에서 잘렸습니다.")
        return text
