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
    synthetic_media: bool = False

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


# ==========================================================================
# 카드형 카운트다운 대본
# ==========================================================================

_ALLOWED_VISUALS = {"text", "bars", "candles", "image"}

# 합성 미디어 고지. 유튜브·틱톡 모두 AI 생성 콘텐츠 표시를 요구한다.
AI_DISCLOSURE = "※ 이 영상의 이미지와 진행자는 AI로 생성되었습니다."


@dataclass
class CardItem:
    name: str
    narration: str
    caption: str
    visual: dict = field(default_factory=dict)
    image_prompt: str = ""    # 뉴스 스타일에서 배경 이미지를 생성할 영어 묘사
    rank: int = 0             # 1 이 가장 높은 순위

    @property
    def rank_label(self) -> str:
        return f"{self.rank}위" if self.rank else ""


@dataclass
class CardScript:
    """단색 배경 카드가 넘어가는 형식의 대본."""

    title: str
    hook: str
    items: list[CardItem]
    outro: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    topic: Topic | None = None
    # AI 생성 이미지/앵커를 쓴 영상인지. 업로드 시 플랫폼 고지를 켜는 근거가 된다.
    synthetic_media: bool = False

    @property
    def narration_parts(self) -> list[str]:
        """카드 순서대로의 내레이션 목록 (인트로 → 항목들 → 아웃트로)."""
        parts = [self.hook] + [item.narration for item in self.items]
        if self.outro:
            parts.append(self.outro)
        return [p for p in parts if p.strip()]

    @property
    def narration(self) -> str:
        return " ".join(self.narration_parts)

    @property
    def estimated_seconds(self) -> float:
        return len(self.narration.replace(" ", "")) / KOREAN_CHARS_PER_SEC

    def preview_items(self) -> list[dict]:
        """인트로 카드에 띄울 미리보기 (순위 역순 그대로)."""
        return [{"name": item.name, "visual": item.visual} for item in self.items]

    # 업로드 메타데이터는 기존 숏폼과 동일한 규칙을 쓴다
    def youtube_title(self) -> str:
        return f"{truncate(self.title, 85)} #Shorts"

    def youtube_description(self) -> str:
        body = ShortsScript(
            title=self.title, hook=self.hook, scenes=[],
            description=self.description, hashtags=self.hashtags, topic=self.topic,
        ).youtube_description()
        if self.synthetic_media:
            body = f"{AI_DISCLOSURE}\n\n{body}"
        return truncate(body, 4900)

    def tiktok_caption(self) -> str:
        tags = " ".join(f"#{tag}" for tag in self.hashtags[:6])
        return truncate(f"{self.title} {tags}".strip(), 150)


def _clean_visual(raw: object) -> dict:
    """LLM 이 준 visual 명세를 검증한다.

    환각한 수치로 그래프를 그리면 잘못된 정보를 그럴듯하게 보여주게 되므로,
    형식이 맞지 않으면 조용히 text 형으로 떨어뜨린다.
    """
    if not isinstance(raw, dict):
        return {}

    kind = str(raw.get("type", "")).strip().lower()
    if kind not in _ALLOWED_VISUALS:
        return {}

    if kind == "bars":
        labels = [str(x).strip() for x in raw.get("labels", []) if str(x).strip()]
        values: list[float] = []
        for item in raw.get("values", []):
            try:
                values.append(float(str(item).replace(",", "").replace("%", "")))
            except (TypeError, ValueError):
                return {}   # 숫자가 아니면 막대그래프를 포기한다
        if len(labels) < 2 or len(labels) != len(values):
            return {}
        return {"type": "bars", "labels": labels[:5], "values": values[:5]}

    if kind == "candles":
        return {
            "type": "candles",
            "pattern": str(raw.get("pattern", "")).strip() or "default",
            "highlight_label": truncate(str(raw.get("highlight_label", "")).strip(), 4, ""),
        }

    if kind == "image":
        return {"type": "image", "path": str(raw.get("path", ""))}

    text = _keyword(str(raw.get("text", "")).strip(), limit=12)
    return {"type": "text", "text": text} if text else {}


