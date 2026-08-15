from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .archive import archived_hours, default_archive_root, hour_floor, read_archived_rows


class AnalyticsStore:
    """Read planner separating compact summary analytics from detailed history."""

    def __init__(self, db, archive_root: Path | None = None):
        self.db = db
        self.archive_root = Path(archive_root or default_archive_root(db.path))

    @staticmethod
    def _dt(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _hours(start: datetime, end: datetime) -> list[str]:
        cur = hour_floor(start)
        out: list[str] = []
        while cur < end:
            out.append(cur.isoformat())
            cur += timedelta(hours=1)
        return out

    def available_range(self) -> dict:
        return self.db.matched_market_available_range()

    def resolve_range(self, started_at: str | None, finished_at: str | None) -> tuple[str | None, str | None, dict]:
        available = self.available_range()
        start = started_at or available.get("from_utc")
        end = finished_at or available.get("to_utc")
        return start, end, available

    def coverage(self, started_at: str | None, finished_at: str | None) -> dict:
        if not started_at or not finished_at:
            return {
                "summary_history_complete": True,
                "detailed_history_complete": True,
                "summary_history_gaps": [],
                "detailed_history_gaps": [],
                "summary_covered_hours": 0,
                "detailed_covered_hours": 0,
                "expected_hours": 0,
            }
        start = self._dt(started_at)
        end = self._dt(finished_at)
        if end <= start:
            return {
                "summary_history_complete": True,
                "detailed_history_complete": True,
                "summary_history_gaps": [],
                "detailed_history_gaps": [],
                "summary_covered_hours": 0,
                "detailed_covered_hours": 0,
                "expected_hours": 0,
            }
        expected = self._hours(start, end)
        ledger = self.db.matched_market_finalized_hours(start.isoformat(), end.isoformat())
        raw = self.db.matched_market_raw_hours(start.isoformat(), end.isoformat())
        archived = archived_hours(self.archive_root, start, end)
        summary_covered = set(ledger) | set(raw)
        detailed_covered = set(raw) | set(archived)
        summary_gaps = [h for h in expected if h not in summary_covered]
        detailed_gaps = [h for h in expected if h not in detailed_covered]
        return {
            "summary_history_complete": not summary_gaps,
            "detailed_history_complete": not detailed_gaps,
            "summary_history_gaps": summary_gaps,
            "detailed_history_gaps": detailed_gaps,
            "summary_covered_hours": len(summary_covered.intersection(expected)),
            "detailed_covered_hours": len(detailed_covered.intersection(expected)),
            "expected_hours": len(expected),
            "summary_source": "hourly_rollups+hot_sqlite",
            "detailed_source": "verified_parquet+hot_sqlite",
        }

    def market_summary(self, started_at: str | None, finished_at: str | None, *, include_economics: bool = True) -> dict:
        start, end, available = self.resolve_range(started_at, finished_at)
        payload = self.db.market_analysis_between(start, end, include_economics=include_economics)
        coverage = self.coverage(start, end)
        payload.update(coverage)
        payload["history_from_utc"] = start
        payload["history_to_utc"] = end
        payload["history_available_from_utc"] = available.get("from_utc")
        payload["history_available_to_utc"] = available.get("to_utc")
        return payload

    def detailed_history(self, started_at: str | None, finished_at: str | None, *, limit: int = 50000,
                         allow_partial: bool = False, section: str | None = None, sport: str | None = None,
                         market: str | None = None, search: str | None = None, event_key: str | None = None) -> dict:
        if not started_at or not finished_at:
            return {"ok": False, "message": "Detailed historical reads require explicit from_utc and to_utc.", "rows": []}
        start = self._dt(started_at); end = self._dt(finished_at)
        if end <= start:
            return {"ok": False, "message": "to_utc must be later than from_utc.", "rows": []}
        cap = max(1, min(250000, int(limit or 50000)))
        coverage = self.coverage(start.isoformat(), end.isoformat())
        if coverage["detailed_history_gaps"] and not allow_partial:
            return {"ok": False, "message": "Detailed history is incomplete for the requested period.", "rows": [], **coverage,
                    "from_utc": start.isoformat(), "to_utc": end.isoformat(), "limit": cap}

        expected = self._hours(start, end)
        raw_hours = self.db.matched_market_raw_hours(start.isoformat(), end.isoformat())
        archive_hours = archived_hours(self.archive_root, start, end)
        # Prefer verified archive for an hour when present; SQLite supplies the hot/unarchived tail.
        chosen_archive = [h for h in expected if h in archive_hours]
        chosen_sqlite = [h for h in expected if h not in archive_hours and h in raw_hours]
        rows: list[dict] = []
        if chosen_archive:
            rows.extend(read_archived_rows(self.archive_root, chosen_archive, start_utc=start.isoformat(), end_utc=end.isoformat(),
                                           limit=cap + 1, section=section, sport=sport, market=market, search=search,
                                           event_key=event_key))
        if len(rows) <= cap and chosen_sqlite:
            rows.extend(self.db.matched_market_detailed_rows(
                start.isoformat(), end.isoformat(), hours=chosen_sqlite, limit=(cap + 1 - len(rows)),
                section=section, sport=sport, market=market, search=search, event_key=event_key,
            ))
        rows.sort(key=lambda r: (str(r.get("observed_at") or ""), int(r.get("id") or 0)))
        overflow = len(rows) > cap
        if overflow:
            rows = rows[:cap]
        return {
            "ok": not overflow,
            "message": (f"Detailed history exceeds the {cap:,}-row safety ceiling; narrow the period." if overflow else None),
            "rows": rows,
            "count": len(rows),
            "limit": cap,
            "truncated": overflow,
            "from_utc": start.isoformat(),
            "to_utc": end.isoformat(),
            "archive_hours": chosen_archive,
            "sqlite_hours": chosen_sqlite,
            **coverage,
        }
