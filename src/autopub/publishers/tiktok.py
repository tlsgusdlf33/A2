"""TikTok Content Posting API (Direct Post).

중요 제약:
- 앱이 심사(audit) 전이면 SELF_ONLY(비공개)로만 게시된다. 공개 발행을 하려면
  TikTok 개발자 포털에서 Content Posting API 심사를 통과해야 한다.
- privacy_level 은 반드시 creator_info/query 가 돌려준 목록 안의 값이어야 한다.
- 사용자 토큰당 6 requests/min, 계정당 일일 상한(비공개 정책, 대략 15~25건)이 있다.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..config import env
from ..http import client
from ..logutil import get_logger
from ..util import truncate
from .base import Publisher, PublishError, PublishResult

log = get_logger(__name__)

API = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API}/oauth/token/"
CREATOR_INFO_URL = f"{API}/post/publish/creator_info/query/"
INIT_URL = f"{API}/post/publish/video/init/"
STATUS_URL = f"{API}/post/publish/status/fetch/"

# 한 번에 올릴 수 있는 청크 상한. 쇼츠 길이 영상은 대부분 단일 청크로 끝난다.
MAX_SINGLE_CHUNK = 64 * 1024 * 1024
CHUNK_SIZE = 10 * 1024 * 1024
TITLE_LIMIT = 2200


class TikTokPublisher(Publisher):
    platform = "tiktok"

    def __init__(self, config, state):
        super().__init__(config, state)
        self.token_file = Path(env("TIKTOK_TOKEN_FILE", "secrets/tiktok_token.json"))
        self._access_token: str | None = None

    # ---------------- 인증 ----------------

    def _load_tokens(self) -> dict:
        if not self.token_file.exists():
            raise PublishError(
                f"TikTok 토큰 파일이 없습니다: {self.token_file}\n"
                "먼저 `python scripts/tiktok_oauth.py` 를 실행해 인증하세요."
            )
        return json.loads(self.token_file.read_text(encoding="utf-8"))

    def _save_tokens(self, tokens: dict) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(
            json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(self.token_file, 0o600)

    def _refresh(self, tokens: dict) -> dict:
        log.info("TikTok 액세스 토큰 갱신 중…")
        data = {
            "client_key": env("TIKTOK_CLIENT_KEY", required=True),
            "client_secret": env("TIKTOK_CLIENT_SECRET", required=True),
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        }
        with client(timeout=30.0) as http:
            response = http.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code >= 400:
            raise PublishError(
                f"TikTok 토큰 갱신 실패 ({response.status_code}): {response.text[:300]}\n"
                "리프레시 토큰이 만료됐다면 재인증이 필요합니다."
            )
        fresh = response.json()
        if "access_token" not in fresh:
            raise PublishError(f"TikTok 토큰 응답이 이상합니다: {str(fresh)[:300]}")
        fresh["obtained_at"] = int(time.time())
        self._save_tokens(fresh)
        return fresh

    def _token(self) -> str:
        if self._access_token:
            return self._access_token

        tokens = self._load_tokens()
        obtained = int(tokens.get("obtained_at", 0))
        expires_in = int(tokens.get("expires_in", 86400))
        # 만료 5분 전이면 미리 갱신
        if not obtained or time.time() > obtained + expires_in - 300:
            tokens = self._refresh(tokens)

        self._access_token = tokens["access_token"]
        return self._access_token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    # ---------------- API 호출 ----------------

    def _post(self, url: str, payload: dict) -> dict:
        with client(timeout=60.0) as http:
            response = http.post(url, json=payload, headers=self._headers())
        try:
            data = response.json()
        except json.JSONDecodeError:
            raise PublishError(f"TikTok 응답 파싱 실패 ({response.status_code}): {response.text[:300]}")

        error = data.get("error") or {}
        code = error.get("code", "ok")
        if response.status_code >= 400 or code not in ("ok", "", None):
            message = error.get("message", "")
            log_id = error.get("log_id", "")
            raise PublishError(
                f"TikTok API 오류 [{code}] {message} (log_id={log_id})"
            )
        return data.get("data", {})

    def creator_info(self) -> dict:
        """게시 가능한 privacy_level 목록과 남은 게시 한도를 조회."""
        return self._post(CREATOR_INFO_URL, {})

    # ---------------- 발행 ----------------

    def _resolve_privacy(self, info: dict) -> str:
        wanted = str(self.settings.get("privacy_level", "PUBLIC_TO_EVERYONE"))
        options = info.get("privacy_level_options") or []
        if not options:
            log.warning("creator_info 에 privacy_level_options 가 없습니다. 설정값을 그대로 사용합니다.")
            return wanted
        if wanted in options:
            return wanted
        # 미심사 앱은 대개 SELF_ONLY 만 제공된다
        fallback = "SELF_ONLY" if "SELF_ONLY" in options else options[0]
        log.warning(
            "요청한 privacy_level=%s 를 사용할 수 없습니다(가능: %s). %s 로 대체합니다. "
            "공개 발행이 필요하면 TikTok 개발자 포털에서 앱 심사를 받아야 합니다.",
            wanted, options, fallback,
        )
        return fallback

    def _upload_file(self, upload_url: str, video_path: Path, chunk_size: int) -> None:
        total = video_path.stat().st_size
        with open(video_path, "rb") as fp, client(timeout=600.0) as http:
            offset = 0
            while offset < total:
                chunk = fp.read(chunk_size)
                if not chunk:
                    break
                last = offset + len(chunk) - 1
                response = http.put(
                    upload_url,
                    content=chunk,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{last}/{total}",
                    },
                )
                if response.status_code not in (200, 201, 206):
                    raise PublishError(
                        f"TikTok 업로드 실패 ({response.status_code}): {response.text[:300]}"
                    )
                offset = last + 1
                log.info("  업로드 %d%%", int(offset * 100 / total))

    def _wait_for_publish(self, publish_id: str, timeout: int = 300) -> dict:
        """게시 처리가 끝날 때까지 상태를 확인한다."""
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            time.sleep(10)
            last = self._post(STATUS_URL, {"publish_id": publish_id})
            status = last.get("status", "")
            log.info("  게시 상태: %s", status)
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                return last
            if status == "FAILED":
                raise PublishError(
                    f"TikTok 게시 실패: {last.get('fail_reason', '사유 미상')}"
                )
        log.warning("게시 상태 확인이 시간 초과됐습니다. TikTok 앱에서 직접 확인하세요.")
        return last

    def publish(self, payload: dict[str, Any]) -> PublishResult:
        """payload: {video_path, title}"""
        video_path = Path(payload["video_path"])
        if not video_path.exists():
            raise PublishError(f"영상 파일이 없습니다: {video_path}")

        info = self.creator_info()
        nickname = info.get("creator_nickname", "")
        privacy = self._resolve_privacy(info)

        remaining = info.get("max_video_post_duration_sec")
        if remaining:
            log.info("계정 %s: 최대 영상 길이 %s초", nickname, remaining)

        size = video_path.stat().st_size
        if size <= MAX_SINGLE_CHUNK:
            chunk_size, chunk_count = size, 1
        else:
            chunk_size = CHUNK_SIZE
            chunk_count = (size + chunk_size - 1) // chunk_size

        title = truncate(str(payload.get("title", "")), TITLE_LIMIT)
        body = {
            "post_info": {
                "title": title,
                "privacy_level": privacy,
                "disable_comment": bool(self.settings.get("disable_comment", False)),
                "disable_duet": bool(self.settings.get("disable_duet", False)),
                "disable_stitch": bool(self.settings.get("disable_stitch", False)),
                "video_cover_timestamp_ms": int(payload.get("cover_ms", 1000)),
                # AI 생성 콘텐츠 표시. 미표시 시 정책 위반이 된다.
                "is_aigc": bool(payload.get("synthetic_media")),
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        }

        if payload.get("synthetic_media"):
            log.info("  AI 생성 콘텐츠(is_aigc)로 표시합니다")
        log.info("TikTok 업로드 시작: %s (%.1fMB, privacy=%s)", title[:40], size / 1_048_576, privacy)
        data = self._post(INIT_URL, body)
        publish_id = data.get("publish_id", "")
        upload_url = data.get("upload_url", "")
        if not publish_id or not upload_url:
            raise PublishError(f"TikTok init 응답이 불완전합니다: {str(data)[:300]}")

        self._upload_file(upload_url, video_path, chunk_size)
        status = self._wait_for_publish(publish_id)

        log.info("TikTok 게시 완료 (publish_id=%s)", publish_id)
        return PublishResult(
            platform=self.platform,
            title=title,
            url=f"https://www.tiktok.com/@{nickname}" if nickname else "",
            external_id=publish_id,
            extra={"privacy_level": privacy, "status": status.get("status", "")},
        )
