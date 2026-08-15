from __future__ import annotations

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-32768;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 captured_at TEXT NOT NULL,
 exchange TEXT NOT NULL,
 event_id TEXT NOT NULL,
 event_name TEXT NOT NULL,
 market_id TEXT,
 market_name TEXT NOT NULL,
 selection_id TEXT,
 selection TEXT NOT NULL,
 side TEXT NOT NULL,
 odds REAL NOT NULL,
 liquidity REAL NOT NULL,
 source_latency_ms INTEGER DEFAULT 0,
 feed_entitlement TEXT NOT NULL DEFAULT 'unknown',
 market_data_transport TEXT NOT NULL DEFAULT 'unknown',
 source_timestamp TEXT,
 source_state_version TEXT,
 commission_pct REAL DEFAULT 0,
 commission_source TEXT,
 market_type TEXT,
 strategy TEXT,
 sport TEXT,
 in_play INTEGER,
 market_status TEXT,
 section TEXT DEFAULT 'sports',
 trap_number INTEGER,
 canonical_selection_key TEXT,
 runner_status TEXT,
 raw_json TEXT
);
-- v0.8.34 bounded quote storage. Fast scans upsert one current row per
-- exchange/market/runner/side instead of appending millions of raw rows.
CREATE TABLE IF NOT EXISTS latest_snapshots (
 exchange TEXT NOT NULL,
 market_id TEXT NOT NULL,
 selection_id TEXT NOT NULL,
 side TEXT NOT NULL,
 captured_at TEXT NOT NULL,
 event_id TEXT NOT NULL,
 event_name TEXT NOT NULL,
 market_name TEXT NOT NULL,
 selection TEXT NOT NULL,
 odds REAL NOT NULL,
 liquidity REAL NOT NULL,
 source_latency_ms INTEGER DEFAULT 0,
 commission_pct REAL DEFAULT 0,
 commission_source TEXT,
 market_type TEXT,
 strategy TEXT,
 sport TEXT,
 in_play INTEGER,
 market_status TEXT,
 section TEXT DEFAULT 'sports',
 trap_number INTEGER,
 canonical_selection_key TEXT,
 runner_status TEXT,
 raw_json TEXT,
 PRIMARY KEY(exchange,market_id,selection_id,side)
);
CREATE INDEX IF NOT EXISTS idx_latest_snapshots_exchange_time ON latest_snapshots(exchange,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_latest_snapshots_event_time ON latest_snapshots(event_id,captured_at DESC);

-- Compact hourly observation counts replace long-term raw quote duplication.
CREATE TABLE IF NOT EXISTS snapshot_rollups (
 hour_utc TEXT NOT NULL,
 exchange TEXT NOT NULL,
 quote_observations INTEGER NOT NULL DEFAULT 0,
 batches INTEGER NOT NULL DEFAULT 0,
 last_captured_at TEXT,
 PRIMARY KEY(hour_utc,exchange)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_rollups_time ON snapshot_rollups(hour_utc DESC,exchange);

-- v0.8.36 compact Market Analysis hourly rollups. These retain hourly breadth/
-- signal counts without repeatedly grouping the large matched_markets history.
CREATE TABLE IF NOT EXISTS market_hourly_rollups (
 hour_utc TEXT NOT NULL,
 section TEXT NOT NULL DEFAULT 'sports',
 sport TEXT NOT NULL DEFAULT 'Unknown',
 market_name TEXT NOT NULL DEFAULT 'Unknown',
 in_play INTEGER NOT NULL DEFAULT 0,
 observations INTEGER NOT NULL DEFAULT 0,
 unique_markets INTEGER NOT NULL DEFAULT 0,
 net_positive INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(hour_utc,section,sport,market_name,in_play)
);
CREATE INDEX IF NOT EXISTS idx_market_hourly_rollups_time ON market_hourly_rollups(hour_utc DESC,section,sport,in_play);
CREATE TABLE IF NOT EXISTS market_hourly_seen (
 hour_utc TEXT NOT NULL,
 section TEXT NOT NULL DEFAULT 'sports',
 sport TEXT NOT NULL DEFAULT 'Unknown',
 market_name TEXT NOT NULL DEFAULT 'Unknown',
 in_play INTEGER NOT NULL DEFAULT 0,
 event_key TEXT NOT NULL,
 net_positive INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(hour_utc,section,sport,market_name,in_play,event_key)
);
CREATE INDEX IF NOT EXISTS idx_market_hourly_seen_time ON market_hourly_seen(hour_utc DESC);
CREATE TABLE IF NOT EXISTS market_hourly_rollup_state (
 hour_utc TEXT PRIMARY KEY,
 built_at TEXT NOT NULL
);

-- v0.8.42 compact financial heatmap facts. Historical hours are immutable
-- rollups; the current hour is refreshed from canonical position/opportunity rows.
CREATE TABLE IF NOT EXISTS market_financial_hourly_rollups (
 hour_utc TEXT NOT NULL,
 section TEXT NOT NULL DEFAULT 'sports',
 sport TEXT NOT NULL DEFAULT 'Unknown',
 market_name TEXT NOT NULL DEFAULT 'Unknown',
 in_play INTEGER NOT NULL DEFAULT 0,
 qualified INTEGER NOT NULL DEFAULT 0,
 executed INTEGER NOT NULL DEFAULT 0,
 deployed REAL NOT NULL DEFAULT 0,
 settled INTEGER NOT NULL DEFAULT 0,
 settled_deployed REAL NOT NULL DEFAULT 0,
 pnl REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(hour_utc,section,sport,market_name,in_play)
);
CREATE INDEX IF NOT EXISTS idx_market_financial_hourly_time ON market_financial_hourly_rollups(hour_utc DESC,section,sport,in_play);
CREATE TABLE IF NOT EXISTS market_financial_hourly_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL);

-- Exchange-native discovery identity. Unlike matched_markets this keeps markets
-- that never found a counterpart, including incomplete Betfair Greyhound catalogue rows.
CREATE TABLE IF NOT EXISTS exchange_market_discovery_hours (
 hour_utc TEXT NOT NULL,
 exchange_key TEXT NOT NULL,
 exchange_label TEXT NOT NULL,
 market_id TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'pre_match',
 event_id TEXT,
 event_name TEXT,
 market_name TEXT,
 canonical_market_key TEXT,
 sport TEXT NOT NULL DEFAULT 'Unknown',
 section TEXT NOT NULL DEFAULT 'sports',
 event_start TEXT,
 race_track TEXT,
 race_number INTEGER,
 source_quality TEXT,
 first_seen TEXT NOT NULL,
 last_seen TEXT NOT NULL,
 observations INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(hour_utc,exchange_key,market_id,phase)
);
CREATE INDEX IF NOT EXISTS idx_exchange_market_discovery_time ON exchange_market_discovery_hours(hour_utc DESC,exchange_key,sport,section,phase);
CREATE INDEX IF NOT EXISTS idx_exchange_market_discovery_market ON exchange_market_discovery_hours(exchange_key,market_id,hour_utc DESC);
CREATE TABLE IF NOT EXISTS exchange_market_discovery_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL,completeness TEXT NOT NULL DEFAULT 'historical');

