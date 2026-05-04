-- /opt/accelerator/sql/004_decision_threads.sql
-- Phase 3 Step 1: ops_decision_threads 表
-- 存"决策中状态"——周报推到 TG 后,每条候选决策的状态 / 关联消息 ID / 用户响应轨迹。
-- 终态(approved)时写 ops_decisions 并通过 ops_decision_id 反向关联。

CREATE TABLE ops_decision_threads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',

    -- 来源(从周报 §4 解析得来)
    report_week TEXT NOT NULL,           -- '2026W19'
    candidate_index INTEGER NOT NULL,    -- 报告里第几条候选决策(1-based)
    decision_type TEXT NOT NULL,         -- 解析自报告
    subject TEXT NOT NULL,
    rationale_proposed TEXT NOT NULL,    -- LLM 在报告里写的 rationale
    verification_plan TEXT NOT NULL,
    risk TEXT,
    evidence JSONB DEFAULT '[]'::jsonb,

    -- 状态机(spec v1 §Step 5 的 8 transition)
    state TEXT NOT NULL                  -- proposed | displayed | in_discussion
                                         -- | approved_pending_rationale
                                         -- | approved | rejected | deferred
        CHECK (state IN ('proposed','displayed','in_discussion',
                         'approved_pending_rationale','approved',
                         'rejected','deferred')),

    -- TG 关联(用于后续 edit message / 移除按钮)
    tg_chat_id BIGINT,
    tg_message_id BIGINT,

    -- 用户响应
    user_rationale TEXT,                 -- 用户在 approved_pending_rationale 阶段填的理由
    decided_by TEXT,                     -- TG username,审批人(单 admin v0)
    decided_at TIMESTAMPTZ,

    -- 终态后写入 ops_decisions 的关联(approved 时回填)
    ops_decision_id UUID REFERENCES ops_decisions(id),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_decision_threads_state ON ops_decision_threads(state, created_at DESC);
CREATE INDEX idx_decision_threads_week ON ops_decision_threads(report_week, candidate_index);
CREATE INDEX idx_decision_threads_tg_msg ON ops_decision_threads(tg_chat_id, tg_message_id);

INSERT INTO schema_versions (version, description)
VALUES ('004', 'Add ops_decision_threads table (Phase 3 decision state machine)');
