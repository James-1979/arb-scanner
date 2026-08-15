from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.replay import replay_analysis


def _add_settled(db: DB, name: str, quality: str) -> int:
    liquidity = 2.0 if quality == "Tiny" else 50.0
    legs = [
        {
            "exchange": "Matchbook", "selection": "Alpha", "odds": 2.2, "liquidity": liquidity,
            "commission_pct": 0.0, "commission_source": "test", "sport": "Football",
        },
        {
            "exchange": "Betfair delayed", "selection": "Beta", "odds": 2.2, "liquidity": liquidity,
            "commission_pct": 0.0, "commission_source": "test", "sport": "Football",
        },
    ]
    oid = db.add_opportunity(
        name.lower(), name, "2026-08-10T12:00:00+00:00", "Match Winner", 9.0, 10.0,
        legs, [], 0.99, f"sig-{name}", strategy="two-way", sport="Football",
    )
    db.settle(oid, "Alpha", notes="test result")
    return oid


def test_replay_minimum_quality_filters_history_and_surfaces_quality(tmp_path):
    db = DB(tmp_path / "replay-quality.sqlite3")
    _add_settled(db, "Tiny Event", "Tiny")
    _add_settled(db, "Strong Event", "Strong")

    result = replay_analysis(
        db, 500.0, min_profit=0.0, min_deployed_roi_pct=0.0, minimum_quality_band="Strong"
    )

    assert result["counts"]["settled_available"] == 2
    assert result["counts"]["skipped_quality"] == 1
    assert result["counts"]["taken"] == 1
    assert result["filters"]["minimum_quality_band"] == "Strong"
    assert result["events"][0]["quality_band"] in {"Strong", "Excellent"}
    assert float(result["events"][0]["quality_score"]) >= 60.0


def test_api_replay_passes_quality_floor_before_monitor_evidence(tmp_path):
    api = API(tmp_path / "api-replay-quality.sqlite3")
    _add_settled(api.db, "Tiny API Event", "Tiny")
    _add_settled(api.db, "Strong API Event", "Strong")

    response = api.analytics_replay({
        "starting_capital": 500, "minimum_profit": 0, "minimum_deployed_roi_pct": 0,
        "minimum_quality_band": "Strong",
    })
    assert response["ok"] is True
    result = response["result"]
    assert result["counts"]["settled_available"] == 2
    assert result["counts"]["skipped_quality"] == 1
    assert result["filters"]["minimum_quality_band"] == "Strong"


def test_single_top_navigation_replay_quality_and_negative_locked_profit_semantics():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    nav = html.split('<div class="nav" id="nav"', 1)[1].split('<section id="dashboard"', 1)[0]
    for label in ("Dashboard", "Active Positions", "Analytics", "Sports", "Racing", "Admin", "Help"):
        assert f">{label}<" in nav
    assert 'data-tab="executions" data-nav-child="sports"' not in nav
    assert 'data-tab="replay"' not in nav
    assert 'data-tab="racing"' in nav
    assert 'class="nav-disabled"' not in nav
    assert "settings-nav" not in html
    assert "Other settings" not in html
    assert 'id="replayQuality"' in html
    assert "Minimum quality" in html
    assert "<th>Quality</th>" in html
    assert "signClass(active.locked_profit)" in html
    assert "signClass(active.locked_return_pct)" in html
    assert "assets/betfair-mark.svg" in html
    assert "assets/matchbook-mark.svg" in html
    assets = Path(__file__).parents[1].joinpath("frontend", "assets")
    assert assets.joinpath("betfair-mark.svg").exists()
    assert assets.joinpath("matchbook-mark.svg").exists()
    assert "#dashboard.dashboard-clean.active{display:grid" in html