-- Small maintenance state; legacy raw rows are pruned incrementally by the worker.
CREATE TABLE IF NOT EXISTS snapshot_storage_state (
 id INTEGER PRIMARY KEY CHECK(id=1),
 legacy_target_id INTEGER NOT NULL DEFAULT 0,
 legacy_rows_deleted INTEGER NOT NULL DEFAULT 0,
 last_prune_at TEXT,
 last_write_error TEXT,
 last_write_error_at TEXT
);
INSERT OR IGNORE INTO snapshot_storage_state(id) VALUES(1);
CREATE TABLE IF NOT EXISTS opportunities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 detected_at TEXT NOT NULL,
 event_key TEXT NOT NULL,
 event_name TEXT,
 event_start TEXT,
 market_name TEXT NOT NULL,
 edge_pct REAL NOT NULL,
 expected_roi_pct REAL NOT NULL,
 legs_json TEXT NOT NULL,
 source_markets_json TEXT,
 match_score REAL DEFAULT 0,
 signature TEXT,
 is_demo INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'paper',
 strategy TEXT DEFAULT '1x2',
 sport TEXT,
 section TEXT DEFAULT 'sports',
 race_track TEXT,
 race_number INTEGER,
 runner_count INTEGER,
 time_to_off_seconds INTEGER,
 in_play INTEGER,
 event_status TEXT,
 qualification_status TEXT DEFAULT 'qualified',
 qualification_reason TEXT,
 routing_diagnostics_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_opportunities_event_market ON opportunities(event_key,market_name);
