"""블로그 글 생성 — LLM 결과를 티스토리에 붙일 HTML 까지 만든다."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from ..llm.base import LLMProvider
from ..logutil import get_logger
from ..trends.models import Topic
from ..util import truncate
from .prompts import build_blog_prompt

log = get_logger(__name__)


@dataclass
class BlogPost:
    title: str
    meta_description: str
    tags: list[str]
    body_markdown: str
    faq: list[dict] = field(default_factory=list)
    image_queries: list[str] = field(default_factory=list)
    sources_note: str = ""
    topic: Topic | None = None
    images: list[dict] = field(default_factory=list)  # {url, alt, credit, credit_url}

    @property
    def char_count(self) -> int:
        return len(re.sub(r"\s", "", self.body_markdown))


def _markdown_to_html(text: str) -> str:
    """마크다운 → HTML. markdown 패키지가 없으면 최소 변환으로 대체."""
    try:
        import markdown as md

        return md.markdown(text, extensions=["extra", "sane_lists", "nl2br"])
    except ImportError:  # pragma: no cover
        log.warning("markdown 패키지가 없어 간이 변환을 사용합니다.")
        parts = []
        for block in text.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            if block.startswith("### "):
                parts.append(f"<h3>{html.escape(block[4:])}</h3>")
            elif block.startswith("## "):
                parts.append(f"<h2>{html.escape(block[3:])}</h2>")
            else:
                parts.append(f"<p>{html.escape(block).replace(chr(10), '<br>')}</p>")
        return "\n".join(parts)


def _faq_html(faq: list[dict]) -> str:
    if not faq:
        return ""
    rows = []
    for item in faq:
        question = html.escape(str(item.get("q", "")).strip())
        answer = html.escape(str(item.get("a", "")).strip())
        if not question or not answer:
            continue
        rows.append(
            "<details><summary><strong>"
            f"{question}</strong></summary><p>{answer}</p></details>"
        )
    if not rows:
        return ""
    return "<h2>자주 묻는 질문</h2>\n" + "\n".join(rows)


def _images_html(images: list[dict]) -> list[str]:
    blocks = []
    for image in images:
        url = html.escape(image.get("url", ""))
        alt = html.escape(image.get("alt", ""))
        credit = html.escape(image.get("credit", ""))
        credit_url = html.escape(image.get("credit_url", ""))
        caption = (
            f'<figcaption style="font-size:12px;color:#888">'
            f'사진: <a href="{credit_url}" rel="nofollow noopener" target="_blank">{credit}</a>'
            f"</figcaption>"
            if credit
            else ""
        )
        blocks.append(
            f'<figure><img src="{url}" alt="{alt}" style="max-width:100%;height:auto">'
            f"{caption}</figure>"
        )
    return blocks


def _sources_html(topic: Topic | None, note: str) -> str:
    if not topic or not topic.evidence:
        return f"<p><em>{html.escape(note)}</em></p>" if note else ""
    items = []
    for evidence in topic.evidence[:5]:
        if not evidence.url:
            continue
        label = html.escape(evidence.title or evidence.url)
        source = f" ({html.escape(evidence.source)})" if evidence.source else ""
        items.append(
            f'<li><a href="{html.escape(evidence.url)}" rel="nofollow noopener" '
            f'target="_blank">{label}</a>{source}</li>'
        )
    if not items:
        return ""
    return "<h2>참고 자료</h2>\n<ul>\n" + "\n".join(items) + "\n</ul>"


def render_html(post: BlogPost) -> str:
    """티스토리 에디터(HTML 모드)에 그대로 붙일 수 있는 본문."""
    body = _markdown_to_html(post.body_markdown)

    # 이미지를 h2 경계에 고르게 끼워 넣는다
    image_blocks = _images_html(post.images)
    if image_blocks:
        chunks = re.split(r"(?=<h2)", body)
        merged: list[str] = []
        image_iter = iter(image_blocks)
        for index, chunk in enumerate(chunks):
            merged.append(chunk)
            if index == 0 or index % 2 == 0:
                nxt = next(image_iter, None)
                if nxt:
                    merged.append(nxt)
        # 남은 이미지는 마지막에
        merged.extend(image_iter)
        body = "\n".join(merged)

    sections = [body, _faq_html(post.faq), _sources_html(post.topic, post.sources_note)]
    return "\n\n".join(section for section in sections if section)


def generate_blog_post(
    llm: LLMProvider,
    topic: Topic,
    *,
    min_chars: int = 1800,
    max_chars: int = 2800,
) -> BlogPost:
    system, prompt = build_blog_prompt(topic, min_chars, max_chars)
    data = llm.generate_json(system, prompt)

    tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()][:10]
    post = BlogPost(
        title=truncate(str(data.get("title", topic.title)).strip(), 90),
        meta_description=truncate(str(data.get("meta_description", "")).strip(), 160),
        tags=tags,
        body_markdown=str(data.get("body_markdown", "")).strip(),
        faq=[f for f in data.get("faq", []) if isinstance(f, dict)],
        image_queries=[str(q).strip() for q in data.get("image_queries", []) if str(q).strip()],
        sources_note=str(data.get("sources_note", "")).strip(),
        topic=topic,
    )

    if not post.body_markdown:
        raise ValueError("LLM 이 본문을 만들지 못했습니다 (body_markdown 이 비어 있음)")

    log.info(
        "블로그 초안 생성: %s (본문 %d자, 태그 %d개, FAQ %d개)",
        post.title,
        post.char_count,
        len(post.tags),
        len(post.faq),
    )
    if post.char_count < min_chars * 0.6:
        log.warning(
            "본문이 목표보다 많이 짧습니다 (%d자 < 목표 %d자)", post.char_count, min_chars
        )
    return post
