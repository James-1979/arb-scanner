from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.execution import Fill, HedgeQuote, OrderSide, build_execution_plan, calculate_back_hedges, venue_outcome_pnls_from_fills
from arbscanner.models import Leg, Quote
from arbscanner.venues import (
    BETFAIR,
    MATCHBOOK,
    CanonicalMarketIdentity,
    OrderIntent,
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
    VenueAccount,
    VenueIdentity,
    VenuePositionLeg,
    VenueType,
)

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0843_release_navigation_and_live_boundary():
    assert __version__ == "0.9.36"
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert 'data-nav-child="analytics" data-analytics-tab="execution"' not in HTML
    assert 'data-tab="sports-engines" data-nav-child="sports"' in HTML
    assert 'data-tab="racing-engines" data-nav-child="racing"' in HTML
    assert 'data-tab="sports-execution" data-nav-child="sports"' not in HTML
    assert 'data-tab="racing-execution" data-nav-child="racing"' not in HTML
    assert 'LIVE remains locked' in HTML or 'LIVE is locked' in HTML


def test_execution_analysis_domain_is_server_enforced(tmp_path):
    api = API(tmp_path / "domains.sqlite3")
    rows = [
        {"opportunity_id": 1, "event_name": "Sports pre", "event_key": "sp", "market_name": "Match Odds", "sport": "Football", "monitor_stream": "pre_match", "started_at": "2026-08-12T00:00:00+00:00", "state": "MONITOR_OPEN", "mode": "monitor", "details": {}, "legs_json": "[]", "deployed": 0.0},
        {"opportunity_id": 2, "event_name": "Sports live", "event_key": "si", "market_name": "Match Odds", "sport": "Tennis", "monitor_stream": "in_play", "started_at": "2026-08-12T00:00:00+00:00", "state": "MONITOR_OPEN", "mode": "monitor", "details": {}, "legs_json": "[]", "deployed": 0.0},
        {"opportunity_id": 3, "event_name": "Racing", "event_key": "r", "market_name": "Win", "sport": "Greyhounds", "monitor_stream": "racing", "started_at": "2026-08-12T00:00:00+00:00", "state": "MONITOR_OPEN", "mode": "monitor", "details": {}, "legs_json": "[]", "deployed": 0.0},
    ]
    api.db.execution_history = lambda **kwargs: [dict(row) for row in rows]

    sports = api.activity_analytics({"domain": "sports", "include_results": False, "include_executions": True, "include_metrics": False, "include_all_time": False, "limit": 100})
    racing = api.activity_analytics({"domain": "racing", "include_results": False, "include_executions": True, "include_metrics": False, "include_all_time": False, "limit": 100})
    assert {r["monitor_stream"] for r in sports["executions"]} == {"pre_match", "in_play"}
    assert {r["monitor_stream"] for r in racing["executions"]} == {"racing"}
    assert sports["filters"]["domain"] == "sports"
    assert racing["filters"]["domain"] == "racing"


def test_existing_exchange_providers_have_explicit_capabilities():
    assert BETFAIR.venue.venue_type == VenueType.EXCHANGE
    assert MATCHBOOK.venue.venue_type == VenueType.EXCHANGE
    for spec in (BETFAIR, MATCHBOOK):
        assert spec.capabilities.back_orders is True
        assert spec.capabilities.lay_orders is True
        assert spec.capabilities.order_cancellation is True
        assert spec.capabilities.partial_fills is True
        assert spec.capabilities.settlement is True


def test_synthetic_bookmaker_and_broker_are_valid_without_exchange_lifecycle():
    bookmaker = ProviderSpec(
        "sharpbook",
        VenueIdentity("sharpbook", "Sharp Book", VenueType.BOOKMAKER, "sharpbook"),
        ProviderCapabilities(back_orders=True, lay_orders=False, order_cancellation=False, partial_fills=False, settlement=True),
    )
    broker = ProviderSpec(
        "brokerapi",
        VenueIdentity("brokerapi", "Broker API", VenueType.BROKER, "brokerapi", underlying_venue_id="sharpbook"),
        ProviderCapabilities(back_orders=True, lay_orders=False, order_cancellation=False, partial_fills=False, settlement=True),
    )
    registry = ProviderRegistry([])
    registry.register(bookmaker)
    registry.register(broker)
    assert registry.get("sharpbook").venue.venue_type == VenueType.BOOKMAKER
    assert registry.get("sharpbook").capabilities.lay_orders is False
    assert registry.get("brokerapi").venue.underlying_venue_id == "sharpbook"


def test_canonical_models_do_not_require_exchange_native_ids_or_lay_semantics():
    market = CanonicalMarketIdentity("event:1", "market:match-winner", "selection:home")
    assert market.provider_market_id is None
    leg = VenuePositionLeg(
        venue_id="sharpbook",
        provider_id="sharpbook",
        selection="Home",
        side="BACK",
        requested_odds=2.1,
        requested_stake=20.0,
        economic_exposure={"Home": 22.0, "Away": -20.0},
    )
    account = VenueAccount("acct-1", "sharpbook", "sharpbook", "GBP", "SIM", 500.0)
    intent = OrderIntent("sharpbook", "sharpbook", "Home", "BACK", 20.0, 2.1)
    assert leg.order_reference is None
    assert account.reserved_capital == 0.0
    assert intent.fill_or_kill is False


