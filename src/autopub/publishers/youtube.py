"""YouTube Data API v3 업로드.

무료 쿼터는 프로젝트당 10,000 units/day 이고 videos.insert 가 1,600 units 라서
하루 업로드는 6회가 물리적 상한이다. daily_max 를 비워 두면 이 값을 자동 계산한다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import env
from ..logutil import get_logger
from ..util import truncate
from .base import Publisher, PublishError, PublishResult

log = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TITLE_LIMIT = 100
DESC_LIMIT = 5000
TAGS_TOTAL_LIMIT = 500  # 태그 전체 길이 합 제한


def load_credentials(token_file: str, client_secrets: str | None = None):
    """저장된 리프레시 토큰으로 자격증명을 만든다.

    토큰이 없으면 scripts/youtube_oauth.py 로 최초 1회 인증을 받아야 한다.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = Path(token_file)
    if not path.exists():
        raise PublishError(
            f"YouTube 토큰 파일이 없습니다: {token_file}\n"
            "먼저 `python scripts/youtube_oauth.py` 를 실행해 인증하세요."
        )

    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            log.info("YouTube 액세스 토큰 갱신 중…")
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise PublishError(
                "YouTube 자격증명이 유효하지 않습니다. 재인증이 필요합니다: "
                "python scripts/youtube_oauth.py"
            )
    return creds


class YouTubePublisher(Publisher):
    platform = "youtube"

    @property
    def daily_max(self) -> int:
        """설정값이 없으면 무료 쿼터에서 자동 계산한다."""
        configured = self.settings.get("daily_max")
        quota = int(self.settings.get("daily_quota_units", 10000))
        cost = int(self.settings.get("upload_cost_units", 1600))
        reserve = int(self.settings.get("reserve_units", 400))
        derived = max(0, (quota - reserve) // max(cost, 1))

        if configured is None:
            return derived
        # 설정값이 쿼터로 가능한 것보다 크면 쿼터 쪽을 따른다
        if int(configured) > derived:
            log.warning(
                "youtube.daily_max=%s 는 무료 쿼터로 불가능합니다. %d 로 제한합니다.",
                configured, derived,
            )
            return derived
        return int(configured)

    def _service(self):
        from googleapiclient.discovery import build

        creds = load_credentials(
            env("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json"),
            env("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json"),
        )
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def _trim_tags(self, tags: list[str]) -> list[str]:
        result, total = [], 0
        for tag in tags:
            clean = str(tag).strip()
            if not clean:
                continue
            if total + len(clean) > TAGS_TOTAL_LIMIT:
                break
            result.append(clean)
            total += len(clean)
        return result

    def build_body(self, payload: dict[str, Any]) -> dict:
        """videos.insert 에 보낼 본문. 업로드와 분리해 두어 검증이 쉽다."""
        title = truncate(
            str(payload["title"]).replace("<", "").replace(">", ""), TITLE_LIMIT
        )
        body = {
            "snippet": {
                "title": title,
                "description": truncate(str(payload.get("description", "")), DESC_LIMIT),
                "tags": self._trim_tags(payload.get("tags", [])),
                "categoryId": str(self.settings.get("category_id", "24")),
                "defaultLanguage": "ko",
                "defaultAudioLanguage": "ko",
            },
            "status": {
                "privacyStatus": str(self.settings.get("privacy_status", "public")),
                "selfDeclaredMadeForKids": bool(self.settings.get("made_for_kids", False)),
            },
        }

        # AI 생성 이미지/진행자를 쓴 영상은 반드시 합성 콘텐츠로 표시해야 한다.
        # 미표시 상태로 반복 업로드하면 채널 제재 대상이 된다.
        if payload.get("synthetic_media"):
            body["status"]["containsSyntheticMedia"] = True
            log.info("  합성 콘텐츠(AI 생성)로 표시합니다")

        return body

    def publish(self, payload: dict[str, Any]) -> PublishResult:
        """payload: {video_path, title, description, tags, thumbnail_path?}"""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        video_path = Path(payload["video_path"])
        if not video_path.exists():
            raise PublishError(f"영상 파일이 없습니다: {video_path}")

        body = self.build_body(payload)
        title = body["snippet"]["title"]

        service = self._service()
        media = MediaFileUpload(
            str(video_path), mimetype="video/mp4", chunksize=4 * 1024 * 1024, resumable=True
        )

        log.info("YouTube 업로드 시작: %s (%.1fMB)", title, video_path.stat().st_size / 1_048_576)
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        try:
            while response is None:
                status, response = request.next_chunk()
                if status:
                    log.info("  업로드 %d%%", int(status.progress() * 100))
        except HttpError as exc:
            if exc.resp.status == 403 and b"quotaExceeded" in (exc.content or b""):
                raise PublishError(
                    "YouTube API 일일 쿼터를 초과했습니다. 내일 다시 시도하거나 "
                    "Google Cloud 콘솔에서 쿼터 증설을 신청하세요."
                ) from exc
            raise PublishError(f"YouTube 업로드 실패: {exc}") from exc

        video_id = response["id"]
        url = f"https://www.youtube.com/watch?v={video_id}"
        log.info("YouTube 업로드 완료: %s", url)

        # 썸네일은 채널 인증(전화 확인)이 되어 있어야 한다. 실패해도 발행은 성공으로 본다.
        thumbnail = payload.get("thumbnail_path")
        if thumbnail and Path(thumbnail).exists():
            try:
                service.thumbnails().set(
                    videoId=video_id, media_body=MediaFileUpload(str(thumbnail))
                ).execute()
                log.info("  썸네일 설정 완료")
            except HttpError as exc:
                log.warning("  썸네일 설정 실패(무시): %s", exc)

        return PublishResult(
            platform=self.platform,
            title=title,
            url=url,
            external_id=video_id,
            extra={"privacy": body["status"]["privacyStatus"]},
        )
