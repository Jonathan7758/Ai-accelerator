-- /opt/accelerator/sql/002_run_log.sql
-- Phase 1 Step 2: L2 自身运行历史

CREATE TABLE l2_run_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind TEXT NOT NULL,           -- 'librarian' | 'watcher' | 'analyst' | ...
    status TEXT NOT NULL          -- 'running' | 'ok' | 'failed' | 'partial'
        CHECK (status IN ('running','ok','failed','partial')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_seconds REAL,
    summary JSONB DEFAULT '{}'::jsonb,
    error_message TEXT
);

CREATE INDEX idx_l2_run_log_kind_started ON l2_run_log(kind, started_at DESC);
CREATE INDEX idx_l2_run_log_status_started ON l2_run_log(status, started_at DESC)
    WHERE status IN ('failed','partial');

INSERT INTO schema_versions (version, description)
VALUES ('002', 'Add l2_run_log table');
