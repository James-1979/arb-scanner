import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from arbscanner.db import DB
from arbscanner.engine import simulate_equal_return
from arbscanner.lifecycle import event_phase
from arbscanner.models import Leg, Scenario
from arbscanner.monitor_timing import MonitorTimingObserver, evaluate_observation


def _legs():
    return [
        Leg("Matchbook", "Home", 2.72, 500, 2.0, event_id="mb-e", market_id="mb-m", selection_id="4"),
        Leg("Betfair delayed", "Draw", 3.75, 500, 2.0, event_id="bf-e", market_id="bf-m", selection_id="2"),
        Leg("Betfair delayed", "Away", 3.05, 500, 2.0, event_id="bf-e", market_id="bf-m", selection_id="3"),
    ]


def _states(matchbook_in_play=False, betfair_in_play=False):
    return {
        ("Matchbook", "mb-m"): {
            "ok": True,
            "status": "OPEN",
            "in_play": matchbook_in_play,
            "latency_ms": 10,
            "quotes": {"4": {"odds": 2.72, "liquidity": 500}},
        },
        ("Betfair delayed", "bf-m"): {
            "ok": True,
            "status": "OPEN",
            "in_play": betfair_in_play,
            "latency_ms": 12,
            "quotes": {
                "2": {"odds": 3.75, "liquidity": 500},
                "3": {"odds": 3.05, "liquidity": 500},
            },
        },
    }


def _evaluate(states, *, scheduled_start_passed=True):
    legs = _legs()
    sim = simulate_equal_return(legs, Scenario("monitor", 500, 100, 100))
    assert sim["executable"] is True
    return evaluate_observation(
        legs,
        sim,
        states,
        bankroll=500,
        max_bankroll_pct=100,
        max_event_exposure_pct=100,
        min_roi=0,
        min_profit=0,
        pre_match_only=True,
        scheduled_start_passed=scheduled_start_passed,
    )


def test_scheduled_start_does_not_override_explicit_exchange_pre_match_state():
    result = _evaluate(_states(False, False), scheduled_start_passed=True)
    assert result["still_executable"] is True
    assert result["failure_reason"] is None
    assert result["start_status"] == "PRE_MATCH_CONFIRMED"


def test_one_explicit_pre_match_flag_is_enough_when_other_venue_is_unknown():
    result = _evaluate(_states(False, None), scheduled_start_passed=True)
    assert result["still_executable"] is True
    assert result["failure_reason"] is None
    assert result["start_status"] == "PRE_MATCH_CONFIRMED"


def test_explicit_in_play_flag_rejects_pre_match_monitor_execution():
    result = _evaluate(_states(False, True), scheduled_start_passed=True)
    assert result["still_executable"] is False
    assert result["failure_reason"] == "BETFAIR_IN_PLAY"
    assert result["start_status"] == "IN_PLAY"


def test_passed_start_with_no_exchange_start_state_is_safely_rejected_as_unconfirmed():
    result = _evaluate(_states(None, None), scheduled_start_passed=True)
    assert result["still_executable"] is False
    assert result["failure_reason"] == "START_STATUS_UNCONFIRMED"
    assert result["start_status"] == "UNCONFIRMED"


def test_unknown_start_state_before_scheduled_start_can_still_be_evaluated():
    result = _evaluate(_states(None, None), scheduled_start_passed=False)
    assert result["still_executable"] is True
    assert result["failure_reason"] is None


def test_lifecycle_explicit_not_in_play_wins_over_passed_scheduled_time():
    now = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)
    past = "2026-08-10T10:30:00+00:00"
    phase = event_phase(past, "OPEN", False, now=now)
    assert phase["phase"] == "upcoming"
    assert phase["label"] == "Pre-match / delayed start"



class _FakeAdapter:
    def __init__(self, name: str):
        self.name = name

    async def fetch_market_state(self, event_id: str, market_id: str):
        if self.name == "Matchbook":
            quotes = {"4": {"odds": 2.72, "liquidity": 500}}
        else:
            quotes = {
                "2": {"odds": 3.75, "liquidity": 500},
                "3": {"odds": 3.05, "liquidity": 500},
            }
        return {
            "ok": True,
            "status": "OPEN",
            "in_play": False,
            "latency_ms": 1,
            "quotes": quotes,
        }


def test_observer_allows_delayed_start_when_fresh_exchange_state_is_explicitly_pre_match(tmp_path: Path):
    db = DB(tmp_path / "delayed-start.sqlite3")
    db.reset_monitor_wallets({"betfair": 250.0, "matchbook": 250.0})
    legs = _legs()
    sim = simulate_equal_return(legs, Scenario("monitor", 100, 100, 100))
    oid = db.add_opportunity(
        "evt", "Delayed Tennis", "2020-01-01T12:00:00+00:00", "Match Odds", 3, 3,
        [asdict(x) for x in legs], [], .99, "delayed-start",
    )
    observer = MonitorTimingObserver(db, checkpoints_ms=(1, 2, 3, 4))
    result = asyncio.run(observer.observe(
        opportunity_id=oid,
        original_legs=legs,
        original_simulation=sim,
        adapters=[_FakeAdapter("Matchbook"), _FakeAdapter("Betfair delayed")],
        event_start="2020-01-01T12:00:00+00:00",
        bankroll=100,
        max_bankroll_pct=100,
        max_event_exposure_pct=100,
        min_roi=0,
        min_profit=0,
        pre_match_only=True,
        reference_checkpoint_ms=2,
        execution_checkpoint_ms=3,
        hedge_checkpoint_ms=4,
        event_key="evt",
        market_name="Match Odds",
    ))
    assert result["reference_executable"] is True
    assert result["first_failure_reason"] is None
    assert all(x["start_status"] == "PRE_MATCH_CONFIRMED" for x in result["observations"])

def test_frontend_has_unconfirmed_start_reason_and_new_version():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "START_STATUS_UNCONFIRMED:'Start status unconfirmed'" in html
    assert "PoC 0.9.36" in html
