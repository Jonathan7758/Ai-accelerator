-- /opt/accelerator/sql/001_init_schema.sql
-- Accelerator L2 - Initial Schema
-- Phase 0 / Step 3

-- 启用 UUID 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 表 1: ops_decisions
-- 运营决策日志。每一次"我们改了什么、为什么改、怎么验证"都是一条记录。
-- ============================================================
CREATE TABLE ops_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 决策内容
    decision_type TEXT NOT NULL,       -- prompt_change | matrix_update | workflow_tweak | strategy_pivot | other
    subject TEXT NOT NULL,             -- 受影响对象，如 "title_template_T1" 或 "matrix_entry_W3.1"
    before_state JSONB,                -- 改前
    after_state JSONB,                 -- 改后

    -- 决策的"思考"(给人和给未来 LLM 读)
    rationale TEXT NOT NULL,           -- 自然语言解释，完整因果链
    evidence JSONB DEFAULT '[]'::jsonb,-- 引用的数据，如 [{"type":"weekly_report","id":"2026W17"}]

    -- 验证回路
    verification_plan TEXT,            -- 怎么验证生效
    verification_due_at TIMESTAMPTZ,   -- 什么时候该回填
    verification_result JSONB,         -- 实际结果，验证时回填

    -- 状态
    status TEXT NOT NULL DEFAULT 'active'  -- active | reverted | superseded
        CHECK (status IN ('active', 'reverted', 'superseded')),
    superseded_by UUID REFERENCES ops_decisions(id),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ops_decisions_tenant_decided ON ops_decisions(tenant_id, decided_at DESC);
CREATE INDEX idx_ops_decisions_subject ON ops_decisions(subject);
CREATE INDEX idx_ops_decisions_status ON ops_decisions(status) WHERE status = 'active';

-- ============================================================
-- 表 2: ops_metrics
-- 指标快照。每条记录 = 某个对象在某个时点的所有指标。
-- 设计上故意"长且窄"——一行一指标快照，JSONB 里平铺所有指标。
-- ============================================================
CREATE TABLE ops_metrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 主体
    subject_type TEXT NOT NULL,        -- article | matrix_entry | title_template | weekly_aggregate
    subject_id TEXT NOT NULL,          -- 主体的标识，如 article_id 或 'W18'

    -- 指标(平铺，如 {"ctr":0.12,"read_rate":0.55,"share":0.06,"views":12340})
    metrics JSONB NOT NULL,

    -- 数据来源(便于追溯)
    source TEXT NOT NULL,              -- pulse_db | wechat_api | manual_input | computed

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ops_metrics_subject ON ops_metrics(tenant_id, subject_type, subject_id, snapshot_at DESC);
CREATE INDEX idx_ops_metrics_time ON ops_metrics(tenant_id, snapshot_at DESC);

-- ============================================================
-- 表 3: ops_playbook
-- 沉淀下来的"已被验证的策略"。从 ops_decisions 里晋升而来。
-- ============================================================
CREATE TABLE ops_playbook (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'history',  -- 'global' 表示跨 tenant

    -- 策略本体
    pattern_name TEXT NOT NULL UNIQUE,         -- 唯一名称
    context TEXT NOT NULL,                     -- 什么场景下适用
    recipe TEXT NOT NULL,                      -- 具体怎么做(prompt 片段、步骤、规则)

    -- 出处
    evidence_decisions UUID[] DEFAULT '{}',    -- 来自哪些 ops_decisions 的支持

    -- 置信度
    confidence REAL NOT NULL DEFAULT 0.5       -- 0-1
        CHECK (confidence >= 0 AND confidence <= 1),
    last_validated_at TIMESTAMPTZ,

    -- 状态
    status TEXT NOT NULL DEFAULT 'active'      -- active | deprecated
        CHECK (status IN ('active', 'deprecated')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ops_playbook_tenant ON ops_playbook(tenant_id, status);
CREATE INDEX idx_ops_playbook_confidence ON ops_playbook(confidence DESC) WHERE status = 'active';

-- ============================================================
-- 自动维护 updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_ops_decisions_updated_at BEFORE UPDATE ON ops_decisions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_ops_playbook_updated_at BEFORE UPDATE ON ops_playbook
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- 元信息表(记录 schema 版本，以后迁移用)
-- ============================================================
CREATE TABLE schema_versions (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_versions (version, description)
VALUES ('001', 'Initial schema: ops_decisions, ops_metrics, ops_playbook');
