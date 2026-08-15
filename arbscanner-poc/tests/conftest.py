"""Current-suite test policy.

Historical tests remain in the repository because they document earlier UI and operating-mode
contracts.  Contracts that were deliberately superseded are retired here rather than being
left as unexplained failures.  Functional behaviour is covered by current-version tests.
"""
from __future__ import annotations

import pytest


RETIRED_CONTRACTS = {
    "tests/test_v063_recent_opportunity_ui.py::test_recent_opportunities_open_drawer_and_show_sport_badge": "Exact v0.6 compact-card CSS contract was superseded by the current Dashboard card/list layout.",
    "tests/test_v065_results_analytics.py::test_frontend_contains_activity_analytics_and_replay_controls": "Old Analytics heading/tab structure was superseded by the sidebar Analytics information architecture.",
    "tests/test_v069_compact_dashboard.py::test_recent_opportunities_use_compact_rows": "Exact legacy recent-card class contract was superseded by the current Dashboard layout.",
    "tests/test_v070_operating_modes.py::test_scanner_screen_exposes_always_on_watch_optional_monitor_timing_and_locked_live": "WATCH/MONITOR_TIMING controls were intentionally collapsed into MONITOR; LIVE remains locked and is tested separately.",
    "tests/test_v0711_monitor_execution.py::test_frontend_calls_strategy_page_replay_and_version_is_current": "Old Strategy Replay page markup was superseded by Analytics > Replay and Analytics > Scenarios.",
    "tests/test_v0711_monitor_execution.py::test_opportunity_drawer_exposes_modeled_monitor_execution_summary": "Legacy drawer wording/layout was superseded by Position/Leg and Execution Detail views.",
    "tests/test_v0712_execution_diagnostics.py::test_frontend_labels_missed_value_and_reason_professionally": "Legacy drawer labels were superseded by the current Execution Analysis profitability/forensic presentation.",
    "tests/test_v0715_dashboard_period_totals.py::test_dashboard_has_period_scanner_totals": "Legacy Dashboard period-total controls were superseded by the current Today activity pipeline and Analytics views.",
    "tests/test_v0717_fast_scanner_metrics.py::test_frontend_exposes_split_scanner_loops_and_cadence_settings": "Legacy Dashboard scanner-metric IDs were superseded by current feed/process cards; split discovery/price behaviour remains actively tested.",
    "tests/test_v0718_inplay_research.py::test_inplay_positive_candidate_is_monitor_research_only_and_never_executes": "In-play Monitor simulation is intentionally supported now; only LIVE execution remains locked.",
    "tests/test_v0718_inplay_research.py::test_frontend_uses_active_bets_and_inplay_research_warning": "Active Bets became Active Positions and in-play moved from research-only to Monitor simulation.",
    "tests/test_v072_lifecycle_filters.py::test_frontend_has_final_lifecycle_and_shared_analytics_filters": "Legacy Activity page controls were superseded by page-specific Analytics filter accordions.",
    "tests/test_v073_operator_workflow.py::test_primary_navigation_is_scanner_activity_analytics_settings": "Primary navigation was deliberately replaced by Dashboard/Analytics/Markets/Admin structure.",
    "tests/test_v073_operator_workflow.py::test_scanner_screen_is_rules_then_optional_monitor_timing_activation": "Legacy Scanner/MONITOR_TIMING workflow was superseded by Sports Monitor + Sports Config + MONITOR.",
    "tests/test_v076_operational_views.py::test_primary_navigation_matches_operational_views": "Old Monitor/Results/Executions/Bankroll Replay navigation was superseded by the current IA.",
    "tests/test_v076_operational_views.py::test_dashboard_overview_tracks_unsettled_execution_capital": "Execution-run-only capital is no longer treated as an open position; current tests use monitor_positions as the source of truth.",
    "tests/test_v076_operational_views.py::test_bankroll_replay_compares_potential_monitor_timing_and_actual": "Legacy bankroll replay MONITOR_TIMING/ACTUAL comparison was superseded by current Scenarios and Performance evidence models.",
    "tests/test_v077_monitor_timing_measurement.py::test_replay_exact_datetime_and_monitor_timing_use_same_scenario_start": "Legacy MONITOR_TIMING evidence comparison was superseded by MONITOR and the current Scenarios model.",
    "tests/test_v077_monitor_timing_measurement.py::test_frontend_has_professional_help_and_exact_replay_datetime": "Legacy MONITOR_TIMING help wording was removed when WATCH/MONITOR_TIMING collapsed into MONITOR.",
    "tests/test_v078_monitor_balances_strategy_replay.py::test_frontend_uses_monitor_actual_strategy_replay_and_balance_controls": "Strategy Replay was deliberately split into Replay (factual) and Scenarios (modelled).",
    "tests/test_v079_execution_performance.py::test_frontend_integrates_results_into_executions_and_fixes_drawer": "Results was intentionally restored as a dedicated settled-position ledger in v0.8.15.",
    "tests/test_v0951_scenarios_routing_integrity.py::test_actual_performance_comparator_is_engine_and_routing_aware": "The v0.9.51 comparator string contract was superseded by the v0.9.54 one-page Scenarios console and its direct replay routing model.",
    "tests/test_v0951_scenarios_routing_integrity.py::test_scenario_period_engine_racing_and_live_isolation_controls_are_wired": "The v0.9.51 Scenario select-based UI was superseded by the v0.9.54 multi-select Scenarios console.",
    "tests/test_v0951_scenarios_routing_integrity.py::test_scenario_transactions_expose_engine_provenance_and_engine_model_handoff": "The v0.9.51 transaction/model handoff panel was intentionally removed by the v0.9.54 single-page economic Scenarios console.",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        reason = RETIRED_CONTRACTS.get(item.nodeid)
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"retired contract: {reason}"))
