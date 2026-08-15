from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.models import ExchangeMarket, Quote, canonical_utc_iso, source_time_is_naive
from arbscanner.normalization import match_markets
from arbscanner.racing import normalize_track, track_similarity
from arbscanner.scanner import Scanner
from arbscanner.db import DB
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _race(exchange: str, market_id: str, track: str, start: str, *, raw_start=None, runners: int = 5) -> ExchangeMarket:
    quotes = []
    for i in range(1, runners + 1):
        quotes.append(Quote(
            exchange=exchange,
            event_id=f"{exchange}-{track}",
            market_id=market_id,
            event_name=track,
            market_name="Win",
            selection_id=f"{market_id}-{i}",
            selection=f"Runner {i}",
            odds=6.0 + i / 10,
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
    raw = {
        "_arbscanner_source_start_raw": raw_start if raw_start is not None else start,
        "_arbscanner_start_utc": start,
        "_arbscanner_source_time_naive": source_time_is_naive(raw_start if raw_start is not None else start),
        "_arbscanner_catalogue_runner_count": runners,
        "_arbscanner_priced_runner_count": runners,
    }
    return ExchangeMarket(
        exchange=exchange,
        event_id=f"{exchange}-{track}",
        market_id=market_id,
        event_name=track,
        market_name="Win",
        start_time=start,
        quotes=quotes,
        status="OPEN",
        market_type="win",
        strategy="multi_runner_win",
        sport="Greyhounds",
        in_play=False,
        raw=raw,
        section="racing",
        race_track=normalize_track(track),
        race_number=4,
    )


def test_exchange_times_are_canonical_utc_and_naive_source_is_visible():
    assert canonical_utc_iso("2026-08-11T13:42:00") == "2026-08-11T13:42:00+00:00"
    assert canonical_utc_iso("2026-08-11T13:42:00+01:00") == "2026-08-11T12:42:00+00:00"
    assert canonical_utc_iso(1786452120).endswith("+00:00")
    assert source_time_is_naive("2026-08-11T13:42:00") is True
    assert source_time_is_naive("2026-08-11T13:42:00Z") is False


def test_country_suffix_is_not_part_of_track_identity():
    assert normalize_track("Angle Park AUS") == "angle park"
    assert normalize_track("Gosford (AUS)") == "gosford"
    assert track_similarity("Angle Park AUS", "Angle Park") == 1.0


def test_five_runner_betfair_and_matchbook_are_same_field_regardless_of_ui_pricing_format():
    start = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
    bf = _race("Betfair delayed", "bf-5", "Angle Park AUS", start, runners=5)
    mb = _race("Matchbook", "mb-5", "Angle Park", start, runners=5)
    matches = match_markets([bf, mb], racing_threshold=.90, racing_runner_threshold=.92)
    assert len(matches) == 1
    assert matches[0].runner_count == 5


def test_diagnostics_surface_one_hour_naive_time_mismatch_instead_of_hiding_pair(tmp_path):
    bf_start = "2026-08-11T12:42:00+00:00"
    mb_raw = "2026-08-11T13:42:00"
    mb_start = canonical_utc_iso(mb_raw)
    bf = _race("Betfair delayed", "bf-time", "Newcastle", bf_start, raw_start="2026-08-11T12:42:00Z", runners=5)
    mb = _race("Matchbook", "mb-time", "Newcastle", mb_start, raw_start=mb_raw, runners=5)
    scanner = Scanner(DB(tmp_path / "time.sqlite3"), SecretStore())
    result = scanner._racing_discovery_diagnostics(
        [bf, mb], [],
        {"racing_match_threshold": .90, "racing_runner_match_threshold": .92},
        statuses=[],
    )
    assert result["summary"]["candidates"] == 0
    assert result["summary"]["time_format_suspects"] == 2
    row = next(x for x in result["rows"] if x["exchange"] == "Matchbook")
    assert row["counterpart"]["time_format_suspect"] is True
    assert row["counterpart"]["time_delta_minutes"] == 60.0
    assert "timezone mismatch" in row["reason"].lower()
    assert row["source_start_raw"] == mb_raw
    assert row["event_start_utc"].endswith("+00:00")


def test_racing_ui_separates_runner_count_from_price_completeness_and_shows_time_diagnostics():
    assert "<th>Runners</th><th>Pricing</th>" in HTML
    assert "function racingPricingText" in HTML
    assert "Source UTC" in HTML
    assert "Candidate UTC" in HTML
    assert "CHECK TIMEZONE" in HTML


def test_matchbook_adapter_normalises_start_and_carries_field_pricing_metadata():
    import asyncio
    from arbscanner.adapters import MatchbookAdapter

    class DiagnosticMatchbook(MatchbookAdapter):
        async def _get_events(self, horizon_hours: int, minimum_liquidity: float):
            runners = [
                {"id": i, "name": f"{i}. Runner {i}", "status": "active",
                 "prices": [{"side": "back", "odds": 6.0 + i / 10, "available-amount": 100.0}]}
                for i in range(1, 6)
            ]
            return {
                "_arbscanner_latency_ms": 1,
                "_sport_map": {},
                "events": [{
                    "id": 100,
                    "name": "Angle Park",
                    "start": "2026-08-11T13:42:00",
                    "sport": {"name": "Greyhounds"},
                    "status": "open",
                    "markets": [{"id": 200, "name": "Win", "status": "open", "runners": runners}],
                }],
            }

    adapter = DiagnosticMatchbook(session_token="token", enabled_sports=["Greyhounds"])
    markets = asyncio.run(adapter.fetch_markets(horizon_hours=24, minimum_liquidity=0.0))
    assert len(markets) == 1
    market = markets[0]
    assert market.start_time == "2026-08-11T13:42:00+00:00"
    assert market.raw["_arbscanner_source_start_raw"] == "2026-08-11T13:42:00"
    assert market.raw["_arbscanner_source_time_naive"] is True
    assert market.raw["_arbscanner_catalogue_runner_count"] == 5
    assert market.raw["_arbscanner_priced_runner_count"] == 5
