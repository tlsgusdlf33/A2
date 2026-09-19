"""LLM 공급자 공통 인터페이스."""
from __future__ import annotations

import abc
import json
import re
import time

from ..logutil import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    """LLM 호출 실패."""


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """모델 응답에서 JSON 객체를 뽑아낸다.

    코드펜스로 감싸거나 앞뒤에 설명을 붙이는 경우가 흔해서 관대하게 파싱한다.
    """
    candidate = text.strip()

    fenced = _JSON_FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 첫 '{' 부터 마지막 '}' 까지를 다시 시도
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON 파싱 실패: {exc}\n응답 앞부분: {text[:300]}") from exc

    raise LLMError(f"응답에서 JSON 을 찾지 못했습니다.\n응답 앞부분: {text[:300]}")


class LLMProvider(abc.ABC):
    """모든 공급자가 구현하는 최소 인터페이스."""

    name = "base"

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.7,
        max_output_tokens: int = 8000,
        min_interval_sec: float = 0.0,
        max_retries: int = 3,
    ):
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.min_interval_sec = min_interval_sec
        self.max_retries = max_retries
        self._last_call = 0.0

    # ---------------- 공급자별 구현 ----------------

    @abc.abstractmethod
    def _complete(self, system: str, prompt: str, *, json_mode: bool) -> str:
        """실제 API 호출. 텍스트를 반환한다."""

    # ---------------- 공통 동작 ----------------

    def _throttle(self) -> None:
        """무료 티어 분당 요청 제한(RPM) 보호."""
        if self.min_interval_sec <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self.min_interval_sec:
            wait = self.min_interval_sec - elapsed
            log.debug("레이트리밋 보호로 %.1fs 대기", wait)
            time.sleep(wait)

    def generate(self, system: str, prompt: str, *, json_mode: bool = False) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                text = self._complete(system, prompt, json_mode=json_mode)
                self._last_call = time.monotonic()
                if not text.strip():
                    raise LLMError("빈 응답")
                return text
            except Exception as exc:  # 공급자별 예외 타입이 제각각이라 넓게 잡는다
                self._last_call = time.monotonic()
                last_error = exc
                backoff = min(60, 2**attempt * 3)
                log.warning(
                    "[%s] 호출 실패 (%d/%d): %s — %ds 후 재시도",
                    self.name,
                    attempt,
                    self.max_retries,
                    exc,
                    backoff,
                )
                if attempt < self.max_retries:
                    time.sleep(backoff)

        raise LLMError(f"[{self.name}] {self.max_retries}회 시도 후 실패: {last_error}")

    def generate_json(self, system: str, prompt: str) -> dict:
        return extract_json(self.generate(system, prompt, json_mode=True))
