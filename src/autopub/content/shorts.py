"""숏폼 대본 생성 — 유튜브 쇼츠 / 틱톡 공용."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..llm.base import LLMProvider
from ..logutil import get_logger
from ..trends.models import Topic
from ..util import truncate
from .prompts import KOREAN_CHARS_PER_SEC, build_shorts_prompt

log = get_logger(__name__)

# TTS 가 읽으면 어색해지는 문자들
_TTS_NOISE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿#*_`~\[\]<>]")


@dataclass
class Scene:
    narration: str
    on_screen: str = ""
    broll_query: str = ""


@dataclass
class ShortsScript:
    title: str
    hook: str
    scenes: list[Scene]
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    topic: Topic | None = None

    @property
    def narration(self) -> str:
        """TTS 에 넘길 전체 내레이션."""
        lines = [self.hook] + [scene.narration for scene in self.scenes]
        return " ".join(line.strip() for line in lines if line.strip())

    @property
    def estimated_seconds(self) -> float:
        return len(self.narration.replace(" ", "")) / KOREAN_CHARS_PER_SEC

    @property
    def broll_queries(self) -> list[str]:
        queries = [s.broll_query for s in self.scenes if s.broll_query]
        return queries or ["abstract background motion"]

    def youtube_title(self) -> str:
        """쇼츠로 인식되도록 #Shorts 를 붙인다. 유튜브 제목 상한은 100자."""
        base = truncate(self.title, 85)
        return f"{base} #Shorts"

    def youtube_description(self) -> str:
        tags = " ".join(f"#{tag}" for tag in self.hashtags[:8])
        parts = [self.description.strip(), tags, "#shorts"]
        source = ""
        if self.topic and self.topic.evidence:
            urls = [e.url for e in self.topic.evidence[:2] if e.url]
            if urls:
                source = "참고: " + " / ".join(urls)
        if source:
            parts.insert(1, source)
        return truncate("\n\n".join(p for p in parts if p), 4900)

    def tiktok_caption(self) -> str:
        """틱톡 캡션(제목) 상한은 2200자지만 실제로는 짧을수록 좋다."""
        tags = " ".join(f"#{tag}" for tag in self.hashtags[:6])
        return truncate(f"{self.title} {tags}".strip(), 150)


def _clean_narration(text: str) -> str:
    cleaned = _TTS_NOISE.sub(" ", str(text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # 문장 끝에 마침표가 없으면 붙여서 TTS 가 끊어 읽게 한다
    if cleaned and cleaned[-1] not in ".!?…":
        cleaned += "."
    return cleaned


def _sanitize_hashtag(tag: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_]", "", str(tag)).strip()


def generate_shorts_script(
    llm: LLMProvider,
    topic: Topic,
    *,
    target_seconds: int = 48,
    max_seconds: int = 59,
) -> ShortsScript:
    system, prompt = build_shorts_prompt(topic, target_seconds)
    data = llm.generate_json(system, prompt)

    scenes: list[Scene] = []
    for raw in data.get("scenes", []):
        if not isinstance(raw, dict):
            continue
        narration = _clean_narration(raw.get("narration", ""))
        if not narration:
            continue
        scenes.append(
            Scene(
                narration=narration,
                on_screen=truncate(str(raw.get("on_screen", "")).strip(), 20, ""),
                broll_query=str(raw.get("broll_query", "")).strip(),
            )
        )

    if not scenes:
        raise ValueError("LLM 이 장면(scenes)을 만들지 못했습니다")

    script = ShortsScript(
        title=truncate(str(data.get("title", topic.title)).strip(), 90),
        hook=_clean_narration(data.get("hook", "")),
        scenes=scenes,
        description=str(data.get("description", "")).strip(),
        hashtags=[t for t in (_sanitize_hashtag(x) for x in data.get("hashtags", [])) if t][:10],
        topic=topic,
    )

    # 길이 초과 시 뒤쪽 장면부터 덜어낸다 (쇼츠 60초 제한 준수)
    while script.estimated_seconds > max_seconds and len(script.scenes) > 3:
        dropped = script.scenes.pop()
        log.info(
            "예상 길이 %0.1f초 > %d초 — 마지막 장면 제거: %s",
            script.estimated_seconds,
            max_seconds,
            dropped.narration[:30],
        )

    log.info(
        "쇼츠 대본 생성: %s (장면 %d개, 예상 %.1f초)",
        script.title,
        len(script.scenes),
        script.estimated_seconds,
    )
    return script
