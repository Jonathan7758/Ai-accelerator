"""Phase 3 Step 3: 周报 §4 候选决策 markdown 解析器。

输入: reports/<week>.md 全文(str)
输出: list[CandidateDecision]

设计原则:
- 纯函数,可单测
- 失败容错:缺 §4 返 [];字段缺失填 None + parse_warnings;evidence JSON 损坏 evidence=[] + parse_warnings
- 不抛异常(除非传入非 str)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class CandidateDecision:
    """对应 PHASE2_ANALYST_SPEC §5 决策 4 的 6 字段 + 解析元数据。"""
    decision_type: str | None
    subject: str | None
    rationale: str | None
    verification_plan: str | None
    risk: str | None
    evidence: list[Any] = field(default_factory=list)
    candidate_index: int = 0
    title: str | None = None
    parse_warnings: list[str] = field(default_factory=list)


KNOWN_FIELDS: tuple[str, ...] = (
    "decision_type",
    "subject",
    "rationale",
    "verification_plan",
    "risk",
    "evidence",
)

# §4 节:## 4. 候选决策...  到下个 ## 或 EOF。?: 容忍 "## 4." / "## 4 候选..." / 中英括号
_SECTION_4_RE = re.compile(
    r'^##\s*4(?:\.|\s).*?$\n(.*?)(?=^##\s|\Z)',
    re.MULTILINE | re.DOTALL,
)

# 决策头:### 决策 N: (中英冒号都接受)
_DECISION_HEAD_RE = re.compile(
    r'^###\s*决策\s*(\d+)\s*[:：]\s*(.*?)$',
    re.MULTILINE,
)

# evidence 专属:- **evidence**: 紧跟一个 ```json ... ``` 代码块
_EVIDENCE_JSON_RE = re.compile(
    r'^-\s*\*\*evidence\*\*\s*[:：]\s*\n```json\s*\n(.*?)\n```',
    re.MULTILINE | re.DOTALL,
)

# 多行字段终止符(下个已知字段 / --- 分隔 / json fence / EOF)
_FIELDS_OR_BLOCKS = (
    r'\n-\s*\*\*(?:' + '|'.join(KNOWN_FIELDS) + r')\*\*\s*[:：]'
    r'|\n---'
    r'|\n\s*```json'
)


def _extract_field(body: str, fname: str) -> str | None:
    """提取 - **fname**: <value> 字段值。支持单行和多行(直到下个已知字段/分隔/EOF)。

    evidence 字段不走这条路,见 _EVIDENCE_JSON_RE。
    """
    pat = re.compile(
        r'^-\s*\*\*' + re.escape(fname) + r'\*\*\s*[:：]\s*(.*?)(?=' + _FIELDS_OR_BLOCKS + r'|\Z)',
        re.MULTILINE | re.DOTALL,
    )
    m = pat.search(body)
    return m.group(1).strip() if m else None


def _split_decision_blocks(section_4: str) -> list[tuple[int, str, str]]:
    """切成 [(decision_number, title, body), ...]"""
    matches = list(_DECISION_HEAD_RE.finditer(section_4))
    if not matches:
        return []
    blocks: list[tuple[int, str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_4)
        body = section_4[start:end]
        blocks.append((int(m.group(1)), m.group(2).strip(), body))
    return blocks


def _parse_one_decision(body: str) -> CandidateDecision:
    cd = CandidateDecision(
        decision_type=None,
        subject=None,
        rationale=None,
        verification_plan=None,
        risk=None,
    )

    for fname in ("decision_type", "subject", "rationale", "verification_plan", "risk"):
        value = _extract_field(body, fname)
        if value is None:
            cd.parse_warnings.append(f"missing field: {fname}")
        else:
            setattr(cd, fname, value)

    em = _EVIDENCE_JSON_RE.search(body)
    if em is None:
        cd.parse_warnings.append("missing field: evidence")
    else:
        try:
            parsed = json.loads(em.group(1))
        except json.JSONDecodeError as e:
            cd.parse_warnings.append(f"evidence JSON invalid: {e.msg} at line {e.lineno}")
        else:
            if isinstance(parsed, list):
                cd.evidence = parsed
            else:
                cd.parse_warnings.append(
                    f"evidence JSON not a list (got {type(parsed).__name__}); coerced to []"
                )

    return cd


def parse_report(md_text: str) -> list[CandidateDecision]:
    """解析周报全文,返回候选决策列表(可能为空)。"""
    if not isinstance(md_text, str):
        raise TypeError(f"md_text must be str, got {type(md_text).__name__}")

    section_match = _SECTION_4_RE.search(md_text)
    if section_match is None:
        log.info("parse_report: §4 候选决策 section not found")
        return []

    blocks = _split_decision_blocks(section_match.group(1))
    if not blocks:
        log.info("parse_report: §4 found but contains 0 decisions")
        return []

    results: list[CandidateDecision] = []
    for number, title, body in blocks:
        cd = _parse_one_decision(body)
        cd.candidate_index = number
        cd.title = title
        results.append(cd)
    return results
