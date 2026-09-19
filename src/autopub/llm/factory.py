"""LLM_PROVIDER 환경변수에 따라 공급자를 생성한다."""
from __future__ import annotations

import os

from ..config import Config
from ..logutil import get_logger
from .base import LLMError, LLMProvider

log = get_logger(__name__)


def build_llm(config: Config, provider: str | None = None) -> LLMProvider:
    name = (provider or os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    common = {
        "temperature": float(config.get("llm.temperature", 0.7)),
        "max_output_tokens": int(config.get("llm.max_output_tokens", 8000)),
        "min_interval_sec": float(config.get("llm.min_interval_sec", 0)),
        "max_retries": int(config.get("llm.max_retries", 3)),
    }

    if name == "gemini":
        from .gemini import GeminiProvider

        model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        log.info("LLM 공급자: Gemini (%s)", model)
        return GeminiProvider(model, **common)

    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        model = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
        log.info("LLM 공급자: Anthropic (%s)", model)
        return AnthropicProvider(model, **common)

    if name in ("openai_compatible", "openai-compat", "groq", "openrouter", "ollama"):
        from .openai_compat import OpenAICompatProvider

        log.info("LLM 공급자: OpenAI 호환 엔드포인트")
        return OpenAICompatProvider(**common)

    raise LLMError(
        f"알 수 없는 LLM_PROVIDER: {name!r} "
        "(gemini | anthropic | openai_compatible 중 하나여야 합니다)"
    )
