from __future__ import annotations

import asyncio
from pathlib import Path

from arbscanner.adapters import BetfairDelayedAdapter
from arbscanner.db import DB
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore

ROOT = Path(__file__).parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _catalogue():
    return [{
        "marketId": "1.234567",
        "marketName": "Win",
        "marketStartTime": "2026-08-11T13:00:00Z",
        "_arbscanner_sport": "Greyhounds",
        "description": {"marketType": "WIN"},
        "eventType": {"id": "4339", "name": "Greyhound Racing"},
        "event": {"id": "e1", "name": "Newcastle 11th Aug", "countryCode": "GB"},
        "runners": [
            {"selectionId": i, "runnerName": f"{i}. Runner {i}", "metadata": {"CLOTH_NUMBER": str(i)}}
            for i in range(1, 7)
        ],
    }]


def _book(*, missing_price: int | None = None):
    runners = []
    for i in range(1, 7):
        available = [] if i == missing_price else [{"price": 7.0 + i / 10.0, "size": 100.0}]
        runners.append({"selectionId": i, "status": "ACTIVE", "ex": {"availableToBack": available}})
    return [{"marketId": "1.234567", "status": "OPEN", "inplay": False, "runners": runners}]


class DiagnosticBetfair(BetfairDelayedAdapter):
    def __init__(self, books):
        super().__init__(app_key="key", session_token="token", enabled_sports=["Greyhounds"])
        self._books = books

    async def list_catalogue(self, horizon_hours: int):
        self.last_racing_discovery["event_type_visible"] = True
        self.last_racing_discovery["event_type_name"] = "Greyhound Racing"
        return _catalogue()

    async def get_discount_rate(self):
        return 0.0

    async def list_books(self, market_ids):
        return self._books, 2


def test_incomplete_betfair_greyhound_field_is_visible_but_not_executable():
    adapter = DiagnosticBetfair(_book(missing_price=6))
    markets = asyncio.run(adapter.fetch_markets(horizon_hours=24, minimum_liquidity=0.0))
    assert markets == []
    diag = adapter.last_racing_discovery
    assert diag["event_type_visible"] is True
    assert diag["catalogue"] == 1
    assert diag["books_returned"] == 1
    assert diag["fully_priced"] == 0
    assert diag["incomplete_prices"] == 1
    assert diag["normalised"] == 0
    assert len(diag["rows"]) == 1
    row = diag["rows"][0]
    assert row["priced_runner_count"] == 5
    assert row["catalogue_runner_count"] == 6
    assert row["match_status"] == "rejected"
    assert "5/6" in row["reason"]


def test_complete_betfair_greyhound_field_remains_eligible_for_matching():
    adapter = DiagnosticBetfair(_book())
    markets = asyncio.run(adapter.fetch_markets(horizon_hours=24, minimum_liquidity=0.0))
    assert len(markets) == 1
    assert markets[0].sport == "Greyhounds"
    assert len(markets[0].quotes) == 6
    diag = adapter.last_racing_discovery
    assert diag["catalogue"] == 1
    assert diag["fully_priced"] == 1
    assert diag["normalised"] == 1
    assert diag["rows"][0]["quality_band"] == "complete"


def test_racing_monitor_merges_catalogue_only_betfair_rows_without_matching_them(tmp_path):
    scanner = Scanner(DB(tmp_path / "diag.sqlite3"), SecretStore())
    telemetry = {
        "event_type_visible": True,
        "event_type_name": "Greyhound Racing",
        "catalogue": 1,
        "books_returned": 1,
        "fully_priced": 0,
        "incomplete_prices": 1,
        "missing_books": 0,
        "in_play_excluded": 0,
        "normalised": 0,
        "rows": [{
            "exchange": "Betfair delayed", "event_id": "e1", "market_id": "m1",
            "event_name": "Newcastle", "market_name": "Win", "event_start": "2026-08-11T13:00:00Z",
            "race_track": "newcastle", "runner_count": 6, "catalogue_runner_count": 6,
            "priced_runner_count": 5, "missing_price_count": 1, "in_play": False,
            "market_status": "OPEN", "country": "GB", "match_status": "rejected",
            "quality_band": "failed", "reason": "Incomplete Betfair prices: 5/6 active runners priced",
        }],
    }
    result = scanner._racing_discovery_diagnostics([], [], {"racing_match_threshold": 0.90}, statuses=[{
        "exchange": "Betfair delayed", "ok": True, "racing_discovery": telemetry,
    }])
    assert result["summary"]["by_exchange"]["Betfair delayed"] == 1
    assert result["summary"]["betfair_feed"]["catalogue"] == 1
    assert result["summary"]["betfair_feed"]["incomplete_prices"] == 1
    assert len(result["rows"]) == 1
    assert result["rows"][0]["match_status"] == "rejected"


def test_racing_ui_uses_real_betfair_exchange_key_and_feed_diagnostics():
    assert "be['Betfair delayed']" in HTML
    assert 'id="racingMonVenue"' in HTML
    assert "venueIds=[...new Set(racingMonitorAll.map(x=>String(x.exchange||'').trim()).filter(Boolean))]" in HTML
    assert "Betfair Greyhound feed" in HTML
    assert "Catalogue races" in HTML
    assert "Incomplete fields" in HTML
    assert "CHECK FEED" in HTML
