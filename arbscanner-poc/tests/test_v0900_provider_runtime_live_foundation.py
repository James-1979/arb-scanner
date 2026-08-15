from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API, OPERATING_MODES
from arbscanner.contracts import SERVICE_BOUNDARY_MANIFEST, assert_contract_serializable
from arbscanner.engine import best_strategy_legs
from arbscanner.models import ExchangeMarket, Leg, Quote
from arbscanner.modes import FeedEntitlement, canonical_mode_value, live_feed_eligible
from arbscanner.normalization import match_markets
from arbscanner.provider_runtime import ProviderRuntimeProfile, ProviderRuntimeRegistry, default_provider_runtime_registry
from arbscanner.secrets import SecretStore
from arbscanner.venues import BETDAQ_SHAPE, SMARKETS_SHAPE, ProviderRegistry, new_order_intent

ROOT = Path(__file__).resolve().parents[1]


def _market(provider: str, odds=(2.2, 3.6, 3.8)) -> ExchangeMarket:
    display = {
        "betfair": "Betfair delayed",
        "matchbook": "Matchbook",
        "smarkets": "Smarkets",
        "betdaq": "BETDAQ",
    }[provider]
    captured = "2026-08-12T12:00:00+00:00"
    names = ("Alpha", "Draw", "Beta")
    quotes = [
        Quote(display, f"{provider}-evt", f"{provider}-mkt", "Alpha v Beta", "Match Odds",
              f"{provider}-{idx}", name, price, 100.0, captured,
              start_time="2026-08-12T19:00:00+00:00", sport="Football",
              provider_id=provider, venue_id=provider,
              feed_entitlement="delayed" if provider == "betfair" else "live",
              market_data_transport="poll")
        for idx, (name, price) in enumerate(zip(names, odds), 1)
    ]
    return ExchangeMarket(
        display, f"{provider}-evt", f"{provider}-mkt", "Alpha v Beta", "Match Odds",
        "2026-08-12T19:00:00+00:00", quotes, sport="Football",
        provider_id=provider, venue_id=provider,
        feed_entitlement="delayed" if provider == "betfair" else "live",
        market_data_transport="poll",
    )


def test_v0900_identity_modes_and_live_lock(tmp_path):
    assert __version__ == "0.9.36"
    assert set(OPERATING_MODES) == {"sim", "live"}
    assert OPERATING_MODES["sim"]["available"] is True
    assert OPERATING_MODES["live"]["available"] is False
    api = API(tmp_path / "v0900.sqlite3")
    state = api.get_state()
    assert state["version"] == "0.9.36"
    assert state["settings"]["mode"] == "sim"
    assert state["settings"]["live_execution_available"] is False


@pytest.mark.parametrize("legacy", ["monitor", "watch", "paper", "simulate", "simulation", "monitor_timing", "research"])
def test_legacy_economic_modes_canonicalise_to_sim(legacy):
    assert canonical_mode_value(legacy) == "sim"
    assert canonical_mode_value("live") == "live"


def test_service_boundary_is_serialisable_and_rpc_ready_without_grpc_dependencies():
    payload = assert_contract_serializable(SERVICE_BOUNDARY_MANIFEST)
    assert payload["ready"] is True
    assert payload["transport"] == "in_process"
    assert payload["grpc_enabled"] is False
    assert payload["protobuf_enabled"] is False
    with pytest.raises(TypeError):
        assert_contract_serializable({"provider_client": lambda: None})
    requirements = (ROOT / "requirements.txt").read_text().lower() if (ROOT / "requirements.txt").exists() else ""
    assert "grpcio" not in requirements
    assert "protobuf" not in requirements


def test_default_provider_runtime_is_serialisable_and_live_ineligible():
    registry = default_provider_runtime_registry()
    manifest = assert_contract_serializable(registry.manifest())
    assert set(manifest) == {"betfair", "matchbook", "smarkets"}
    assert manifest["smarkets"]["runtime_profile"]["api_state"] == "awaiting_api_access"
    assert manifest["betfair"]["runtime_profile"]["feed_entitlement"] == "delayed"
    assert manifest["matchbook"]["runtime_profile"]["feed_entitlement"] == "live"
    for provider in ("betfair", "matchbook"):
        gate = registry.live_eligibility(provider, global_live_unlocked=False)
        assert gate["eligible"] is False
        assert gate["checks"]["global_live_unlocked"] is False
        assert gate["checks"]["execution_enabled"] is False


def test_synthetic_smarkets_and_betdaq_shapes_register_without_network_adapters():
    runtime = ProviderRuntimeRegistry(ProviderRegistry([]))
    runtime.register_provider(SMARKETS_SHAPE, profile=ProviderRuntimeProfile("smarkets", enabled=True))
    runtime.register_provider(BETDAQ_SHAPE, profile=ProviderRuntimeProfile("betdaq", enabled=True))
    manifest = assert_contract_serializable(runtime.manifest())
    assert set(manifest) == {"smarkets", "betdaq"}
    assert all(row["adapter_registered"] is False for row in manifest.values())
    assert manifest["smarkets"]["venue"]["venue_type"] == "EXCHANGE"
    assert manifest["betdaq"]["capabilities"]["heartbeat"] is True


