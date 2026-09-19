"""발행 이력 / 일일 할당량 상태 저장소.

GitHub Actions 처럼 컨테이너가 매번 초기화되는 환경을 전제로,
상태는 레포에 커밋되는 단일 JSON 파일에 보관한다.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .logutil import get_logger
from .util import KST, now_kst, today_key, topic_key

log = get_logger(__name__)


@dataclass
class PublishRecord:
    platform: str
    topic: str
    topic_hash: str
    title: str
    url: str = ""
    external_id: str = ""
    published_at: str = field(default_factory=lambda: now_kst().isoformat())
    extra: dict[str, Any] = field(default_factory=dict)


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {"records": [], "counters": {}, "version": 1}
        self.load()

    # ---------------- 입출력 ----------------

    def load(self) -> None:
        if not self.path.exists():
            log.info("상태 파일이 없어 새로 시작합니다: %s", self.path)
            return
        try:
            with open(self.path, encoding="utf-8") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("상태 파일을 읽지 못해 초기화합니다 (%s): %s", self.path, exc)

    def save(self) -> None:
        """원자적 저장 — 중간에 죽어도 파일이 깨지지 않도록."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(self._data, fp, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ---------------- 조회 ----------------

    @property
    def records(self) -> list[dict[str, Any]]:
        return self._data.setdefault("records", [])

    def count_today(self, platform: str) -> int:
        """오늘(KST) 해당 플랫폼에 성공 발행한 횟수."""
        key = today_key()
        return sum(
            1
            for r in self.records
            if r.get("platform") == platform
            and str(r.get("published_at", "")).startswith(key)
        )

    def last_published_at(self, platform: str) -> datetime | None:
        stamps = [
            r["published_at"]
            for r in self.records
            if r.get("platform") == platform and r.get("published_at")
        ]
        if not stamps:
            return None
        try:
            return datetime.fromisoformat(max(stamps))
        except ValueError:
            return None

    def minutes_since_last(self, platform: str) -> float:
        last = self.last_published_at(platform)
        if last is None:
            return float("inf")
        if last.tzinfo is None:
            last = last.replace(tzinfo=KST)
        return (now_kst() - last).total_seconds() / 60.0

    def is_duplicate(self, topic: str, *, within_days: int, platform: str | None = None) -> bool:
        """최근 N일 안에 같은 주제를 발행했는지."""
        key = topic_key(topic)
        cutoff = now_kst() - timedelta(days=within_days)
        for record in self.records:
            if record.get("topic_hash") != key:
                continue
            if platform and record.get("platform") != platform:
                continue
            try:
                stamp = datetime.fromisoformat(record["published_at"])
            except (KeyError, ValueError):
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=KST)
            if stamp >= cutoff:
                return True
        return False

    # ---------------- 기록 ----------------

    def add(self, record: PublishRecord) -> None:
        self.records.append(asdict(record))
        log.info(
            "기록됨: [%s] %s → %s", record.platform, record.title, record.url or "(URL 없음)"
        )

    def record_publish(
        self,
        platform: str,
        topic: str,
        title: str,
        url: str = "",
        external_id: str = "",
        **extra: Any,
    ) -> PublishRecord:
        record = PublishRecord(
            platform=platform,
            topic=topic,
            topic_hash=topic_key(topic),
            title=title,
            url=url,
            external_id=external_id,
            extra=extra,
        )
        self.add(record)
        return record

    def prune(self, keep_days: int = 90) -> int:
        """오래된 기록 정리. 삭제 건수를 반환."""
        cutoff = now_kst() - timedelta(days=keep_days)
        before = len(self.records)
        kept = []
        for record in self.records:
            try:
                stamp = datetime.fromisoformat(record["published_at"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=KST)
            except (KeyError, ValueError):
                continue  # 형식이 깨진 기록은 버린다
            if stamp >= cutoff:
                kept.append(record)
        self._data["records"] = kept
        return before - len(kept)
