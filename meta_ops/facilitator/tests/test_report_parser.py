"""Phase 3 Step 3: report_parser 单测。

覆盖:
1. 完整 2026W19.md → 4 条决策,各字段非空,evidence 是 list
2. 缺 §4 整段 → []
3. §4 存在但内含 0 个决策(如"本周无候选") → []
4. 字段顺序乱序 → 仍能正确取到值
5. risk 字段缺失 → risk=None + parse_warnings 含 "missing field: risk"
6. evidence JSON 格式损坏 → evidence=[] + parse_warnings 含 "evidence JSON invalid"
7. §4 仅 1 条决策 → 返 1 条
8. §4 末跨入 §5 → 不读 §5 内容
9. evidence JSON 不是 list (是 dict) → evidence=[] + warning
10. md_text 非 str → TypeError
"""
from __future__ import annotations

from pathlib import Path

import pytest

from meta_ops.facilitator.report_parser import (
    CandidateDecision,
    KNOWN_FIELDS,
    parse_report,
)

FIXTURE_2026W19 = Path(__file__).parent / "fixtures" / "2026W19.md"


# ----------------------------------------------------------------------
# Test 1: 真实样本 — 2026W19.md
# ----------------------------------------------------------------------
def test_parse_real_2026w19_returns_4_decisions():
    md = FIXTURE_2026W19.read_text(encoding="utf-8")
    decisions = parse_report(md)
    assert len(decisions) == 4, (
        f"expect 4 decisions, got {len(decisions)} — "
        f"titles: {[d.title for d in decisions]}"
    )

    for i, d in enumerate(decisions, start=1):
        assert d.candidate_index == i, f"decision #{i} has index {d.candidate_index}"
        assert d.title and len(d.title) > 5, f"decision #{i} title empty: {d.title!r}"
        assert d.decision_type, f"decision #{i} missing decision_type"
        assert d.subject, f"decision #{i} missing subject"
        assert d.rationale and len(d.rationale) > 50, (
            f"decision #{i} rationale too short: {len(d.rationale or '')}"
        )
        assert d.verification_plan, f"decision #{i} missing verification_plan"
        assert d.risk, f"decision #{i} missing risk"
        assert isinstance(d.evidence, list) and len(d.evidence) >= 1, (
            f"decision #{i} evidence empty or not list: {d.evidence!r}"
        )
        # 在 v0 报告里,所有决策都应该解析干净
        assert not d.parse_warnings, (
            f"decision #{i} unexpected warnings: {d.parse_warnings}"
        )

    # 抽查决策 1 字段值
    d1 = decisions[0]
    assert d1.decision_type == "workflow_tweak"  # 首尾反引号被 _extract_field strip(瑕 2 修复)
    assert "published" in d1.title
    assert d1.evidence[0]["type"] == "ops_metric"


# ----------------------------------------------------------------------
# Test 2: 缺 §4 整段
# ----------------------------------------------------------------------
def test_no_section_4_returns_empty():
    md = """# 周报 2026W99

## 1. 数据状态
nothing.

## 2. 摘要
nothing.

## 5. 验证回填
nothing.
"""
    assert parse_report(md) == []


# ----------------------------------------------------------------------
# Test 3: §4 存在但 0 决策
# ----------------------------------------------------------------------
def test_section_4_with_zero_decisions():
    md = """# 周报 2026W99

## 4. 候选决策

本周无候选决策(数据不足以支撑任何建议)。

## 5. 验证回填
nothing.
"""
    assert parse_report(md) == []


# ----------------------------------------------------------------------
# Test 4: 字段乱序
# ----------------------------------------------------------------------
def test_field_order_does_not_matter():
    md = """## 4. 候选决策

### 决策 1: 字段乱序的决策

- **subject**: my_subject
- **risk**: low
- **decision_type**: `prompt_change`
- **verification_plan**: 下周看
- **rationale**: 因为某个原因,需要做这个事
- **evidence**:
```json
[{"type": "test", "v": 1}]
```

## 5. 后续
"""
    decisions = parse_report(md)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.decision_type == "prompt_change"  # 反引号被 strip(瑕 2)
    assert d.subject == "my_subject"
    assert d.rationale == "因为某个原因,需要做这个事"
    assert d.verification_plan == "下周看"
    assert d.risk == "low"
    assert d.evidence == [{"type": "test", "v": 1}]
    assert d.parse_warnings == []


# ----------------------------------------------------------------------
# Test 5: risk 缺失
# ----------------------------------------------------------------------
def test_missing_risk_field():
    md = """## 4. 候选决策

### 决策 1: 缺 risk 字段

- **decision_type**: `workflow_tweak`
- **subject**: foo
- **rationale**: 一段说明
- **verification_plan**: 下周看
- **evidence**:
```json
[]
```

## 5. xxx
"""
    decisions = parse_report(md)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.risk is None
    assert any("missing field: risk" in w for w in d.parse_warnings)
    # 其他字段应正常解出
    assert d.decision_type == "workflow_tweak"  # 反引号被 strip(瑕 2)
    assert d.subject == "foo"


