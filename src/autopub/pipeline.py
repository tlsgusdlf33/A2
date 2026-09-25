"""발행 파이프라인.

설계 원칙: 한 번 실행하면 "한 건"만 발행한다.
하루 최대 횟수는 크론이 하루에 여러 번 실행하는 것으로 채운다.
이렇게 해야 발행 간격(min_gap_minutes)이 자연스럽게 지켜지고,
한 번에 몰아 올려서 스팸으로 판정될 위험이 사라진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .content import generate_blog_post, generate_shorts_script
from .content.blog import render_html
from .content.shorts import CardScript, generate_card_script
from .llm import build_llm
from .logutil import get_logger
from .media import (
    CardScene,
    CardSpec,
    Theme,
    build_card_video,
    build_short_video,
    extract_thumbnail,
    fetch_article_images,
    fetch_backgrounds,
    group_cues,
    render_intro_card,
    render_item_card,
    synthesize,
)
from .publishers import (
    PublishError,
    TikTokPublisher,
    TistoryPublisher,
    YouTubePublisher,
)
from .state import State
from .trends import TrendAggregator, Topic
from .util import ensure_dir, now_kst, slugify_ko

log = get_logger(__name__)


@dataclass
class RunReport:
    published: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.published) and not self.failed

    def summary(self) -> str:
        lines = []
        for item in self.published:
            lines.append(f"  ✅ [{item['platform']}] {item['title']} → {item.get('url', '')}")
        for item in self.skipped:
            lines.append(f"  ⏭️  [{item['platform']}] 건너뜀: {item['reason']}")
        for item in self.failed:
            lines.append(f"  ❌ [{item['platform']}] 실패: {item['error']}")
        return "\n".join(lines) or "  (수행한 작업 없음)"


class Pipeline:
    def __init__(self, config: Config, state: State | None = None):
        self.config = config
        self.state = state or State(config.path("general.state_file", "state/state.json"))
        self.dry_run = bool(config.get("general.dry_run", False))
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = build_llm(self.config)
        return self._llm

    def _work_dir(self, prefix: str, topic: Topic) -> Path:
        stamp = now_kst().strftime("%Y%m%d_%H%M%S")
        return ensure_dir(
            self.config.path("general.work_dir", "work")
            / f"{prefix}_{stamp}_{slugify_ko(topic.title, 30)}"
        )

    def _pick_topic(self, platforms: list[str]) -> Topic | None:
        """대상 플랫폼 모두에서 최근에 쓰지 않은 주제를 고른다."""
        aggregator = TrendAggregator(self.config, self.state)
        dedupe_days = int(self.config.get("general.dedupe_days", 7))

        for topic in aggregator.top_topics(limit=15):
            if any(
                self.state.is_duplicate(topic.title, within_days=dedupe_days, platform=platform)
                for platform in platforms
            ):
                continue
            return topic

        log.warning("발행 가능한 새 주제를 찾지 못했습니다 (최근 %d일 중복 제외)", dedupe_days)
        return None

    # ================================================================
    # 블로그 (티스토리)
    # ================================================================

    def run_blog(self) -> RunReport:
        report = RunReport()
        publisher = TistoryPublisher(self.config, self.state)

        ready, reason = publisher.check_ready()
        if not ready:
            log.info("티스토리 건너뜀: %s", reason)
            report.skipped.append({"platform": "tistory", "reason": reason})
            return report

        topic = self._pick_topic(["tistory"])
        if not topic:
            report.skipped.append({"platform": "tistory", "reason": "새 주제 없음"})
            return report

        try:
            target = self.config.get("platforms.tistory.target_chars", [1800, 2800])
            post = generate_blog_post(
                self.llm, topic, min_chars=int(target[0]), max_chars=int(target[1])
            )

            image_count = int(self.config.get("platforms.tistory.images_per_post", 3))
            if image_count > 0:
                post.images = fetch_article_images(
                    post.image_queries, image_count, self._work_dir("blog", topic)
                )

            html = render_html(post)
            tags = post.tags + list(self.config.get("platforms.tistory.default_tags", []))

            if self.dry_run:
                out = ensure_dir(self.config.path("general.out_dir", "out"))
                path = out / f"{now_kst():%Y%m%d_%H%M%S}_{slugify_ko(post.title, 40)}.html"
                path.write_text(
                    f"<h1>{post.title}</h1>\n<p><em>{post.meta_description}</em></p>\n{html}"
                    f"\n<hr><p>태그: {', '.join(tags)}</p>",
                    encoding="utf-8",
                )
                log.info("[DRY-RUN] 발행하지 않고 파일로 저장: %s", path)
                report.published.append(
                    {"platform": "tistory", "title": post.title, "url": f"file://{path}"}
                )
                return report

            result = publisher.publish(
                {"title": post.title, "html": html, "tags": tags}
            )
            self.state.record_publish(
                "tistory", topic.title, result.title, result.url,
                chars=post.char_count, tags=tags,
            )
            self.state.save()
            report.published.append(
                {"platform": "tistory", "title": result.title, "url": result.url}
            )

        except (PublishError, ValueError, RuntimeError) as exc:
            log.error("티스토리 발행 실패: %s", exc)
            report.failed.append({"platform": "tistory", "error": str(exc)})
        finally:
            publisher.close()

        return report

    # ================================================================
    # 숏폼 (유튜브 쇼츠 + 틱톡)
    # ================================================================

    def run_shorts(self, targets: list[str] | None = None) -> RunReport:
        report = RunReport()
        targets = targets or ["youtube", "tiktok"]

        publishers: dict[str, Any] = {}
        if "youtube" in targets:
            publishers["youtube"] = YouTubePublisher(self.config, self.state)
        if "tiktok" in targets:
            publishers["tiktok"] = TikTokPublisher(self.config, self.state)

        # 지금 올릴 수 있는 플랫폼만 남긴다
        active = {}
        for name, publisher in publishers.items():
            ready, reason = publisher.check_ready()
            if ready:
                active[name] = publisher
            else:
                log.info("%s 건너뜀: %s", name, reason)
                report.skipped.append({"platform": name, "reason": reason})

        if not active and not self.dry_run:
            return report

        topic = self._pick_topic(list(active) or targets)
        if not topic:
            for name in active:
                report.skipped.append({"platform": name, "reason": "새 주제 없음"})
            return report

        # ---- 영상 1개를 만들어 두 플랫폼에 공통으로 올린다 ----
        try:
            video_path, script, thumbnail = self._make_video(topic)
        except Exception as exc:
            log.error("영상 제작 실패: %s", exc)
            for name in active or targets:
                report.failed.append({"platform": name, "error": f"영상 제작 실패: {exc}"})
            return report

        if self.dry_run:
            log.info("[DRY-RUN] 업로드하지 않음. 결과 영상: %s", video_path)
            for name in active or targets:
                report.published.append(
                    {"platform": name, "title": script.title, "url": f"file://{video_path}"}
                )
            return report

        for name, publisher in active.items():
            try:
                if name == "youtube":
                    payload = {
                        "video_path": video_path,
                        "title": script.youtube_title(),
                        "description": script.youtube_description(),
                        "tags": script.hashtags,
                        "thumbnail_path": thumbnail,
                    }
                else:
                    payload = {"video_path": video_path, "title": script.tiktok_caption()}

                result = publisher.publish(payload)
                self.state.record_publish(
                    name, topic.title, result.title, result.url, result.external_id,
                    **result.extra,
                )
                self.state.save()
                report.published.append(
                    {"platform": name, "title": result.title, "url": result.url}
                )
            except PublishError as exc:
                log.error("%s 발행 실패: %s", name, exc)
                report.failed.append({"platform": name, "error": str(exc)})
            finally:
                publisher.close()

        return report

    def _make_video(self, topic: Topic):
        """설정된 스타일에 따라 영상을 만든다."""
        style = str(self.config.get("video.style", "card")).lower()
        if style == "card":
            return self._make_card_video(topic)
        return self._make_broll_video(topic)

    # ---- 카드형 (권장) ----

    def _make_card_video(self, topic: Topic):
        """카운트다운 카드가 넘어가는 형식.

        카드마다 그 카드의 내레이션을 따로 합성하기 때문에
        화면 전환과 말이 정확히 맞는다.
        """
        video_cfg = self.config.section("video")
        cards_cfg = video_cfg.get("cards", {})
        shorts_cfg = self.config.section("platforms.youtube").get("shorts", {})

        seconds_range = video_cfg.get("target_seconds", [40, 55])
        target_seconds = int(sum(seconds_range) / 2)
        max_seconds = int(shorts_cfg.get("max_seconds", 59))

        script = generate_card_script(
            self.llm, topic,
            target_seconds=target_seconds,
            max_seconds=max_seconds,
            item_count=int(cards_cfg.get("item_count", 5)),
        )

        work = self._work_dir("cards", topic)
        theme = Theme.preset(str(cards_cfg.get("theme", "cream")))
        resolution = shorts_cfg.get("resolution", [1080, 1920])
        size = (int(resolution[0]), int(resolution[1]))

        voice_kwargs = {
            "voice": video_cfg.get("voice", "ko-KR-SunHiNeural"),
            "rate": video_cfg.get("rate", "+0%"),
            "pitch": video_cfg.get("pitch", "+0Hz"),
        }

        scenes: list[CardScene] = []

        def add_scene(card_path, narration: str, index: int) -> None:
            tts = synthesize(narration, work / f"narr_{index:02d}.mp3", **voice_kwargs)
            scenes.append(
                CardScene(card_path=card_path, audio_path=tts.audio_path, duration=tts.duration)
            )

        # 인트로 카드 — 제목과 항목 미리보기로 끝까지 볼 이유를 만든다
        intro = render_intro_card(
            script.title, script.preview_items(), work / "card_00.png", theme, size
        )
        add_scene(intro, script.hook or script.title, 0)

        # 항목 카드
        for index, item in enumerate(script.items, start=1):
            card = render_item_card(
                CardSpec(
                    rank=item.rank_label,
                    heading=item.name,
                    caption=item.caption,
                    visual=item.visual,
                ),
                work / f"card_{index:02d}.png", theme, size,
            )
            add_scene(card, item.narration, index)

        # 아웃트로 카드
        if script.outro and cards_cfg.get("outro_card", True):
            outro = render_item_card(
                CardSpec(heading=script.title, caption=script.outro),
                work / f"card_{len(script.items) + 1:02d}.png", theme, size,
            )
            add_scene(outro, script.outro, len(script.items) + 1)

        bgm_cfg = video_cfg.get("bgm", {})
        result = build_card_video(
            scenes, work / "video.mp4", work,
            width=size[0], height=size[1],
            fps=int(shorts_cfg.get("fps", 30)),
            hold_seconds=float(cards_cfg.get("hold_seconds", 0.45)),
            punch_in=bool(cards_cfg.get("punch_in", True)),
            bgm_path=bgm_cfg.get("path") if bgm_cfg.get("enabled") else None,
            bgm_volume=float(bgm_cfg.get("volume", 0.06)),
        )

        if result.duration > max_seconds:
            log.warning(
                "완성 영상이 %.1f초로 상한(%d초)을 넘었습니다. "
                "video.cards.item_count 를 줄이세요.",
                result.duration, max_seconds,
            )

        # 인트로 카드가 곧 썸네일 — 제목이 가장 잘 보이는 장면이다
        return result.path, script, intro

    # ---- 스톡 b-roll (기존) ----

    def _make_broll_video(self, topic: Topic):
        """대본 → TTS → 자막 → 배경 → 영상."""
        video_cfg = self.config.section("video")
        shorts_cfg = self.config.section("platforms.youtube").get("shorts", {})

        seconds_range = video_cfg.get("target_seconds", [40, 55])
        target_seconds = int(sum(seconds_range) / 2)
        max_seconds = int(shorts_cfg.get("max_seconds", 59))

        script = generate_shorts_script(
            self.llm, topic, target_seconds=target_seconds, max_seconds=max_seconds
        )

        work = self._work_dir("shorts", topic)
        tts_result = synthesize(
            script.narration,
            work / "narration.mp3",
            voice=video_cfg.get("voice", "ko-KR-SunHiNeural"),
            rate=video_cfg.get("rate", "+0%"),
            pitch=video_cfg.get("pitch", "+0Hz"),
        )

        # TTS 실측 길이가 상한을 넘으면 재생성하지 않고 경고만 남긴다
        # (쇼츠는 60초, 틱톡은 그보다 길어도 되므로 치명적이지 않다)
        if tts_result.duration > max_seconds:
            log.warning(
                "내레이션이 %.1f초로 상한(%d초)을 넘었습니다. 대본을 더 짧게 조정하세요.",
                tts_result.duration, max_seconds,
            )

        subtitle_cfg = video_cfg.get("subtitles", {})
        cues = group_cues(
            tts_result.words, max_chars=int(subtitle_cfg.get("max_chars_per_cue", 18))
        )

        resolution = shorts_cfg.get("resolution", [1080, 1920])
        clip_seconds = float(video_cfg.get("bg_clip_seconds", 5))
        needed = max(3, int(tts_result.duration / clip_seconds) + 1)
        backgrounds = fetch_backgrounds(
            script.broll_queries,
            needed,
            work / "bg",
            providers=list(video_cfg.get("stock_providers", ["pexels", "pixabay"])),
            width=int(resolution[0]),
            height=int(resolution[1]),
        )

        bgm_cfg = video_cfg.get("bgm", {})
        result = build_short_video(
            tts_result,
            cues,
            backgrounds,
            work / "video.mp4",
            work,
            width=int(resolution[0]),
            height=int(resolution[1]),
            fps=int(shorts_cfg.get("fps", 30)),
            clip_seconds=clip_seconds,
            darken=float(video_cfg.get("bg_darken", 0.35)),
            subtitle_opts=subtitle_cfg,
            bgm_path=bgm_cfg.get("path") if bgm_cfg.get("enabled") else None,
            bgm_volume=float(bgm_cfg.get("volume", 0.08)),
        )

        thumbnail = None
        try:
            thumbnail = extract_thumbnail(result.path, work / "thumbnail.jpg", 1.5)
        except Exception as exc:
            log.warning("썸네일 추출 실패(무시): %s", exc)

        return result.path, script, thumbnail

    # ================================================================

    def status(self) -> dict:
        """오늘 각 플랫폼의 사용량/잔여량."""
        rows = {}
        for cls in (TistoryPublisher, YouTubePublisher, TikTokPublisher):
            try:
                publisher = cls(self.config, self.state)
            except Exception as exc:  # 환경변수 미설정 등
                rows[cls.platform] = {"error": str(exc)}
                continue
            ready, reason = publisher.check_ready()
            rows[cls.platform] = {
                "enabled": publisher.enabled,
                "used": self.state.count_today(cls.platform),
                "daily_max": publisher.daily_max,
                "remaining": publisher.remaining_today(),
                "ready": ready,
                "reason": reason,
            }
        return rows