def test_n_venue_matching_retains_all_four_and_is_order_independent():
    markets = [_market("betfair"), _market("matchbook"), _market("smarkets"), _market("betdaq")]
    a = match_markets(markets)
    b = match_markets(list(reversed(markets)))
    assert len(a) == len(b) == 1
    assert {m.provider_id for m in a[0].markets} == {"betfair", "matchbook", "smarkets", "betdaq"}
    assert {m.provider_id for m in b[0].markets} == {"betfair", "matchbook", "smarkets", "betdaq"}
    assert a[0].canonical_event_id == b[0].canonical_event_id
    assert a[0].canonical_market_id == b[0].canonical_market_id


def test_strategy_can_choose_best_pair_from_four_venues():
    rows = {
        "Home": [
            Leg("Betfair delayed", "Home", 2.0, 100, venue_id="betfair", provider_id="betfair"),
            Leg("Matchbook", "Home", 1.9, 100, venue_id="matchbook", provider_id="matchbook"),
            Leg("Smarkets", "Home", 2.2, 100, venue_id="smarkets", provider_id="smarkets"),
            Leg("BETDAQ", "Home", 1.95, 100, venue_id="betdaq", provider_id="betdaq"),
        ],
        "Away": [
            Leg("Betfair delayed", "Away", 1.9, 100, venue_id="betfair", provider_id="betfair"),
            Leg("Matchbook", "Away", 2.0, 100, venue_id="matchbook", provider_id="matchbook"),
            Leg("Smarkets", "Away", 1.95, 100, venue_id="smarkets", provider_id="smarkets"),
            Leg("BETDAQ", "Away", 2.2, 100, venue_id="betdaq", provider_id="betdaq"),
        ],
    }
    chosen = best_strategy_legs(rows, require_cross_exchange=True)
    assert [(x.selection, x.resolved_venue_id) for x in chosen] == [("Home", "smarkets"), ("Away", "betdaq")]


def test_feed_provenance_is_independent_of_execution_mode_and_delayed_is_not_live_eligible():
    q = _market("betfair").quotes[0]
    assert q.feed_entitlement == "delayed"
    assert q.market_data_transport == "poll"
    # 0.9.3 corrects a provenance ambiguity: local receipt time is no longer
    # manufactured into provider source time.
    assert q.source_timestamp is None
    assert q.timestamp_quality == "LOCAL_RECEIPT"
    assert live_feed_eligible(FeedEntitlement.DELAYED) is False
    assert live_feed_eligible(FeedEntitlement.REPLAY) is False
    assert live_feed_eligible(FeedEntitlement.LIVE) is True


def test_generic_sim_provider_capital_does_not_redistribute_existing_wallets(tmp_path):
    api = API(tmp_path / "capital.sqlite3")
    cfg = api.db.get_setting("config", {})
    cfg["sim_provider_starting_balances"] = {
        "pre_match": {"smarkets": 0},
        "in_play": {"smarkets": 0},
        "racing": {"smarkets": 0},
    }
    api.db.set_setting("config", cfg)
    overview = api.sim_portfolio_budget_overview()
    pre = next(row for row in overview["rows"] if row["stream"] == "pre_match")
    assert pre["venues"]["betfair"]["equity"] == pytest.approx(250.0)
    assert pre["venues"]["matchbook"]["equity"] == pytest.approx(250.0)
    assert pre["venues"]["smarkets"]["equity"] == pytest.approx(0.0)
    assert overview["account_totals"]["smarkets"] == pytest.approx(0.0)


def test_replay_accepts_arbitrary_venue_balances(tmp_path):
    api = API(tmp_path / "replay.sqlite3")
    result = api.analytics_replay({
        "venue_balances": {"betfair": 200, "matchbook": 175, "smarkets": 125},
        "starting_capital": 500,
        "period": "all",
    })
    starting = result["result"]["venue_balances"]["starting"]
    assert starting == {"betfair": 200.0, "matchbook": 175.0, "smarkets": 125.0}
    assert result["result"]["exchange_balances"]["starting"] == starting


def test_live_persistence_is_separate_and_sim_journal_is_rejected(tmp_path):
    api = API(tmp_path / "live.sqlite3")
    counts = api.db.live_persistence_counts()
    assert counts and all(v == 0 for v in counts.values())
    sim_intent = new_order_intent(
        venue_id="betfair", provider_id="betfair", selection="Home", side="BACK",
        stake=1.0, target_odds=2.0, position_id="p1", leg_id="l1", mode="sim",
    )
    with pytest.raises(ValueError):
        api.db.record_live_order_intent(asdict(sim_intent))
    assert all(v == 0 for v in api.db.live_persistence_counts().values())


