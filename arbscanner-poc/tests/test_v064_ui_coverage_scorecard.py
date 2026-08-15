import asyncio
from pathlib import Path

import arbscanner.adapters as adapters_module
from arbscanner.adapters import MatchbookAdapter
from arbscanner.api import API
from arbscanner.sports import SUPPORTED_SPORTS


def test_sports_coverage_always_lists_all_supported_sports(tmp_path):
    api = API(tmp_path / "db.sqlite3")
    api.db.sport_coverage = lambda: {
        "scan": {"id": 1, "started_at": "2026-08-09T16:00:00+00:00", "finished_at": "2026-08-09T16:01:00+00:00"},
        "rows": [
            {
                "sport": "Football",
                "markets_seen": 12,
                "matched": 3,
                "live_matched": 1,
                "theoretical_arbs": 1,
                "net_positive": 1,
                "recommended": 0,
            }
        ],
    }
    result = api.sport_coverage()
    assert result["ok"] is True
    assert [r["sport"] for r in result["rows"][: len(SUPPORTED_SPORTS)]] == list(SUPPORTED_SPORTS)
    rugby = next(r for r in result["rows"] if r["sport"] == "Rugby Union")
    assert rugby["enabled"] is True
    assert rugby["markets_seen"] == 0
    assert rugby["matched"] == 0


def test_scorecard_counts_unique_tracks_and_uses_window_observation_peaks(tmp_path):
    api = API(tmp_path / "db.sqlite3")
    api.db.track_observations_since = lambda cutoff: [
        {
            "track_key": "a", "strategy": "1x2", "sport": "Football",
            "quality_score": 50, "quality_band": "Usable", "bankroll_roi_pct": 0.10,
            "deployed": 100, "expected_profit": 0.50,
        },
        {
            "track_key": "a", "strategy": "1x2", "sport": "Football",
            "quality_score": 80, "quality_band": "Excellent", "bankroll_roi_pct": 0.30,
            "deployed": 120, "expected_profit": 1.50,
        },
        {
            "track_key": "b", "strategy": "two-way", "sport": "Tennis",
            "quality_score": 65, "quality_band": "Strong", "bankroll_roi_pct": 0.20,
            "deployed": 80, "expected_profit": 0.80,
        },
    ]
    result = api.research_scorecard({"days": 7})
    assert result["opportunities"] == 2
    assert result["observations"] == 3
    assert result["strong_or_better"] == 2
    assert result["peak_paper_profit_total"] == 2.30
    assert result["peak_deployable_total"] == 200.00
    assert result["average_peak_bankroll_roi_pct"] == 0.25
    assert result["by_strategy"]["1x2"]["count"] == 1
    assert result["by_strategy"]["two-way"]["count"] == 1
    assert "selected period" in result["conclusion_note"]


def test_opportunity_drawer_uses_full_detail_renderer():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert 'function openOpportunityDrawer(id)' in html
    assert "loadOpportunity(Number(id),'drawerBody',false,true)" in html
    assert "$('drawerCloseBtn')?.addEventListener('click',closeOpportunityDrawer)" in html
    assert "$('oppDrawerBackdrop')?.addEventListener('click',closeOpportunityDrawer)" in html
    assert 'class="drawer-body"' in html
    assert 'class="opphead"' in html
    assert 'Full paper calculation' in html


class _FakeResponse:
    status_code = 200

    def __init__(self, sport_id):
        self.sport_id = sport_id

    def json(self):
        return {"events": [{"id": f"event-{self.sport_id}", "sport-id": self.sport_id, "markets": []}]}


class _FakeClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, headers=None):
        self.__class__.calls.append(dict(params or {}))
        return _FakeResponse(str((params or {}).get("sport-ids") or "all"))


def test_matchbook_queries_enabled_sports_separately(monkeypatch):
    adapter = MatchbookAdapter(session_token="token", enabled_sports=["Football", "Rugby Union"])

    async def fake_lookup():
        return {"1": "Football", "2": "Rugby Union", "3": "Tennis"}

    adapter._sports_lookup = fake_lookup
    _FakeClient.calls = []
    monkeypatch.setattr(adapters_module.httpx, "AsyncClient", _FakeClient)
    result = asyncio.run(adapter._get_events(24, 2.0))
    assert [c.get("sport-ids") for c in _FakeClient.calls] == ["1", "2"]
    assert len(result["events"]) == 2