CREATE TABLE IF NOT EXISTS scenario_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 opportunity_id INTEGER NOT NULL,
 scenario_name TEXT NOT NULL,
 bankroll REAL NOT NULL,
 deployed REAL NOT NULL,
 expected_profit REAL NOT NULL,
 expected_roi_pct REAL NOT NULL,
 limited_by TEXT,
 stakes_json TEXT,
 outcome_pnls_json TEXT,
 realized_pnl REAL,
 created_at TEXT NOT NULL,
 FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE TABLE IF NOT EXISTS settlements (
 opportunity_id INTEGER PRIMARY KEY,
 settled_at TEXT NOT NULL,
 outcome TEXT,
 simulated_pnl REAL,
 notes TEXT,
 FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE TABLE IF NOT EXISTS settlement_audits (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 opportunity_id INTEGER NOT NULL,
 observed_at TEXT NOT NULL,
 status TEXT NOT NULL,
 raw_provider_winner TEXT,
 provider_winner_id TEXT,
 canonical_winner TEXT,
 stored_selections_json TEXT,
 mapping_method TEXT,
 mapping_confidence REAL,
 winning_exchange TEXT,
 settlement_contributions_json TEXT,
 total_realized_pnl REAL,
 reconciliation_status TEXT,
 reconciliation_delta REAL,
 details_json TEXT,
 FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_settlement_audits_opportunity ON settlement_audits(opportunity_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_settlement_audits_status ON settlement_audits(status, observed_at DESC);
CREATE TABLE IF NOT EXISTS execution_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 opportunity_id INTEGER NOT NULL,
 mode TEXT NOT NULL,
 execution_type TEXT NOT NULL DEFAULT 'stress',
 started_at TEXT NOT NULL,
 finished_at TEXT,
 state TEXT NOT NULL,
 is_real INTEGER NOT NULL DEFAULT 0,
 deployed REAL DEFAULT 0,
 expected_profit REAL DEFAULT 0,
 captured_profit REAL,
 execution_leakage REAL,
 max_unhedged_exposure REAL DEFAULT 0,
 details_json TEXT,
 FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_runs_mode_time ON execution_runs(mode,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_runs_opportunity ON execution_runs(opportunity_id);
CREATE TABLE IF NOT EXISTS monitor_timing_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 opportunity_id INTEGER NOT NULL,
 started_at TEXT NOT NULL,
 finished_at TEXT,
 status TEXT NOT NULL DEFAULT 'RUNNING',
 initial_deployed REAL DEFAULT 0,
 initial_profit REAL DEFAULT 0,
 initial_roi_pct REAL DEFAULT 0,
 planned_stakes_json TEXT,
 reference_checkpoint_ms INTEGER DEFAULT 250,
 reference_profit REAL,
 reference_roi_pct REAL,
 reference_executable INTEGER,
 survived_through_ms INTEGER DEFAULT 0,
 first_failure_reason TEXT,
 research_only INTEGER NOT NULL DEFAULT 0,
 stream TEXT NOT NULL DEFAULT 'pre_match',
 FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_monitor_timing_runs_opportunity ON monitor_timing_runs(opportunity_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_monitor_timing_runs_time ON monitor_timing_runs(started_at DESC);
CREATE TABLE IF NOT EXISTS monitor_timing_observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 monitor_timing_run_id INTEGER NOT NULL,
 offset_ms INTEGER NOT NULL,
 elapsed_ms INTEGER NOT NULL,
 observed_at TEXT NOT NULL,
 fetch_latency_ms INTEGER DEFAULT 0,
 deployed REAL DEFAULT 0,
 expected_profit REAL DEFAULT 0,
 expected_roi_pct REAL DEFAULT 0,
 executable_fraction REAL DEFAULT 0,
 full_stake_available INTEGER DEFAULT 0,
 still_profitable INTEGER DEFAULT 0,
 still_executable INTEGER DEFAULT 0,
 failure_reason TEXT,
 quotes_json TEXT,
 venues_json TEXT,
 FOREIGN KEY(monitor_timing_run_id) REFERENCES monitor_timing_runs(id),
 UNIQUE(monitor_timing_run_id,offset_ms)
);
CREATE INDEX IF NOT EXISTS idx_monitor_timing_observations_run ON monitor_timing_observations(monitor_timing_run_id,offset_ms);
CREATE TABLE IF NOT EXISTS job_schedules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 mode TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1,
 recurrence TEXT NOT NULL DEFAULT 'once',
 timezone_name TEXT,
 first_run_at TEXT NOT NULL,
 next_run_at TEXT,
 last_run_at TEXT,
 duration_minutes INTEGER,
 strategy_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_schedules_next ON job_schedules(enabled,next_run_at);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 schedule_id INTEGER,
 name TEXT NOT NULL,
 mode TEXT NOT NULL,
 trigger_type TEXT NOT NULL DEFAULT 'manual',
 status TEXT NOT NULL DEFAULT 'scheduled',
 created_at TEXT NOT NULL,
 scheduled_start TEXT,
 scheduled_end TEXT,
 started_at TEXT,
 finished_at TEXT,
 stop_reason TEXT,
 strategy_json TEXT NOT NULL,
 FOREIGN KEY(schedule_id) REFERENCES job_schedules(id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_time ON jobs(status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule ON jobs(schedule_id,id DESC);
CREATE TABLE IF NOT EXISTS scan_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 job_id INTEGER,
 started_at TEXT NOT NULL,
 finished_at TEXT,
 markets_seen INTEGER DEFAULT 0,
 matches_seen INTEGER DEFAULT 0,
 opportunities_found INTEGER DEFAULT 0,
 status_json TEXT,
 error TEXT,
 processed_candidates INTEGER DEFAULT 0,
 positive_opportunities INTEGER DEFAULT 0,
 qualified_count INTEGER DEFAULT 0,
 executed_count INTEGER DEFAULT 0,
 duration_ms INTEGER DEFAULT 0,
 scan_kind TEXT DEFAULT 'legacy',
 stage_timings_json TEXT,
 cache_entries INTEGER DEFAULT 0,
 stale_rejections INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS market_cache (
 cache_key TEXT PRIMARY KEY,
 event_key TEXT NOT NULL,
 event_name TEXT NOT NULL,
 event_start TEXT,
 market_name TEXT NOT NULL,
 market_type TEXT,
 strategy TEXT NOT NULL,
 sport TEXT NOT NULL,
 section TEXT DEFAULT 'sports',
 race_track TEXT,
 race_number INTEGER,
 runner_count INTEGER,
 match_score REAL DEFAULT 0,
 source_markets_json TEXT NOT NULL,
 discovered_at TEXT NOT NULL,
 last_validated_at TEXT NOT NULL,
 last_price_refresh_at TEXT,
 refresh_interval_seconds INTEGER NOT NULL DEFAULT 10,
 active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_market_cache_active_start ON market_cache(active,event_start);
CREATE INDEX IF NOT EXISTS idx_market_cache_refresh ON market_cache(active,last_price_refresh_at);
CREATE TABLE IF NOT EXISTS matched_markets (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scan_id INTEGER NOT NULL,
 observed_at TEXT NOT NULL,
 event_key TEXT NOT NULL,
 event_name TEXT NOT NULL,
 event_start TEXT,
 market_name TEXT NOT NULL,
 match_score REAL DEFAULT 0,
 theoretical_edge_pct REAL,
 gross_roi_pct REAL,
 commission_impact_pct REAL,
 net_roi_pct REAL,
 diagnostic_deployed REAL,
 diagnostic_profit REAL,
 limited_by TEXT,
 status TEXT NOT NULL,
 reason TEXT,
 legs_json TEXT,
 source_markets_json TEXT,
 strategy TEXT DEFAULT '1x2',
 quality_score REAL,
 quality_band TEXT,
 reference_bankroll REAL,
 bankroll_roi_pct REAL,
 capital_used_pct REAL,
 sport TEXT,
 section TEXT DEFAULT 'sports',
 race_track TEXT,
 race_number INTEGER,
 runner_count INTEGER,
 time_to_off_seconds INTEGER,
 in_play INTEGER,
 event_status TEXT,
 FOREIGN KEY(scan_id) REFERENCES scan_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_matched_markets_scan ON matched_markets(scan_id);
CREATE INDEX IF NOT EXISTS idx_matched_markets_roi ON matched_markets(scan_id,net_roi_pct DESC);
-- v0.9.3 bounded matched-market current state and compact diagnostic history.
CREATE TABLE IF NOT EXISTS matched_market_latest (
 state_key TEXT PRIMARY KEY, scan_id INTEGER NOT NULL, observed_at TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 observation_count INTEGER NOT NULL DEFAULT 1, material_fingerprint TEXT NOT NULL, last_verbose_at TEXT,
 event_key TEXT NOT NULL, event_name TEXT NOT NULL, event_start TEXT, market_name TEXT NOT NULL, match_score REAL DEFAULT 0,
 theoretical_edge_pct REAL, gross_roi_pct REAL, commission_impact_pct REAL, net_roi_pct REAL, diagnostic_deployed REAL, diagnostic_profit REAL, limited_by TEXT,
 status TEXT NOT NULL, reason TEXT, legs_json TEXT, source_markets_json TEXT, strategy TEXT DEFAULT '1x2', quality_score REAL, quality_band TEXT,
 reference_bankroll REAL, bankroll_roi_pct REAL, capital_used_pct REAL, sport TEXT, section TEXT DEFAULT 'sports', race_track TEXT, race_number INTEGER,
 runner_count INTEGER, time_to_off_seconds INTEGER, in_play INTEGER, event_status TEXT, max_executable_stake REAL, limiting_provider TEXT,
 limiting_selection TEXT, limiting_side TEXT, liquidity_capable INTEGER, liquidity_rejection_reason TEXT, depth_at_qualification_json TEXT,
 quote_age_at_qualification_ms INTEGER, book_revision TEXT, quote_oldest_age_ms INTEGER, quote_newest_age_ms INTEGER, quote_receipt_spread_ms INTEGER,
 source_timestamp_spread_ms INTEGER, timestamp_quality TEXT
);
CREATE INDEX IF NOT EXISTS idx_matched_market_latest_scan ON matched_market_latest(scan_id);
CREATE INDEX IF NOT EXISTS idx_matched_market_latest_time ON matched_market_latest(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_matched_market_latest_analysis ON matched_market_latest(section,sport,market_name,in_play,last_seen DESC);
CREATE TABLE IF NOT EXISTS matched_market_reason_hourly_rollups (
 hour_utc TEXT NOT NULL, section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown', market_name TEXT NOT NULL DEFAULT 'Unknown',
 in_play INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, reason_sample TEXT, observations INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(hour_utc,section,sport,market_name,in_play,status)
);
CREATE INDEX IF NOT EXISTS idx_matched_market_reason_hourly_time ON matched_market_reason_hourly_rollups(hour_utc DESC,section,sport,in_play);
CREATE TABLE IF NOT EXISTS scan_qualification_breakdown (
 scan_id INTEGER NOT NULL, status TEXT NOT NULL, total_count INTEGER NOT NULL DEFAULT 0, positive_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(scan_id,status)
);
CREATE INDEX IF NOT EXISTS idx_scan_qualification_breakdown_scan ON scan_qualification_breakdown(scan_id);
CREATE TABLE IF NOT EXISTS racing_funnel_hourly_rollups (
 hour_utc TEXT NOT NULL, sport TEXT NOT NULL DEFAULT 'Greyhounds', observations INTEGER NOT NULL DEFAULT 0,
 complete_books INTEGER NOT NULL DEFAULT 0, theoretical_positive INTEGER NOT NULL DEFAULT 0, post_commission_positive INTEGER NOT NULL DEFAULT 0,
 liquidity_capable INTEGER NOT NULL DEFAULT 0, qualified INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(hour_utc,sport)
);
CREATE INDEX IF NOT EXISTS idx_racing_funnel_hourly_time ON racing_funnel_hourly_rollups(hour_utc DESC,sport);
CREATE TABLE IF NOT EXISTS matched_market_history_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS matched_market_storage_state (
 id INTEGER PRIMARY KEY CHECK(id=1), rows_deleted INTEGER NOT NULL DEFAULT 0, last_prune_at TEXT, prune_safe_through TEXT,
 last_error TEXT, last_error_at TEXT
);
INSERT OR IGNORE INTO matched_market_storage_state(id) VALUES(1);
-- v0.9.8 isolated LIVE-context decision evidence. These tables contain
-- simulated decisions derived from provider observations only; they are not
-- SIM opportunity/economic records and are not LIVE account/order records.
CREATE TABLE IF NOT EXISTS live_decision_latest (
 state_key TEXT PRIMARY KEY, decision_id TEXT NOT NULL, canonical_event_id TEXT, canonical_market_id TEXT NOT NULL,
 book_revision TEXT NOT NULL, strategy TEXT NOT NULL, domain TEXT NOT NULL, section TEXT NOT NULL, sport TEXT NOT NULL,
 market_type TEXT, event_name TEXT, market_name TEXT NOT NULL, in_play INTEGER NOT NULL DEFAULT 0,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, observation_count INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL, evidence_quality TEXT NOT NULL, reason_code TEXT, reason TEXT,
 gross_edge_pct REAL, net_roi_pct REAL, expected_simulated_profit REAL, requested_stake REAL, max_executable_stake REAL,
 simulated_stake REAL, simulated_filled_stake REAL, oldest_quote_age_ms INTEGER, receipt_spread_ms INTEGER,
 source_time_spread_ms INTEGER, decision_compute_ms REAL, provider_pair TEXT, limiting_provider TEXT, limiting_selection TEXT,
 limiting_side TEXT, application_mode TEXT NOT NULL DEFAULT 'live', decision_type TEXT NOT NULL DEFAULT 'simulated',
 legs_json TEXT NOT NULL, simulation_json TEXT, qualification_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_decision_latest_time ON live_decision_latest(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_live_decision_latest_market ON live_decision_latest(canonical_market_id,strategy);
CREATE TABLE IF NOT EXISTS live_decision_events (
 decision_id TEXT PRIMARY KEY, state_key TEXT NOT NULL, created_at TEXT NOT NULL, canonical_event_id TEXT, canonical_market_id TEXT NOT NULL,
 book_revision TEXT NOT NULL, strategy TEXT NOT NULL, domain TEXT NOT NULL, section TEXT NOT NULL, sport TEXT NOT NULL,
 market_type TEXT, event_name TEXT, market_name TEXT NOT NULL, in_play INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL, evidence_quality TEXT NOT NULL, reason_code TEXT, reason TEXT, gross_edge_pct REAL, net_roi_pct REAL,
 expected_simulated_profit REAL, requested_stake REAL, max_executable_stake REAL, simulated_stake REAL, simulated_filled_stake REAL,
 oldest_quote_age_ms INTEGER, receipt_spread_ms INTEGER, source_time_spread_ms INTEGER, decision_compute_ms REAL,
 provider_pair TEXT, limiting_provider TEXT, limiting_selection TEXT, limiting_side TEXT,
 application_mode TEXT NOT NULL DEFAULT 'live', decision_type TEXT NOT NULL DEFAULT 'simulated',
 legs_json TEXT NOT NULL, simulation_json TEXT, qualification_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_decision_events_time ON live_decision_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_decision_events_analysis ON live_decision_events(domain,sport,market_type,evidence_quality,created_at DESC);
CREATE TABLE IF NOT EXISTS live_decision_hourly_rollups (
 hour_utc TEXT NOT NULL, domain TEXT NOT NULL, sport TEXT NOT NULL, market_type TEXT NOT NULL, provider_pair TEXT NOT NULL,
 evidence_quality TEXT NOT NULL, reason_code TEXT NOT NULL, observed INTEGER NOT NULL DEFAULT 0, positive INTEGER NOT NULL DEFAULT 0,
 liquidity_capable INTEGER NOT NULL DEFAULT 0, qualified INTEGER NOT NULL DEFAULT 0, simulated_attempts INTEGER NOT NULL DEFAULT 0,
 simulated_fills INTEGER NOT NULL DEFAULT 0, simulated_misses INTEGER NOT NULL DEFAULT 0, execution_grade INTEGER NOT NULL DEFAULT 0,
 expected_profit_sum REAL NOT NULL DEFAULT 0, executable_stake_sum REAL NOT NULL DEFAULT 0, executable_stake_samples INTEGER NOT NULL DEFAULT 0,
 decision_ms_sum REAL NOT NULL DEFAULT 0, decision_ms_samples INTEGER NOT NULL DEFAULT 0, max_decision_ms REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(hour_utc,domain,sport,market_type,provider_pair,evidence_quality,reason_code)
);
CREATE INDEX IF NOT EXISTS idx_live_decision_hourly_time ON live_decision_hourly_rollups(hour_utc DESC,domain,sport,evidence_quality);
CREATE TABLE IF NOT EXISTS opportunity_tracks (
 track_key TEXT PRIMARY KEY,
 event_key TEXT NOT NULL,
 event_name TEXT NOT NULL,
 market_name TEXT NOT NULL,
 strategy TEXT NOT NULL,
 first_seen TEXT NOT NULL,
 last_seen TEXT NOT NULL,
 closed_at TEXT,
 scan_count INTEGER NOT NULL DEFAULT 1,
 current_quality_score REAL DEFAULT 0,
 current_quality_band TEXT,
 peak_quality_score REAL DEFAULT 0,
 peak_quality_band TEXT,
 peak_roi_pct REAL DEFAULT 0,
 peak_bankroll_roi_pct REAL DEFAULT 0,
 peak_deployed REAL DEFAULT 0,
 peak_profit REAL DEFAULT 0,
 reference_bankroll REAL DEFAULT 500,
 last_status TEXT,
 last_reason TEXT,
 sport TEXT
);
CREATE INDEX IF NOT EXISTS idx_opportunity_tracks_last_seen ON opportunity_tracks(last_seen);
CREATE TABLE IF NOT EXISTS track_observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 track_key TEXT NOT NULL,
 scan_id INTEGER NOT NULL,
 observed_at TEXT NOT NULL,
 net_roi_pct REAL,
 bankroll_roi_pct REAL,
 deployed REAL,
 expected_profit REAL,
 quality_score REAL,
 quality_band TEXT,
 status TEXT,
 FOREIGN KEY(track_key) REFERENCES opportunity_tracks(track_key),
 FOREIGN KEY(scan_id) REFERENCES scan_runs(id)
);
CREATE TABLE IF NOT EXISTS alert_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 track_key TEXT NOT NULL,
 quality_band TEXT NOT NULL,
 quality_score REAL NOT NULL,
 sent_at TEXT NOT NULL,
 UNIQUE(track_key, quality_band)
);
CREATE TABLE IF NOT EXISTS alert_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 track_key TEXT,
 quality_band TEXT,
 quality_score REAL DEFAULT 0,
 attempted_at TEXT NOT NULL,
 success INTEGER NOT NULL DEFAULT 0,
 reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_attempts_time ON alert_attempts(attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_attempts_track ON alert_attempts(track_key,quality_band,attempted_at DESC);
CREATE TABLE IF NOT EXISTS monitor_wallets (
 exchange TEXT PRIMARY KEY,
 opening_balance REAL NOT NULL DEFAULT 0,
 available_balance REAL NOT NULL DEFAULT 0,
 reserved_balance REAL NOT NULL DEFAULT 0,
 realized_pnl REAL NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monitor_stream_wallets (
 stream TEXT NOT NULL,
 exchange TEXT NOT NULL,
 opening_balance REAL NOT NULL DEFAULT 0,
 available_balance REAL NOT NULL DEFAULT 0,
 reserved_balance REAL NOT NULL DEFAULT 0,
 realized_pnl REAL NOT NULL DEFAULT 0,
 funding_adjustment REAL NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(stream,exchange)
);
CREATE TABLE IF NOT EXISTS monitor_positions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 opportunity_id INTEGER NOT NULL UNIQUE,
 execution_run_id INTEGER,
 event_key TEXT,
 market_name TEXT,
 opened_at TEXT NOT NULL,
 settled_at TEXT,
 status TEXT NOT NULL DEFAULT 'OPEN',
 deployed REAL NOT NULL DEFAULT 0,
 expected_profit REAL NOT NULL DEFAULT 0,
 stakes_by_exchange_json TEXT NOT NULL,
 outcome_exchange_pnls_json TEXT NOT NULL,
 simulation_json TEXT,
 stream TEXT NOT NULL DEFAULT 'pre_match',
 currency TEXT NOT NULL DEFAULT 'GBP',
 outcome TEXT,
 realized_pnl REAL,
 realized_by_exchange_json TEXT,
 FOREIGN KEY(opportunity_id) REFERENCES opportunities(id),
 FOREIGN KEY(execution_run_id) REFERENCES execution_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_monitor_positions_status ON monitor_positions(status,opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitor_positions_market ON monitor_positions(event_key,market_name,status);

-- v0.8.30 canonical account/balance audit. MONITOR rows are snapshots of the
-- virtual exchange-account provider. LIVE rows are read-only exchange API
-- observations and never imply order-placement permission.
CREATE TABLE IF NOT EXISTS account_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mode TEXT NOT NULL,
 exchange TEXT NOT NULL,
 stream TEXT,
 currency TEXT NOT NULL,
 source TEXT NOT NULL,
 available_balance REAL NOT NULL DEFAULT 0,
 reserved_balance REAL NOT NULL DEFAULT 0,
 exposure REAL NOT NULL DEFAULT 0,
 equity REAL NOT NULL DEFAULT 0,
 realized_pnl REAL NOT NULL DEFAULT 0,
 freshness TEXT NOT NULL DEFAULT 'CURRENT',
 captured_at TEXT NOT NULL,
 context TEXT,
 metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_snapshots_lookup ON account_snapshots(mode,exchange,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_account_snapshots_stream ON account_snapshots(mode,stream,exchange,captured_at DESC);

CREATE TABLE IF NOT EXISTS balance_reconciliations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mode TEXT NOT NULL,
 exchange TEXT,
 stream TEXT,
 status TEXT NOT NULL,
 expected REAL,
 observed REAL,
 delta REAL,
 tolerance REAL NOT NULL DEFAULT 0.01,
 checked_at TEXT NOT NULL,
 details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_balance_reconciliations_lookup ON balance_reconciliations(mode,exchange,checked_at DESC);

-- v0.8.34 auditable SIM account funding. Adjustments change current virtual
-- balances without rewriting opening balances or historical P&L.
CREATE TABLE IF NOT EXISTS sim_account_adjustments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 exchange TEXT NOT NULL,
 action TEXT NOT NULL,
 amount REAL NOT NULL,
 previous_equity REAL NOT NULL,
 resulting_equity REAL NOT NULL,
 currency TEXT NOT NULL,
 reason TEXT,
 created_at TEXT NOT NULL,
 metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_account_adjustments_time ON sim_account_adjustments(exchange,created_at DESC);


-- v0.9.14 engine framework. Transactional metadata and decision provenance stay
-- in SQLite; high-volume market evidence remains in the existing archive layer.
CREATE TABLE IF NOT EXISTS engine_instances (
 engine_instance_id TEXT PRIMARY KEY,
 engine_type TEXT NOT NULL,
 engine_version TEXT NOT NULL,
 section TEXT NOT NULL DEFAULT 'all',
 sport TEXT NOT NULL DEFAULT 'all',
 competition TEXT NOT NULL DEFAULT 'all',
 market_type TEXT NOT NULL DEFAULT 'all',
 requested_lifecycle TEXT NOT NULL DEFAULT 'DISABLED',
 effective_lifecycle TEXT NOT NULL DEFAULT 'DISABLED',
 effective_reason TEXT NOT NULL DEFAULT 'REQUESTED_DISABLED',
 active_config_version INTEGER,
 health TEXT NOT NULL DEFAULT 'HEALTHY',
 last_evidence_at TEXT,
 last_evaluation_at TEXT,
 events_processed INTEGER NOT NULL DEFAULT 0,
 decisions_generated INTEGER NOT NULL DEFAULT 0,
 errors INTEGER NOT NULL DEFAULT 0,
 processing_latency_ms REAL NOT NULL DEFAULT 0,
 description TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '',
 package_source TEXT NOT NULL DEFAULT 'builtin',
 package_sha256 TEXT,
 package_author TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engine_instances_route ON engine_instances(section,sport,market_type,effective_lifecycle);
CREATE TABLE IF NOT EXISTS engine_configs (
 engine_instance_id TEXT NOT NULL,
 config_version INTEGER NOT NULL,
 config_hash TEXT NOT NULL,
 config_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 activated_at TEXT,
 derived_from_version INTEGER,
 PRIMARY KEY(engine_instance_id,config_version),
 UNIQUE(engine_instance_id,config_hash),
 FOREIGN KEY(engine_instance_id) REFERENCES engine_instances(engine_instance_id)
);
CREATE INDEX IF NOT EXISTS idx_engine_configs_active ON engine_configs(engine_instance_id,activated_at DESC,config_version DESC);
CREATE TABLE IF NOT EXISTS engine_decisions (
 decision_id TEXT PRIMARY KEY,
 economic_intent_key TEXT NOT NULL,
 created_at TEXT NOT NULL,
 engine_instance_id TEXT NOT NULL,
 engine_type TEXT NOT NULL,
 engine_version TEXT NOT NULL,
 config_version INTEGER NOT NULL,
 config_hash TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 feed_generation TEXT NOT NULL,
 section TEXT NOT NULL,
 sport TEXT NOT NULL,
 event_name TEXT,
 market_name TEXT,
 mode TEXT NOT NULL,
 requested_lifecycle TEXT NOT NULL,
 effective_lifecycle TEXT NOT NULL,
 expected_edge REAL NOT NULL DEFAULT 0,
 expected_profit REAL NOT NULL DEFAULT 0,
 requested_capital REAL NOT NULL DEFAULT 0,
 intent_json TEXT NOT NULL,
 evaluation_latency_ms REAL NOT NULL DEFAULT 0,
 central_validation TEXT NOT NULL DEFAULT 'NOT_SUBMITTED'
);
CREATE INDEX IF NOT EXISTS idx_engine_decisions_instance_time ON engine_decisions(engine_instance_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_engine_decisions_market ON engine_decisions(market_snapshot_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_engine_decisions_economic ON engine_decisions(economic_intent_key,created_at DESC);
CREATE TABLE IF NOT EXISTS engine_errors (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 engine_instance_id TEXT NOT NULL,
 market_snapshot_id TEXT,
 error_type TEXT NOT NULL,
 message TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engine_errors_instance_time ON engine_errors(engine_instance_id,created_at DESC);
CREATE TABLE IF NOT EXISTS engine_sim_results (
 decision_id TEXT PRIMARY KEY,
 engine_instance_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 deployed REAL NOT NULL DEFAULT 0,
 expected_profit REAL NOT NULL DEFAULT 0,
 expected_roi_pct REAL NOT NULL DEFAULT 0,
 simulation_level TEXT NOT NULL DEFAULT 'DECISION_SIM'
);
CREATE INDEX IF NOT EXISTS idx_engine_sim_results_instance_time ON engine_sim_results(engine_instance_id,created_at DESC);
CREATE TABLE IF NOT EXISTS engine_monitor_timing_results (
 decision_id TEXT PRIMARY KEY,
 engine_instance_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'WOULD_HAVE_PLACED',
 requested_capital REAL NOT NULL DEFAULT 0,
 expected_profit REAL NOT NULL DEFAULT 0,
 central_validation TEXT NOT NULL DEFAULT 'LIVE_EXECUTION_LOCKED'
);
CREATE INDEX IF NOT EXISTS idx_engine_monitor_timing_results_instance_time ON engine_monitor_timing_results(engine_instance_id,created_at DESC);
CREATE TABLE IF NOT EXISTS engine_scenario_runs (
 run_id TEXT PRIMARY KEY,
 scenario_id TEXT NOT NULL,
 scenario_version INTEGER NOT NULL DEFAULT 1,
 engine_instance_id TEXT NOT NULL,
 engine_version TEXT NOT NULL,
 config_version INTEGER NOT NULL,
 config_hash TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 run_at TEXT NOT NULL,
 simulation_level TEXT NOT NULL,
 decision_json TEXT,
 input_observed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_engine_scenario_runs_engine_time ON engine_scenario_runs(engine_instance_id,run_at DESC);

"""
