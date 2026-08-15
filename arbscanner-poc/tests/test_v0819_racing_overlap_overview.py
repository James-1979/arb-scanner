from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _race(exchange: str, market_id: str, track: str, start: str, runners: int = 6) -> ExchangeMarket:
    quotes = []
    for i in range(1, runners + 1):
        quotes.append(Quote(
            exchange=exchange,
            event_id=f"{exchange}-{track}",
            market_id=market_id,
            event_name=f"{track} Greyhounds",
            market_name="Win",
            selection_id=f"{market_id}-{i}",
            selection=f"Runner {i}",
            odds=7.0,
            liquidity=100.0,
            captured_at=datetime.now(timezone.utc).isoformat(),
            start_time=start,
            commission_pct=2.0,
            sport="Greyhounds",
            strategy="multi_runner_win",
            market_type="win",
            in_play=False,
            market_status="OPEN",
            section="racing",
            trap_number=i,
            canonical_selection_key=f"trap:{i}|runner-{i}",
            runner_status="ACTIVE",
        ))
    return ExchangeMarket(
        exchange=exchange,
        event_id=f"{exchange}-{track}",
        market_id=market_id,
        event_name=f"{track} Greyhounds",
        market_name="Win",
        start_time=start,
        quotes=quotes,
        status="OPEN",
        market_type="win",
        strategy="multi_runner_win",
        sport="Greyhounds",
        in_play=False,
        section="racing",
        race_track=track,
        race_number=4,
    )


def test_unpaired_but_same_race_is_exposed_as_candidate(tmp_path):
    start = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
    bf = _race("Betfair delayed", "bf-1", "Romford", start)
    mb = _race("Matchbook", "mb-1", "Romford", start)
    scanner = Scanner(DB(tmp_path / "candidate.sqlite3"), SecretStore())
    result = scanner._racing_discovery_diagnostics(
        [bf, mb], [],
        {"racing_match_threshold": 0.90, "racing_runner_match_threshold": 0.92},
        statuses=[],
    )
    assert result["summary"]["matched"] == 0
    assert result["summary"]["candidates"] == 1
    assert result["summary"]["candidate_sources"] == 2
    assert all(row["match_status"] == "candidate" for row in result["rows"])
    bf_row = next(row for row in result["rows"] if row["exchange"] == "Betfair delayed")
    assert bf_row["counterpart"]["exchange"] == "Matchbook"
    assert bf_row["counterpart"]["identity_likely"] is True
    assert bf_row["counterpart"]["runner_match_count"] == 6
    assert "strict matcher" in bf_row["reason"].lower()


def test_non_overlapping_tracks_remain_unmatched(tmp_path):
    start = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
    bf = _race("Betfair delayed", "bf-1", "Angle Park", start)
    mb = _race("Matchbook", "mb-1", "Newcastle", start)
    scanner = Scanner(DB(tmp_path / "unmatched.sqlite3"), SecretStore())
    result = scanner._racing_discovery_diagnostics(
        [bf, mb], [],
        {"racing_match_threshold": 0.90, "racing_runner_match_threshold": 0.92},
        statuses=[],
    )
    assert result["summary"]["candidates"] == 0
    assert result["summary"]["unmatched"] == 2
    assert all(row["counterpart"] is None for row in result["rows"])


def test_racing_overview_uses_raw_discovery_when_no_matches_exist(tmp_path):
    api = API(db_path=tmp_path / "overview.sqlite3")
    start = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
    api.db.set_setting("racing_discovery_latest", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": 148,
            "matched": 0,
            "candidates": 2,
            "unmatched": 123,
            "rejected": 23,
            "by_exchange": {"Betfair delayed": 139, "Matchbook": 9},
            "betfair_feed": {"catalogue": 139, "fully_priced": 116, "incomplete_prices": 23},
        },
        "rows": [{
            "exchange": "Matchbook",
            "event_id": "mb-e1",
            "market_id": "mb-m1",
            "event_name": "Newcastle",
            "market_name": "Win",
            "event_start": start,
            "race_track": "newcastle",
            "runner_count": 6,
            "country": "GB",
            "match_status": "unmatched",
            "reason": "No suitable counterpart found",
            "in_play": False,
            "market_status": "OPEN",
        }],
    })
    result = api.racing_overview({})
    assert result["ok"] is True
    assert result["summary"]["betfair_detected"] == 139
    assert result["summary"]["matchbook_detected"] == 9
    assert result["summary"]["betfair_complete"] == 116
    assert result["summary"]["betfair_incomplete"] == 23
    assert result["summary"]["candidate_races"] == 2
    assert result["summary"]["feed_health"] == "HEALTHY"
    assert result["summary"]["matched_races"] == 0
    assert result["upcoming"][0]["coverage_state"] == "Matchbook only"
    assert result["monitor_execution_allowed"] is True
    assert result["live_execution_allowed"] is False


def test_racing_ui_has_overview_coverage_and_candidate_diagnostics():
    assert "Racing Overview" in HTML
    assert "Matching coverage" in HTML
    assert "Upcoming races" in HTML
    assert 'id="racingMonCandidates"' in HTML
    assert '<option value="candidate">Candidates</option>' in HTML
    assert '<option value="single_venue">Single venue only</option>' in HTML
    assert "racingMonitorDetail" in HTML
    assert "Runner alignment" in HTML
    assert "monitor only" in HTML.lower()
    assert "live order placement" in HTML.lower()
    assert "locked" in HTML.lower()