# ----------------------------------------------------------------------
# Test 6: evidence JSON 损坏
# ----------------------------------------------------------------------
def test_evidence_json_malformed():
    md = """## 4. 候选决策

### 决策 1: evidence 损坏

- **decision_type**: `workflow_tweak`
- **subject**: foo
- **rationale**: 一段说明
- **verification_plan**: 下周看
- **risk**: low
- **evidence**:
```json
[{"type": "ops_metric", "broken_no_close_brace
```

## 5. xxx
"""
    decisions = parse_report(md)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.evidence == []
    assert any("evidence JSON invalid" in w for w in d.parse_warnings)
    # 其他字段不受影响
    assert d.decision_type == "workflow_tweak"  # 反引号被 strip(瑕 2)


# ----------------------------------------------------------------------
# Test 7: 单条决策
# ----------------------------------------------------------------------
def test_single_decision():
    md = """## 4. 候选决策

### 决策 1: 唯一一条

- **decision_type**: `prompt_change`
- **subject**: bar
- **rationale**: 因为...
- **verification_plan**: 下周看
- **risk**: 无
- **evidence**:
```json
[{"type": "test"}]
```
"""
    decisions = parse_report(md)
    assert len(decisions) == 1
    assert decisions[0].candidate_index == 1
    assert decisions[0].title == "唯一一条"


# ----------------------------------------------------------------------
# Test 8: §4 不应越界读到 §5
# ----------------------------------------------------------------------
def test_section_4_does_not_leak_into_section_5():
    md = """## 4. 候选决策

### 决策 1: 真决策

- **decision_type**: `t`
- **subject**: s
- **rationale**: r
- **verification_plan**: vp
- **risk**: rk
- **evidence**:
```json
[]
```

## 5. 验证回填

### 决策 2: 这是 §5 的内容,不该被 parse_report 当成候选决策

- **decision_type**: should_not_be_parsed
- **subject**: should_not
- **rationale**: should_not
- **verification_plan**: should_not
- **risk**: should_not
- **evidence**:
```json
[]
```
"""
    decisions = parse_report(md)
    assert len(decisions) == 1, (
        f"§5 内容泄漏到 §4 解析,got {len(decisions)} decisions"
    )
    assert decisions[0].title == "真决策"
    assert decisions[0].decision_type == "t"  # 反引号被 strip(瑕 2)


# ----------------------------------------------------------------------
# Test 9: evidence JSON 不是 list
# ----------------------------------------------------------------------
def test_evidence_not_a_list():
    md = """## 4. 候选决策

### 决策 1: evidence 是 dict

- **decision_type**: `t`
- **subject**: s
- **rationale**: r
- **verification_plan**: vp
- **risk**: rk
- **evidence**:
```json
{"type": "i_am_a_dict_not_a_list"}
```
"""
    decisions = parse_report(md)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.evidence == []
    assert any("not a list" in w for w in d.parse_warnings)


# ----------------------------------------------------------------------
# Test 10: 非 str 输入
# ----------------------------------------------------------------------
def test_non_str_input_raises():
    with pytest.raises(TypeError):
        parse_report(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_report(b"bytes are not str")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Sanity: KNOWN_FIELDS 顺序
# ----------------------------------------------------------------------
def test_known_fields_constant():
    # parse_one 假设 evidence 是最后一个特殊字段;若未来加字段,这里要更新
    assert KNOWN_FIELDS[-1] == "evidence"
    assert "rationale" in KNOWN_FIELDS
    assert "verification_plan" in KNOWN_FIELDS


# ----------------------------------------------------------------------
# Test 11(Step 6 顺手补): _extract_field 应去首尾反引号
# ----------------------------------------------------------------------
def test_field_value_strips_backticks():
    """LLM 偶尔把 decision_type / subject 整段包反引号,导致下游路由判错。
    parser 必须在返回前 strip('`'),内部反引号(如 setup_cross_links_for_3lian())不动。
    """
    md = """## 4. 候选决策

### 决策 1: 反引号污染样本
- **decision_type**: `workflow_tweak`
- **subject**: `content_matrix.setup_cross_links_for_3lian()`
- **rationale**: 用户体验提升
- **verification_plan**: 2026W20 验证
- **risk**: 低
- **evidence**:
```json
[]
```
"""
    decisions = parse_report(md)
    assert len(decisions) == 1
    d = decisions[0]
    # 首尾反引号去掉,值本身的下划线 / 括号保留
    assert d.decision_type == "workflow_tweak"
    assert d.subject == "content_matrix.setup_cross_links_for_3lian()"