# 이미지 프롬프트에 사람이 들어가면 실존 인물의 가짜 장면을 만들게 된다
_PERSON_WORDS = re.compile(
    r"\b(person|people|man|men|woman|women|girl|boy|face|portrait|crowd|"
    r"politician|president|ceo|celebrity|actor|singer|player)\b",
    re.IGNORECASE,
)


def _clean_image_prompt(raw: object) -> str:
    """장면 이미지 프롬프트를 검증한다.

    한글이 섞여 있으면 생성 모델이 제대로 못 알아듣고,
    사람을 묘사하면 실존 인물의 가짜 장면을 만들어낼 위험이 있다.
    둘 중 하나라도 걸리면 프롬프트를 버리고 앵커 화면으로 떨어뜨린다.
    """
    text = str(raw or "").strip()
    if not text or len(text) < 8:
        return ""
    if re.search(r"[가-힣]", text):
        return ""
    if _PERSON_WORDS.search(text):
        return ""
    return text[:200]


def _keyword(text: str, limit: int = 12) -> str:
    """화면 가운데 크게 띄울 강조 문구로 다듬는다.

    LLM 이 규칙을 어기고 문장을 넣는 경우가 있다. 글자 수로 자르면
    음절 중간에서 끊겨 보기 흉하므로 어절 경계에서 자른다.
    """
    cleaned = re.sub(r"[.!?…]+$", "", text).strip()
    if len(cleaned) <= limit:
        return cleaned

    words, kept = cleaned.split(), []
    for word in words:
        candidate = " ".join(kept + [word])
        if len(candidate) > limit:
            break
        kept.append(word)
    # 첫 어절조차 너무 길면 어쩔 수 없이 글자 수로 자른다
    return " ".join(kept) if kept else cleaned[:limit]


def generate_card_script(
    llm: LLMProvider,
    topic: Topic,
    *,
    target_seconds: int = 48,
    max_seconds: int = 59,
    item_count: int = 5,
) -> CardScript:
    from .prompts import build_card_prompt

    system, prompt = build_card_prompt(topic, target_seconds, item_count)
    data = llm.generate_json(system, prompt)

    raw_items = [x for x in data.get("items", []) if isinstance(x, dict)]
    if not raw_items:
        raise ValueError("LLM 이 항목(items)을 만들지 못했습니다")

    items: list[CardItem] = []
    total = len(raw_items)
    for index, raw in enumerate(raw_items):
        narration = _clean_narration(raw.get("narration", ""))
        name = truncate(str(raw.get("name", "")).strip(), 16, "")
        if not (narration and name):
            continue
        items.append(
            CardItem(
                name=name,
                narration=narration,
                caption=truncate(str(raw.get("caption", "")).strip(), 90),
                visual=_clean_visual(raw.get("visual")),
                image_prompt=_clean_image_prompt(raw.get("image_prompt", "")),
                # 배열 마지막이 1위인 역순 카운트다운
                rank=total - index,
            )
        )

    if not items:
        raise ValueError("유효한 항목이 하나도 없습니다")

    script = CardScript(
        title=truncate(str(data.get("title", topic.title)).strip(), 40),
        hook=_clean_narration(data.get("hook", "")),
        items=items,
        outro=_clean_narration(data.get("outro", "")) if data.get("outro") else "",
        description=str(data.get("description", "")).strip(),
        hashtags=[t for t in (_sanitize_hashtag(x) for x in data.get("hashtags", [])) if t][:10],
        topic=topic,
    )

    # 길이 초과 시 낮은 순위(배열 앞쪽)부터 덜어낸다 — 1위는 반드시 남긴다
    while script.estimated_seconds > max_seconds and len(script.items) > 3:
        dropped = script.items.pop(0)
        log.info(
            "예상 %.1f초 > %d초 — 최하위 항목 제거: %s",
            script.estimated_seconds, max_seconds, dropped.name,
        )
        for new_rank, item in enumerate(reversed(script.items), start=1):
            item.rank = new_rank

    log.info(
        "카드 대본 생성: %s (항목 %d개, 예상 %.1f초)",
        script.title, len(script.items), script.estimated_seconds,
    )
    return script
