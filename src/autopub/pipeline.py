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
    BroadcastSpec,
    OverlaySpec,
    PresenterScene,
    build_presenter_video,
    render_overlay,
    CardScene,
    CardSpec,
    Theme,
    anchor_portrait,
    generate_image,
    render_broadcast_card,
    scene_prompt,
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
from .util import ensure_dir, now_kst, slugify_ko, truncate

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
                synthetic = bool(getattr(script, "synthetic_media", False))
                if name == "youtube":
                    payload = {
                        "video_path": video_path,
                        "title": script.youtube_title(),
                        "description": script.youtube_description(),
                        "tags": script.hashtags,
                        "thumbnail_path": thumbnail,
                        "synthetic_media": synthetic,
                    }
                else:
                    payload = {
                        "video_path": video_path,
                        "title": script.tiktok_caption(),
                        "synthetic_media": synthetic,
                    }

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
        style = str(self.config.get("video.style", "presenter")).lower()
        if style == "presenter":
            return self._make_presenter_video(topic)
        if style == "news":
            return self._make_news_video(topic)
        if style == "card":
            return self._make_card_video(topic)
        return self._make_broll_video(topic)

    # ---- 진행자형 (진행자 클립 + 오버레이) ----

    def _make_presenter_video(self, topic: Topic):
        """진행자 클립 하나를 재사용하고 그 위에 텍스트·데이터 카드를 얹는다.

        영상마다 사람을 새로 만들지 않는 것이 핵심이다. 그래야 무료로
        매일 무인 운영이 되고, 화면에 나오는 사람도 항상 같아 채널로 보인다.
        """
        video_cfg = self.config.section("video")
        cards_cfg = video_cfg.get("cards", {})
        presenter_cfg = video_cfg.get("presenter", {})
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

        work = self._work_dir("presenter", topic)
        resolution = shorts_cfg.get("resolution", [1080, 1920])
        size = (int(resolution[0]), int(resolution[1]))

        clip = self.config.path("video.presenter.clip", "assets/presenter/presenter.mp4")
        fallback = None
        synthetic = False
        if not clip.exists():
            # 진행자 클립이 없으면 AI 앵커 정지 이미지로 대체한다
            fallback = anchor_portrait(
                self.config.path("video.news.anchor_dir", "assets/anchor"),
                seed=int(video_cfg.get("news", {}).get("anchor_seed", 4242)),
                provider=self.config.get("video.imagegen.provider"),
            )
            synthetic = fallback is not None
            if fallback is None:
                raise RuntimeError(
                    f"진행자 클립({clip})도 AI 앵커도 준비하지 못했습니다."
                )

        accents = list(presenter_cfg.get("accents", ["blue", "yellow", "red"])) or ["blue"]
        card_ratio = float(presenter_cfg.get("card_top_ratio", 0.66))

        voice_kwargs = {
            "voice": video_cfg.get("voice", "ko-KR-SunHiNeural"),
            "rate": video_cfg.get("rate", "+0%"),
            "pitch": video_cfg.get("pitch", "+0Hz"),
        }

        scenes: list[PresenterScene] = []

        def add(spec: OverlaySpec, narration: str, index: int) -> None:
            overlay = render_overlay(
                spec, work / f"ov_{index:02d}.png", size=size, card_top_ratio=card_ratio
            )
            tts = synthesize(narration, work / f"narr_{index:02d}.mp3", **voice_kwargs)
            scenes.append(
                PresenterScene(overlay, tts.audio_path, tts.duration, tts.words)
            )

        # 오프닝 — 제목만 크게
        add(
            OverlaySpec(
                kicker=topic.title, headline=script.title,
                highlight="", accent=accents[0],
            ),
            script.hook or script.title, 0,
        )

        # 항목 — 헤드라인 + 데이터 카드
        for index, item in enumerate(script.items, start=1):
            card_value = str(item.visual.get("text", "")).strip() if item.visual else ""
            add(
                OverlaySpec(
                    kicker=script.title,
                    headline=item.name,
                    highlight=item.highlight,
                    accent=accents[(index - 1) % len(accents)],
                    card_badge=item.rank_label,
                    card_value=card_value or item.name,
                    card_label=truncate(item.caption, 40),
                ),
                item.narration, index,
            )

        # 클로징
        if script.outro and cards_cfg.get("outro_card", True):
            add(
                OverlaySpec(kicker=topic.title, headline=script.title, accent=accents[0]),
                script.outro, len(script.items) + 1,
            )

        bgm_cfg = video_cfg.get("bgm", {})
        result = build_presenter_video(
            scenes, work / "video.mp4", work,
            presenter_clip=clip if clip.exists() else None,
            fallback_image=fallback,
            width=size[0], height=size[1],
            fps=int(shorts_cfg.get("fps", 30)),
            hold_seconds=float(cards_cfg.get("hold_seconds", 0.35)),
            subtitle_opts=video_cfg.get("subtitles", {}),
            bgm_path=bgm_cfg.get("path") if bgm_cfg.get("enabled") else None,
            bgm_volume=float(bgm_cfg.get("volume", 0.05)),
        )

        if result.duration > max_seconds:
            log.warning(
                "완성 영상이 %.1f초로 상한(%d초)을 넘었습니다. item_count 를 줄이세요.",
                result.duration, max_seconds,
            )

        # AI 앵커로 대체된 경우에만 합성 미디어 고지가 필요하다.
        # 실제 촬영 클립을 쓰면 AI 생성물이 아니다.
        script.synthetic_media = synthetic

        thumbnail = None
        try:
            thumbnail = extract_thumbnail(result.path, work / "thumbnail.jpg", 1.5)
        except Exception as exc:
            log.warning("썸네일 추출 실패(무시): %s", exc)

        return result.path, script, thumbnail

    # ---- 뉴스 방송형 (생성 이미지 + AI 앵커) ----

    def _make_news_video(self, topic: Topic):
        """항목마다 내용과 관련된 이미지를 생성해 방송 화면으로 만든다.

        이미지를 만들지 못한 항목은 AI 앵커가 전하는 화면으로 대체한다.
        둘 다 합성 미디어이므로 업로드 시 AI 생성 고지가 자동으로 켜진다.
        """
        video_cfg = self.config.section("video")
        cards_cfg = video_cfg.get("cards", {})
        news_cfg = video_cfg.get("news", {})
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

        work = self._work_dir("news", topic)
        resolution = shorts_cfg.get("resolution", [1080, 1920])
        size = (int(resolution[0]), int(resolution[1]))

        image_cfg = self.config.section("video.imagegen")
        provider = image_cfg.get("provider")
        gen_size = tuple(image_cfg.get("size", [768, 1344]))
        cache_dir = self.config.path("video.imagegen.cache_dir", "assets/.imgcache")

        anchor = anchor_portrait(
            self.config.path("video.news.anchor_dir", "assets/anchor"),
            seed=int(news_cfg.get("anchor_seed", 4242)),
            provider=provider,
            auto_create=bool(news_cfg.get("auto_create_anchor", True)),
        )
        if anchor is None:
            log.warning("앵커 초상을 준비하지 못했습니다. 이미지 생성에만 의존합니다.")

        voice_kwargs = {
            "voice": video_cfg.get("voice", "ko-KR-SunHiNeural"),
            "rate": video_cfg.get("rate", "+0%"),
            "pitch": video_cfg.get("pitch", "+0Hz"),
        }

        scenes: list[CardScene] = []
        generated_count = 0

        def add_scene(card_path, narration: str, index: int) -> None:
            tts = synthesize(narration, work / f"narr_{index:02d}.mp3", **voice_kwargs)
            scenes.append(CardScene(card_path, tts.audio_path, tts.duration))

        # 오프닝 — 앵커가 방송을 연다
        opening = render_broadcast_card(
            BroadcastSpec(
                headline=script.title,
                caption=script.hook,
                kicker=str(news_cfg.get("kicker", "뉴스")),
                ticker=topic.title,
                image_path=anchor,
            ),
            work / "card_00.png", size=size,
        )
        add_scene(opening, script.hook or script.title, 0)

        # 항목 — 관련 이미지를 만들고, 실패하면 앵커 화면
        for index, item in enumerate(script.items, start=1):
            image_path = None
            if item.image_prompt:
                result = generate_image(
                    scene_prompt(item.image_prompt),
                    work / f"scene_{index:02d}.jpg",
                    size=(int(gen_size[0]), int(gen_size[1])),
                    seed=abs(hash((topic.key, index))) % 1_000_000,
                    provider=provider,
                    cache_dir=cache_dir,
                )
                if result:
                    image_path = result.path
                    generated_count += 1

            if image_path is None:
                log.info("항목 %d: 관련 이미지가 없어 앵커 화면으로 대체합니다", index)
                image_path = anchor

            card = render_broadcast_card(
                BroadcastSpec(
                    headline=item.name,
                    caption=item.caption,
                    kicker=(
                        item.rank_label
                        if news_cfg.get("rank_kicker", True) and item.rank_label
                        else str(news_cfg.get("kicker", "속보"))
                    ),
                    ticker=script.title,
                    image_path=image_path,
                ),
                work / f"card_{index:02d}.png", size=size,
            )
            add_scene(card, item.narration, index)

        # 클로징 — 다시 앵커
        if script.outro and cards_cfg.get("outro_card", True):
            closing = render_broadcast_card(
                BroadcastSpec(
                    headline=script.title, caption=script.outro,
                    kicker="정리", ticker=topic.title, image_path=anchor,
                ),
                work / f"card_{len(script.items) + 1:02d}.png", size=size,
            )
            add_scene(closing, script.outro, len(script.items) + 1)

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

        log.info(
            "뉴스 영상 완성: 생성 이미지 %d장 / 앵커 화면 %d장",
            generated_count, len(script.items) - generated_count,
        )
        # 합성 미디어이므로 업로드 시 AI 고지를 켜야 한다
        script.synthetic_media = True
        return result.path, script, opening

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
