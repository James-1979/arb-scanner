from pathlib import Path
import sqlite3

from arbscanner.db import DB


def test_upgrade_from_pre_0717_scan_runs_schema(tmp_path: Path):
    path = tmp_path / 'arbscanner.sqlite3'
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE scan_runs (
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
            duration_ms INTEGER DEFAULT 0
        );
    ''')
    conn.commit(); conn.close()

    db = DB(path)
    cols = {r[1] for r in db.conn.execute('PRAGMA table_info(scan_runs)')}
    assert {'scan_kind','stage_timings_json','cache_entries','stale_rejections'} <= cols
    indexes = {r[1] for r in db.conn.execute("PRAGMA index_list('scan_runs')")}
    assert 'idx_scan_runs_kind_time' in indexes
    db.conn.close()
