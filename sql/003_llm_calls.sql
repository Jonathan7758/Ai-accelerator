-- /opt/accelerator/sql/003_llm_calls.sql
-- Phase 2 决策 1 = C 双轨:LLM 调用留痕(DB 摘要 + jsonl 全量)
-- 本表存摘要;完整 prompt/response 在 knowledge/_meta/llm_calls.jsonl

CREATE TABLE l2_llm_calls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- 来源
    kind TEXT NOT NULL,                      -- 'code_index' | 'extracted' | 'analyst' | ...
    related_run_id UUID REFERENCES l2_run_log(id),  -- 关联到哪次 librarian/watcher 跑

    -- 加工对象
    target_path TEXT,                        -- 源相对路径(.py 路径 或 extracted topic 名)
    prompt_template TEXT,                    -- prompt 模板路径/版本(便于回溯)

    -- 模型 & token
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,               -- prompt caching 命中
    cache_creation_tokens INTEGER,           -- 写入 cache 的部分

    -- 成本估算(USD)
    estimated_cost_usd NUMERIC(10, 6),

    -- 结果
    status TEXT NOT NULL                     -- 'ok' | 'failed'
        CHECK (status IN ('ok','failed')),
    error_message TEXT,

    -- 时间
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_seconds REAL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_l2_llm_calls_kind_started ON l2_llm_calls(kind, started_at DESC);
CREATE INDEX idx_l2_llm_calls_run ON l2_llm_calls(related_run_id);
CREATE INDEX idx_l2_llm_calls_failed ON l2_llm_calls(started_at DESC)
    WHERE status = 'failed';

INSERT INTO schema_versions (version, description)
VALUES ('003', 'Add l2_llm_calls table (Phase 2 LLM call tracing)');
