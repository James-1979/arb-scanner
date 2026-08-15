from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, OPERATING_MODES

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _leg(exchange: str, selection: str = "Alpha") -> dict:
    return {
        "exchange": exchange,
        "selection": selection,
        "odds": 2.1,
        "available": 500.0,
        "commission_pct": 2.0,
    }


def _source(exchange: str, market_id: str) -> dict:
    return {"exchange": exchange, "market_id": market_id}


def test_v0841_version_live_lock_and_market_comparator_markup():
    # The v0.8.41 comparator API remains a compatibility contract, while the
    # current Market Analysis UI is provider-registry driven.
    assert __version__ == "0.9.39"
    assert OPERATING_MODES["live"]["available"] is False
    for element_id in (
        "marketObserved", "marketPositive", "marketLiquidityCapable",
        "marketQualified", "marketAttempted", "marketExecuted", "marketSettled",
        "marketVenueSummary",
    ):
        assert f'id="{element_id}"' in HTML
    assert "marketBetfairMarkets" not in HTML
    assert "marketMatchbookMarkets" not in HTML
    assert "marketExchangeOverlap" not in HTML
    assert "renderMarketVenueSummary091" in HTML

def test_exchange_comparator_deduplicates_market_scans_and_counts_opportunity_contribution(tmp_path: Path):
    api = API(tmp_path / "v0841.sqlite3")
    db = api.db
    sid = db.start_scan()
    both_legs = [_leg("Betfair delayed", "Alpha"), _leg("Matchbook", "Beta")]
    both_sources = [_source("Betfair delayed", "bf-1"), _source("Matchbook", "mb-1")]

    # Same canonical event+market observed twice: market counts must remain one.
    for _ in range(2):
        db.add_matched_market(
            sid, "event-1", "Alpha v Beta", None, "Match Winner", 1.0,
            1.0, 1.0, 0.0, 1.0, 100.0, 1.0, "", "recommended", "",
            both_legs, both_sources, sport="Football", section="sports", in_play=False,
        )

    # The same market later observed in-play still counts once when phase=all.
    db.add_matched_market(
        sid, "event-1", "Alpha v Beta", None, "Match Winner", 1.0,
        1.0, 1.0, 0.0, 1.0, 100.0, 1.0, "", "in_play_monitor", "",
        both_legs, both_sources, sport="Football", section="sports", in_play=True,
    )

    # A second Betfair-only market expands Betfair and the union, but not overlap.
    db.add_matched_market(
        sid, "event-2", "Gamma v Delta", None, "Match Winner", 1.0,
        0.5, 0.5, 0.0, 0.5, 100.0, 0.5, "", "observed", "",
        [_leg("Betfair delayed", "Gamma")], [_source("Betfair delayed", "bf-2")],
        sport="Football", section="sports", in_play=False,
    )

    # Opportunities are stored opportunity records, not scanner observations.
    db.add_opportunity(
        "event-1", "Alpha v Beta", None, "Match Winner", 1.0, 1.0,
        both_legs, both_sources, 1.0, "v0841-both", sport="Football", section="sports", in_play=False,
    )
    db.add_opportunity(
        "event-2", "Gamma v Delta", None, "Match Winner", 0.5, 0.5,
        [_leg("Betfair delayed", "Gamma")], [_source("Betfair delayed", "bf-2")],
        1.0, "v0841-bf", sport="Football", section="sports", in_play=False,
    )

    now = datetime.now(timezone.utc)
    result = api.market_analysis({
        "from_utc": (now - timedelta(hours=1)).isoformat(),
        "to_utc": (now + timedelta(hours=1)).isoformat(),
        "scope": "sports",
        "phase": "pre_match",
        "sport": "Football",
    })
    comp = result["exchange_comparator"]
    assert comp["betfair_markets"] == 2
    assert comp["matchbook_markets"] == 1
    assert comp["overlap_markets"] == 1
    assert comp["union_markets"] == 2
    assert comp["overlap_pct"] == 50.0
    assert comp["betfair_opportunities"] == 2
    assert comp["matchbook_opportunities"] == 1

    all_phase = api.market_analysis({
        "from_utc": (now - timedelta(hours=1)).isoformat(),
        "to_utc": (now + timedelta(hours=1)).isoformat(),
        "scope": "sports",
        "phase": "all",
        "sport": "Football",
    })["exchange_comparator"]
    assert all_phase["betfair_markets"] == 2
    assert all_phase["matchbook_markets"] == 1
    assert all_phase["overlap_markets"] == 1
    assert all_phase["union_markets"] == 2
    assert all_phase["overlap_pct"] == 50.0


def test_replay_is_period_review_with_only_agreed_time_presets():
    replay = HTML.split('<div class="analytics-pane" data-analytics-pane="replay">', 1)[1].split(
        '<div class="analytics-pane" data-analytics-pane="scenarios">', 1
    )[0]
    assert '<h2 style="margin:0">Period Review</h2>' in replay
    period = replay.split('id="timelineReplayPeriod"', 1)[1].split('</select>', 1)[0]
    assert '<option value="7d">7 days</option>' in period
    assert '<option value="24h">24 hours</option>' in period
    assert '<option value="today" selected>Today</option>' in period
    assert '<option value="custom">Custom period</option>' in period
    for old_value in ("1h", "3h", "6h", "12h", "previous_day"):
        assert f'value="{old_value}"' not in period

    for element_id in (
        "timelineReplayPositions", "timelineReplayWon", "timelineReplayLost", "timelineReplayProfit",
        "timelineReplayDeployed", "timelineReplayRoi", "timelineReplayBest", "timelineReplayWorst",
        "replayStreamPre", "replayStreamInplay", "replayStreamRacing",
        "timelineReplayActiveMarket", "timelineReplayLargestDeployment", "timelineReplayHedges", "timelineReplaySuperbets",
    ):
        assert f'id="{element_id}"' in replay
    assert 'id="timelineReplayLegs"' not in replay
    assert "What happened when" in replay


def test_market_discovery_and_heatmap_polish_contracts_are_present():
    market = HTML.split('<div class="analytics-pane" data-analytics-pane="market">', 1)[1].split(
        '<div class="analytics-pane" data-analytics-pane="replay">', 1
    )[0]
    assert "market-discovery-grid" in market
    assert "Sports · Pre-match" in market
    assert "Sports · In-play" in market
    assert "Greyhound discovery" in market
    assert "future" in HTML
    assert "unobserved" in HTML
    assert "current-hour" in HTML
