"""Anthropic Claude — 최고 품질 옵션(유료).

무료 티어가 없으므로 기본 공급자는 아니지만,
글 품질을 최대로 끌어올리고 싶을 때 LLM_PROVIDER=anthropic 으로 전환한다.
"""
from __future__ import annotations

import os

from ..logutil import get_logger
from .base import LLMError, LLMProvider

log = get_logger(__name__)

# 거부(refusal) 시 서버가 알아서 대체 모델로 라우팅해 파이프라인이 멈추지 않게 한다
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5", **kwargs):
        super().__init__(model, **kwargs)
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY 가 없습니다.")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("pip install anthropic 이 필요합니다.") from exc
        self._sdk = anthropic
        self.client = anthropic.Anthropic()

    def _stream_text(self, system: str, prompt: str, *, use_fallbacks: bool) -> str:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            # 긴 글/스크립트 작성은 충분히 복잡한 작업이라 적응형 사고를 켠다
            "thinking": {"type": "adaptive"},
        }
        if use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
            stream_ctx = self.client.beta.messages.stream(**kwargs)
        else:
            stream_ctx = self.client.messages.stream(**kwargs)

        with stream_ctx as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise LLMError(f"Claude 가 요청을 거부했습니다: {details}")
        if message.stop_reason == "max_tokens":
            log.warning("Claude 응답이 max_tokens 에서 잘렸습니다.")

        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )

    def _complete(self, system: str, prompt: str, *, json_mode: bool) -> str:
        if json_mode:
            system = (
                f"{system}\n\n반드시 유효한 JSON 객체 하나만 출력하세요. "
                "설명, 인사말, 코드펜스를 붙이지 마세요."
            )
        try:
            return self._stream_text(system, prompt, use_fallbacks=True)
        except self._sdk.BadRequestError as exc:
            # 베타 파라미터를 쓸 수 없는 계정/모델이면 표준 호출로 재시도
            log.info("서버사이드 폴백을 사용할 수 없어 표준 호출로 재시도합니다: %s", exc)
            return self._stream_text(system, prompt, use_fallbacks=False)