def test_legacy_betfair_matchbook_quote_and_execution_economics_are_preserved():
    q = Quote("Betfair delayed", "e1", "m1", "Event", "Match Odds", "s1", "Home", 2.0, 100.0, "2026-08-12T00:00:00+00:00")
    assert q.venue_id == "betfair"
    assert q.provider_id == "betfair"
    assert q.executable_capacity == q.liquidity == 100.0
    assert q.displayed_odds == q.executable_odds == q.odds == 2.0
    assert q.capacity_source == "exchange_liquidity"

    legs = [
        Leg("Betfair delayed", "Home", 2.0, 100.0),
        Leg("Matchbook", "Away", 2.1, 100.0),
    ]
    sim = {"executable": True, "stakes": [{"stake": 50.0}, {"stake": 47.619}], "expected_profit": 2.0, "expected_roi_pct": 2.05, "deployed": 97.619, "outcome_pnls": {"Home": 2.0, "Away": 2.0}}
    plan = build_execution_plan(legs, sim)
    assert [x.side for x in plan.legs] == [OrderSide.BACK, OrderSide.BACK]
    assert [x.requested_stake for x in plan.legs] == [50.0, 47.619]
    payload = plan.as_dict()
    assert payload["capital_required_by_exchange"] == {"Betfair delayed": 50.0, "Matchbook": 47.619}
    assert payload["capital_required_by_venue"] == {"betfair": 50.0, "matchbook": 47.619}


def test_dashboard_market_and_replay_v0843_ui_contract():
    assert '0 wins · 0 losses · 0 decided' in HTML
    assert 'Wins divided by decided settled positions today (wins + losses).' in HTML
    assert 'market-analysis-head' in HTML
    assert 'market-header-filters simplefilterbar' in HTML
    assert 'Sports in this period' in HTML
    assert 'timelineReplaySportTiles' in HTML
    assert 'timelineReplayMarketTiles' not in HTML
    assert '.analytics-pane[data-analytics-pane="replay"] .timeline-canvas' in HTML
    assert 'min-width:0!important' in HTML


def test_provider_manifest_is_generic_and_live_locked(tmp_path):
    api = API(tmp_path / "manifest.sqlite3")
    manifest = api.venue_provider_manifest()
    assert manifest["live_execution"] is False
    assert manifest["providers"]["betfair"]["venue"]["venue_type"] == "EXCHANGE"
    assert manifest["providers"]["matchbook"]["venue"]["venue_type"] == "EXCHANGE"


def test_venue_pnl_uses_explicit_venue_identity_not_legacy_exchange_label():
    fills = [
        Fill("f1", "o1", 0, "Broker API", "Home", OrderSide.BACK, 2.0, 10.0, venue_id="brokerapi", provider_id="brokerapi", underlying_venue_id="sharpbook"),
        Fill("f2", "o2", 1, "Betfair delayed", "Away", OrderSide.BACK, 2.0, 10.0, venue_id="betfair", provider_id="betfair"),
    ]
    pnl = venue_outcome_pnls_from_fills(["Home", "Away"], fills)
    assert set(pnl["Home"]) == {"brokerapi", "betfair"}
    assert "broker_api" not in pnl["Home"]
    assert pnl["Home"]["brokerapi"] == 10.0
    assert pnl["Home"]["betfair"] == -10.0


def test_hedge_instructions_and_simulated_hedge_fills_preserve_venue_metadata():
    fills = [Fill("f1", "o1", 0, "Betfair delayed", "Home", OrderSide.BACK, 2.0, 10.0, venue_id="betfair", provider_id="betfair")]
    quotes = {
        "Away": HedgeQuote("Broker API", "Away", 2.0, venue_id="brokerapi", provider_id="brokerapi", underlying_venue_id="sharpbook"),
    }
    instructions, _ = calculate_back_hedges(["Home", "Away"], fills, quotes, target_outcome_pnls={"Home": 0.0, "Away": 0.0})
    assert instructions
    assert instructions[0].venue_id == "brokerapi"
    assert instructions[0].provider_id == "brokerapi"
    assert instructions[0].underlying_venue_id == "sharpbook"


def test_execution_analysis_navigation_keeps_one_shared_implementation():
    assert HTML.count('<div class="analytics-pane" data-analytics-pane="execution">') == 1
    assert 'openExecutionAnalysis("sports")' in HTML or "openExecutionAnalysis('sports')" in HTML
    assert 'openExecutionAnalysis("racing")' in HTML or "openExecutionAnalysis('racing')" in HTML
    assert "domain:executionAnalysisDomain" in HTML
    assert "executionDomainForRow" in HTML