def test_live_order_journal_is_durable_before_submission_and_unknown_never_means_failed(tmp_path):
    api = API(tmp_path / "journal.sqlite3")
    intent = new_order_intent(
        venue_id="betfair", provider_id="betfair", selection="Home", side="BACK",
        stake=1.0, target_odds=2.0, position_id="p1", leg_id="l1", attempt_id=1, mode="live",
        canonical_event_id="evt:1", canonical_market_id="mkt:1", canonical_selection_id="sel:1",
    )
    assert intent.client_order_id and intent.created_at
    row = api.db.record_live_order_intent(asdict(intent))
    assert row["state"] == "NOT_SUBMITTED"
    # Duplicate persistence is idempotent rather than creating a second intent.
    api.db.record_live_order_intent(asdict(intent))
    assert api.db.live_persistence_counts()["live_order_attempts"] == 1
    api.db.mark_live_order_submission_attempted(intent.client_order_id)
    assert api.db.live_order_attempts(limit=1)[0]["state"] == "PENDING"
    api.db.mark_live_order_unknown(intent.client_order_id, "network timeout")
    assert api.db.live_order_attempts(limit=1)[0]["state"] == "UNKNOWN"
    assert api.db.unresolved_live_order_count() == 1
    api.db.reconcile_live_order_attempt(intent.client_order_id, state="ACCEPTED", external_order_id="EXT-1")
    assert api.db.live_order_attempts(limit=1)[0]["state"] == "ACCEPTED"
    assert api.db.unresolved_live_order_count() == 0


def test_research_reset_preserves_live_journal(tmp_path):
    api = API(tmp_path / "reset.sqlite3")
    intent = new_order_intent(
        venue_id="betfair", provider_id="betfair", selection="Home", side="BACK",
        stake=1.0, target_odds=2.0, position_id="p1", leg_id="l1", mode="live",
    )
    api.db.record_live_order_intent(asdict(intent))
    api.db.clear_research_history()
    assert api.db.live_persistence_counts()["live_order_attempts"] == 1


def test_live_account_view_and_preflight_never_fall_back_to_sim(tmp_path):
    api = API(tmp_path / "isolation.sqlite3")
    assert api.sim_account_adjust({"exchange": "betfair", "action": "add", "value": 321.0})["ok"]
    sim = api.account_overview({"mode": "sim"})
    live = api.account_overview({"mode": "live"})
    assert sim["accounts"]["betfair"]["equity"] > 0
    assert live["accounts"]["betfair"].get("equity") is None
    assert live["provider"] == "live_account_provider"
    gate = api.live_preflight({"stream": "pre_match"})
    assert gate["ok"] is True
    assert gate["eligible"] is False
    assert gate["global_live_unlocked"] is False
    assert gate["allow_new_positions"] is False
    assert gate["manage_existing_exposure"] is True


def test_provider_scoped_credentials_coexist_with_legacy_credentials(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.set("betfair_app_key", "delayed-key")
    store.set_provider_credentials("betfair", "live", {"app_key": "live-key", "certificate": "cert.pem"})
    store.set_provider_credentials("smarkets", "default", {"api_key": "smarkets-key"})
    assert store.get("betfair_app_key") == "delayed-key"
    assert store.provider_credentials("betfair", "live")["app_key"] == "live-key"
    assert store.provider_credentials("smarkets", "default") == {"api_key": "smarkets-key"}
    raw = json.loads((tmp_path / "secrets.json").read_text())
    assert raw["providers"]["betfair"]["live"]["app_key"] == "live-key"
    assert raw["providers"]["smarkets"]["default"]["api_key"] == "smarkets-key"


def test_runtime_registry_disables_provider_without_affecting_other_provider():
    runtime = default_provider_runtime_registry()
    assert set(runtime.enabled_provider_ids()) == {"betfair", "matchbook", "smarkets"}
    runtime.set_runtime_enabled("betfair", False)
    assert set(runtime.enabled_provider_ids()) == {"matchbook", "smarkets"}
    assert runtime.runtime_status("betfair").degraded_reason == "disabled"
    assert runtime.runtime_status("matchbook").enabled is True


def test_frontend_generic_provider_rendering_contracts():
    html = (ROOT / "frontend" / "index.html").read_text()
    assert 'id="headerSub">Enabled venues<' in html
    assert '<label>Venue<select id="activityExchange"' in html
    assert 'All venues</option>' in html
    assert 'id="racingMonVenue"' in html
    assert 'Single venue only' in html
    assert 'venue_balances:venueBalances' in html
    assert 'sim-budget-venue-input' in html
    assert 'orderedVenueIds0900' in html
    assert 'Venue Accounts' in html
    assert "a.exchange==='Matchbook'?0:10" not in html
