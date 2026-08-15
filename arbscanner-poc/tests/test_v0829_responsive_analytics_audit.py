import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0829_release_and_ui_scope_present():
    assert __version__ == "0.9.36"
    for token in (
        'id="activePositionsNavCount"',
        'id="performanceFrom" type="datetime-local"',
        'id="performanceTo" type="datetime-local"',
        'id="performanceExactRange"',
        'id="positionResultsWinRate24h"',
        'id="positionResultsSuperbets"',
        'id="marketHeatmapWeekLabel"',
        'Weekly market heatmap',
        'v0.8.42 UI stabilization',
        'Base arb legs, scaled-entry fills, balancing/recovery fills and emergency hedges are shown separately.',
    ):
        assert token in HTML
    assert '.dash-fit' not in HTML.split('</style>',1)[0]
    assert "function performancePeriodChanged()" in HTML
    assert "function shiftMarketHeatmapWeek(delta)" in HTML
    assert "function renderMarketWeekHeatmap()" in HTML


def test_performance_custom_datetime_range_is_exact(tmp_path):
    api = API(tmp_path / "range.sqlite3")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=3)).replace(microsecond=0)
    end = (now - timedelta(hours=1)).replace(microsecond=0)
    result = api.performance_analytics({
        "period": "custom",
        "scope": "all",
        "stream": "all",
        "basis": "actual",
        "from_utc": start.isoformat(),
        "to_utc": end.isoformat(),
        "timezone_offset_minutes": 0,
        "timezone_name": "UTC",
    })
    assert result["ok"] is True
    assert result["filters"]["period"] == "custom"
    assert result["from_utc"].startswith(start.isoformat())
    assert result["to_utc"].startswith(end.isoformat())
    assert "UTC" in result["range_label"]


def test_dashboard_open_position_preserves_fill_roles_and_planned_count(tmp_path):
    api = API(tmp_path / "roles.sqlite3")
    db = api.db
    now = datetime.now(timezone.utc).isoformat()
    legs = [
        {"exchange": "Betfair delayed", "selection": "A", "odds": 2.1, "liquidity": 100},
        {"exchange": "Matchbook", "selection": "B", "odds": 2.1, "liquidity": 100},
    ]
    oid = db.add_opportunity(
        "role-event", "Role Event", now, "Match Winner", 1.0, 1.0, legs, [], 1.0,
        "role-sig", strategy="two-way", sport="Tennis",
    )
    simulation = {
        "stakes": [
            {"exchange": "Betfair delayed", "selection": "A", "odds": 2.1, "stake": 10, "is_hedge": False, "tranche": 1},
            {"exchange": "Matchbook", "selection": "B", "odds": 2.1, "stake": 10, "is_hedge": False, "tranche": 1},
            {"exchange": "Betfair delayed", "selection": "A", "odds": 2.08, "stake": 5, "is_hedge": False, "tranche": 2},
            {"exchange": "Matchbook", "selection": "B", "odds": 2.08, "stake": 5, "is_hedge": False, "tranche": 2},
            {"exchange": "Matchbook", "selection": "A", "odds": 2.0, "stake": 1, "is_hedge": True, "tranche": 2},
        ],
        "after_hedge": {"balanced": True, "worst_case_pnl": 1.0, "best_case_pnl": 1.1},
        "superbet": {"is_superbet": True, "tranche_count": 2, "additional_stake": 10.0},
    }
    with db.lock:
        db.conn.execute(
            """INSERT INTO monitor_positions(
                opportunity_id,event_key,market_name,opened_at,status,deployed,expected_profit,
                stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (oid, "role-event", "Match Winner", now, "OPEN", 31.0, 1.0, "{}", "{}", json.dumps(simulation), "pre_match"),
        )
        db.conn.commit()
    result = api.dashboard_overview({})
    row = next(x for x in result["rows"] if x["opportunity_id"] == oid)
    assert row["planned_leg_count"] == 2
    assert [x["role"] for x in row["bets"]] == ["planned", "planned", "scaled_entry", "scaled_entry", "balancing"]
    assert [x["tranche"] for x in row["bets"]] == [1, 1, 2, 2, 2]


def test_results_and_market_integrity_controls_are_canonical_ui_paths():
    # v0.9.0: Results W/L is the visible settled cohort; the old independent
    # dashboard 24h cohort must not own or overwrite the Results percentage tile.
    results_helper = HTML.split("async function loadResultsIntegrityTiles(){", 1)[1].split("async function loadPositionResults", 1)[0]
    assert "dashboard_results_24h" not in results_helper
    assert "callReadBounded('dashboard_overview'" in HTML
    assert "loadResultsIntegrityTiles()" in HTML
    assert "marketHeatmapWeekOffset" in HTML
    assert "loadMarketHeatmapDay0835" in HTML
    assert "call('market_heatmap'" in HTML
    assert HTML.count('class="helpq"') >= 5


def test_replay_and_results_do_not_flatten_all_fills_as_planned_legs():
    assert "function fillRoleAudit(row)" in HTML
    assert "function compactLegStructure(row)" in HTML
    assert "SCALED T${tranche}" in HTML
    assert "Balance / recovery" in HTML
    assert "Emergency hedge" in HTML
    assert 'id="timelineReplayPnlChart"' not in HTML
    assert 'timeline-return-marker' in HTML
