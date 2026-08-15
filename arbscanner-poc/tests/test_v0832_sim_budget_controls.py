from pathlib import Path

import pytest

from arbscanner import __version__
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0832_account_editor_and_budget_ui_contract():
    assert __version__ == "0.9.36"
    for token in (
        'id="simAccountModal"',
        'openSimAccountEditor',
        'submitSimAccountEditor',
        'id="simBudgetEditor"',
        'SIM market budgets & hedge reserve',
        'applySimBudgets',
        'sim_portfolio_budget_update',
        "['pre_match','in_play','racing']",
    ):
        assert token in HTML
    # Account-card actions must no longer depend on native prompt/confirm UI.
    section = HTML[HTML.index('function simAccountAction'):HTML.index('function renderSimBudgetEditor', HTML.index('function simAccountAction'))]
    assert 'prompt(' not in section
    assert 'confirm(' not in section


def test_sim_market_budgets_reallocate_without_changing_exchange_equity(tmp_path):
    api = API(tmp_path / "budgets.sqlite3")
    before = api.account_overview({"mode": "sim"})
    assert before["accounts"]["betfair"]["equity"] == 750.0
    assert before["accounts"]["matchbook"]["equity"] == 750.0

    result = api.sim_portfolio_budget_update({
        "targets": {
            "pre_match": {"betfair": 300.0, "matchbook": 100.0},
            "in_play": {"betfair": 200.0, "matchbook": 400.0},
            "racing": {"betfair": 250.0, "matchbook": 250.0},
        },
        "hedge_reserve_amounts": {"pre_match": 40.0, "in_play": 60.0, "racing": 50.0},
        "reason": "test allocation",
    })
    assert result["ok"] is True

    after = api.account_overview({"mode": "sim"})
    assert after["accounts"]["betfair"]["equity"] == 750.0
    assert after["accounts"]["matchbook"]["equity"] == 750.0
    bf = {x["stream"]: x["equity"] for x in after["accounts"]["betfair"]["allocations"]}
    mb = {x["stream"]: x["equity"] for x in after["accounts"]["matchbook"]["allocations"]}
    assert bf == {"pre_match": 300.0, "in_play": 200.0, "racing": 250.0}
    assert mb == {"pre_match": 100.0, "in_play": 400.0, "racing": 250.0}
    assert after["reconciliation"]["status"] == "RECONCILED"

    overview = api.sim_portfolio_budget_overview({})
    rows = {x["stream"]: x for x in overview["rows"]}
    assert rows["pre_match"]["hedge_reserve_amount"] == pytest.approx(40.0, abs=0.01)
    assert rows["in_play"]["hedge_reserve_amount"] == pytest.approx(60.0, abs=0.01)
    assert rows["racing"]["hedge_reserve_amount"] == pytest.approx(50.0, abs=0.01)


def test_sim_budget_cannot_move_reserved_open_position_capital(tmp_path):
    api = API(tmp_path / "reserved_budget.sqlite3")
    # Reserve most of the pre-match Betfair allocation.
    with api.db.lock:
        api.db.conn.execute(
            "UPDATE monitor_stream_wallets SET available_balance=50,reserved_balance=200 WHERE stream='pre_match' AND exchange='betfair'"
        )
        # Keep total Betfair account equity unchanged by moving the removed £0? The
        # row still totals £250, so the canonical account remains £750.
        api.db.conn.commit()

    result = api.sim_portfolio_budget_update({
        "targets": {
            "pre_match": {"betfair": 150.0, "matchbook": 250.0},
            "in_play": {"betfair": 350.0, "matchbook": 250.0},
            "racing": {"betfair": 250.0, "matchbook": 250.0},
        },
        "hedge_reserve_amounts": {"pre_match": 20.0, "in_play": 20.0, "racing": 20.0},
    })
    assert result["ok"] is False
    assert "reserved" in result["message"].lower()
    state = api.account_overview({"mode": "sim"})["accounts"]["betfair"]
    pre = next(x for x in state["allocations"] if x["stream"] == "pre_match")
    assert pre["reserved"] == 200.0
    assert pre["equity"] == 250.0


def test_sim_topup_preserves_current_market_allocation_proportions(tmp_path):
    api = API(tmp_path / "topup.sqlite3")
    result = api.sim_portfolio_budget_update({
        "targets": {
            "pre_match": {"betfair": 300.0, "matchbook": 250.0},
            "in_play": {"betfair": 200.0, "matchbook": 250.0},
            "racing": {"betfair": 250.0, "matchbook": 250.0},
        },
        "hedge_reserve_amounts": {"pre_match": 20.0, "in_play": 20.0, "racing": 20.0},
    })
    assert result["ok"] is True
    topup = api.sim_account_adjust({"exchange": "betfair", "action": "add", "value": 75.0})
    assert topup["ok"] is True
    state = api.account_overview({"mode": "sim"})["accounts"]["betfair"]
    alloc = {x["stream"]: x["equity"] for x in state["allocations"]}
    assert alloc["pre_match"] == pytest.approx(330.0, abs=0.001)
    assert alloc["in_play"] == pytest.approx(220.0, abs=0.001)
    assert alloc["racing"] == pytest.approx(275.0, abs=0.001)
    assert sum(alloc.values()) == pytest.approx(825.0, abs=0.001)
