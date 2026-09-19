"""티스토리 발행.

⚠️ 배경: 카카오는 티스토리 Open API 를 2024년 2월까지 순차 종료했다.
글쓰기/수정/첨부 API 가 모두 사라져서, 남은 현실적인 자동화 수단은
로그인한 브라우저 세션으로 에디터를 조작하는 방법뿐이다.

그래서 이 모듈은 Playwright 로 사람이 하는 것과 같은 순서로 동작한다.
  로그인(세션 재사용) → 새 글 → 제목 → HTML 모드 → 본문 → 태그 → 발행

에디터 DOM 은 티스토리 업데이트로 바뀔 수 있으므로,
선택자를 여러 개 준비해 순차 시도하고 실패 시 스크린샷을 남긴다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import env
from ..logutil import get_logger
from ..util import ensure_dir, now_kst
from .base import Publisher, PublishError, PublishResult

log = get_logger(__name__)

# 티스토리/카카오 UI 변경에 대비해 후보를 여러 개 둔다
TITLE_SELECTORS = [
    "#post-title-inp",
    "textarea#post-title-inp",
    "input[placeholder='제목을 입력하세요']",
    "textarea[placeholder='제목을 입력하세요']",
]
HTML_MODE_BUTTON = [
    "#editor-mode-layer-btn-open",
    "button:has-text('기본모드')",
    "button:has-text('마크다운')",
]
HTML_MODE_ITEM = [
    "#editor-mode-html",
    "#editor-mode-html-text",
    "button:has-text('HTML')",
    "a:has-text('HTML')",
]
TAG_SELECTORS = ["#tagText", "input[placeholder*='태그']"]
DONE_BUTTON = ["#publish-layer-btn", "button:has-text('완료')"]
PUBLISH_BUTTON = ["#publish-btn", "button:has-text('공개 발행')", "button:has-text('발행')"]


class TistoryPublisher(Publisher):
    platform = "tistory"

    def __init__(self, config, state):
        super().__init__(config, state)
        # 자격증명은 실제로 발행할 때만 필요하다.
        # 생성자에서 요구하면 --dry-run 이나 status 조회까지 막힌다.
        self._blog_name = os.getenv("TISTORY_BLOG_NAME", "").strip()
        self.storage_state = Path(env("TISTORY_STORAGE_STATE", "secrets/tistory_state.json"))
        self.headless = os.getenv("TISTORY_HEADLESS", "1") != "0"
        self._playwright = None
        self._browser = None
        self._context = None

    @property
    def blog_name(self) -> str:
        if not self._blog_name:
            raise PublishError(
                "TISTORY_BLOG_NAME 이 설정되지 않았습니다 "
                "(myblog.tistory.com 이라면 'myblog')."
            )
        return self._blog_name

    # ---------------- 브라우저 ----------------

    def _start(self):
        from playwright.sync_api import sync_playwright

        if self._context:
            return self._context

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        kwargs: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 960},
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        }
        if self.storage_state.exists():
            kwargs["storage_state"] = str(self.storage_state)
            log.info("저장된 티스토리 세션을 사용합니다: %s", self.storage_state)

        self._context = self._browser.new_context(**kwargs)
        self._context.set_default_timeout(30000)
        return self._context

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:  # pragma: no cover
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:  # pragma: no cover
                pass
        self._context = self._browser = self._playwright = None

    def _screenshot(self, page, label: str) -> None:
        try:
            target = ensure_dir("logs/screenshots") / f"{now_kst():%Y%m%d_%H%M%S}_{label}.png"
            page.screenshot(path=str(target), full_page=True)
            log.warning("디버그 스크린샷 저장: %s", target)
        except Exception:  # pragma: no cover
            pass

    @staticmethod
    def _first(page, selectors: list[str], *, timeout: int = 5000):
        """여러 후보 선택자 중 먼저 보이는 요소를 반환."""
        for selector in selectors:
            try:
                element = page.locator(selector).first
                element.wait_for(state="visible", timeout=timeout)
                return element
            except Exception:
                continue
        return None

    # ---------------- 로그인 ----------------

    def _ensure_login(self, page) -> None:
        """관리 페이지에 접근 가능한지 확인하고, 아니면 카카오 로그인."""
        page.goto(f"https://{self.blog_name}.tistory.com/manage", wait_until="domcontentloaded")

        if "/manage" in page.url and "kakao" not in page.url.lower():
            log.info("기존 세션으로 로그인 상태 확인됨")
            return

        kakao_id = env("TISTORY_KAKAO_ID")
        kakao_pw = env("TISTORY_KAKAO_PW")
        if not (kakao_id and kakao_pw):
            raise PublishError(
                "티스토리 로그인 세션이 없고 TISTORY_KAKAO_ID/PW 도 설정되지 않았습니다.\n"
                "`python scripts/tistory_login.py` 를 실행해 브라우저에서 한 번 로그인하면 "
                "세션이 저장되어 이후 자동화가 동작합니다."
            )

        log.info("카카오 계정으로 로그인 시도…")
        page.goto(
            "https://www.tistory.com/auth/login", wait_until="domcontentloaded"
        )
        kakao_btn = self._first(page, ["a.link_kakao_id", "a:has-text('카카오계정으로 로그인')"])
        if kakao_btn:
            kakao_btn.click()
            page.wait_for_load_state("domcontentloaded")

        page.fill("input[name='loginId'], #loginId--1", kakao_id)
        page.fill("input[name='password'], #password--2", kakao_pw)
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle", timeout=60000)

        if "kakao" in page.url.lower() and "login" in page.url.lower():
            self._screenshot(page, "login_failed")
            raise PublishError(
                "카카오 로그인에 실패했습니다. 2단계 인증이나 캡차가 걸렸을 가능성이 높습니다.\n"
                "`python scripts/tistory_login.py` 로 수동 로그인 후 세션을 저장하세요."
            )

        self.storage_state.parent.mkdir(parents=True, exist_ok=True)
        page.context.storage_state(path=str(self.storage_state))
        log.info("로그인 성공 — 세션을 저장했습니다: %s", self.storage_state)

    # ---------------- 발행 ----------------

    def _dismiss_draft_dialog(self, page) -> None:
        """'작성 중인 글이 있습니다. 이어서 작성하시겠습니까?' 팝업 처리.

        이 팝업을 넘기지 않으면 에디터 조작이 전부 막힌다. 자동화가 깨지는
        가장 흔한 원인이라 별도로 다룬다.
        """
        page.once("dialog", lambda dialog: dialog.dismiss())
        try:
            cancel = page.locator(
                "button:has-text('취소'), .btn_cancel, button:has-text('새로 작성')"
            ).first
            cancel.wait_for(state="visible", timeout=4000)
            cancel.click()
            log.info("임시저장 글 불러오기 팝업을 닫았습니다 (새 글로 작성)")
        except Exception:
            pass  # 팝업이 없으면 정상

    def _switch_to_html_mode(self, page) -> bool:
        opener = self._first(page, HTML_MODE_BUTTON, timeout=8000)
        if not opener:
            return False
        opener.click()
        page.wait_for_timeout(500)
        item = self._first(page, HTML_MODE_ITEM, timeout=5000)
        if not item:
            return False
        item.click()
        # 모드 전환 확인 팝업
        page.once("dialog", lambda dialog: dialog.accept())
        page.wait_for_timeout(1200)
        log.info("에디터를 HTML 모드로 전환했습니다")
        return True

    def _fill_html_body(self, page, html: str) -> None:
        """CodeMirror 기반 HTML 편집기에 본문을 주입한다.

        키보드 타이핑은 수천 자에서 매우 느리고 자동완성 때문에 깨지므로
        CodeMirror 인스턴스에 직접 값을 넣는다.
        """
        injected = page.evaluate(
            """(html) => {
                const holder = document.querySelector('.CodeMirror');
                if (holder && holder.CodeMirror) {
                    holder.CodeMirror.setValue(html);
                    holder.CodeMirror.refresh();
                    return 'codemirror';
                }
                const area = document.querySelector('textarea#html-editor-container, textarea.tx-editor');
                if (area) {
                    area.value = html;
                    area.dispatchEvent(new Event('input', { bubbles: true }));
                    return 'textarea';
                }
                return '';
            }""",
            html,
        )
        if injected:
            log.info("본문 주입 완료 (%s, %d자)", injected, len(html))
            return

        # 최후의 수단: 에디터 본문 영역에 직접 입력
        body = self._first(page, [".CodeMirror", "#editor-tistory", "iframe#editor-tistory_ifr"])
        if not body:
            self._screenshot(page, "body_not_found")
            raise PublishError("본문 입력 영역을 찾지 못했습니다. 에디터 구조가 변경되었을 수 있습니다.")
        body.click()
        page.keyboard.insert_text(html)
        log.info("본문 주입 완료 (키보드 입력, %d자)", len(html))

    def _add_tags(self, page, tags: list[str]) -> None:
        if not tags:
            return
        field = self._first(page, TAG_SELECTORS, timeout=8000)
        if not field:
            log.warning("태그 입력란을 찾지 못해 태그를 건너뜁니다")
            return
        for tag in tags[:10]:
            field.click()
            field.type(tag, delay=30)
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
        log.info("태그 %d개 입력", min(len(tags), 10))

    def publish(self, payload: dict[str, Any]) -> PublishResult:
        """payload: {title, html, tags, visibility?}"""
        if self.settings.get("mode") == "api":
            raise PublishError(
                "티스토리 Open API 는 2024년 2월에 종료되어 더 이상 사용할 수 없습니다. "
                "config 에서 platforms.tistory.mode 를 'browser' 로 두세요."
            )

        title = str(payload["title"]).strip()
        html = str(payload["html"])
        tags = list(payload.get("tags", []))

        context = self._start()
        page = context.new_page()

        try:
            self._ensure_login(page)

            page.goto(
                f"https://{self.blog_name}.tistory.com/manage/newpost/",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(2500)
            self._dismiss_draft_dialog(page)

            title_field = self._first(page, TITLE_SELECTORS, timeout=15000)
            if not title_field:
                self._screenshot(page, "title_not_found")
                raise PublishError("제목 입력란을 찾지 못했습니다. 에디터 구조가 변경되었을 수 있습니다.")
            title_field.click()
            title_field.fill(title)
            log.info("제목 입력: %s", title)

            if not self._switch_to_html_mode(page):
                self._screenshot(page, "html_mode_failed")
                raise PublishError(
                    "에디터를 HTML 모드로 전환하지 못했습니다. "
                    "티스토리 설정에서 기본 에디터를 'HTML'로 바꿔두면 안정적입니다."
                )

            self._fill_html_body(page, html)
            self._add_tags(page, tags)

            done = self._first(page, DONE_BUTTON, timeout=10000)
            if not done:
                self._screenshot(page, "done_not_found")
                raise PublishError("'완료' 버튼을 찾지 못했습니다")
            done.click()
            page.wait_for_timeout(1500)

            # 공개/비공개 선택
            visibility = payload.get("visibility") or self.settings.get("visibility", "public")
            label = {"public": "공개", "protected": "보호", "private": "비공개"}.get(
                visibility, "공개"
            )
            try:
                page.locator(f"label:has-text('{label}')").first.click(timeout=3000)
            except Exception:
                log.debug("공개 범위 선택 UI 를 찾지 못했습니다 (기본값 사용)")

            category_id = str(self.settings.get("category_id") or "").strip()
            if category_id:
                try:
                    page.select_option("#category-item", category_id, timeout=3000)
                except Exception:
                    log.debug("카테고리 선택에 실패했습니다 (기본 카테고리로 발행)")

            publish_btn = self._first(page, PUBLISH_BUTTON, timeout=10000)
            if not publish_btn:
                self._screenshot(page, "publish_not_found")
                raise PublishError("'발행' 버튼을 찾지 못했습니다")
            publish_btn.click()

            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(2000)

            url = page.url
            if "/manage/" in url:
                # 발행 후 관리 페이지로 튕기는 경우가 있어 최신 글 링크를 찾는다
                url = f"https://{self.blog_name}.tistory.com/"
            log.info("티스토리 발행 완료: %s", url)

            # 세션 갱신 저장
            try:
                context.storage_state(path=str(self.storage_state))
            except Exception:
                pass

            return PublishResult(
                platform=self.platform, title=title, url=url, extra={"visibility": visibility}
            )

        except PublishError:
            raise
        except Exception as exc:
            self._screenshot(page, "unexpected")
            raise PublishError(f"티스토리 발행 중 예기치 못한 오류: {exc}") from exc
        finally:
            try:
                page.close()
            except Exception:
                pass
