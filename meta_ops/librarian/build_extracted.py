"""Phase 2 Step 5: extracted/ LLM 加工。

对决策 4 锁定的 4 个业务概念主题,从 docs + .py 多源融合,产出
knowledge/pulse/extracted/<topic>.md。

每个 topic:
- 一组源(docs 路径 + code 路径)
- 一份 prompt 模板(本 v1 共用 prompts/extracted_v1.md,后续可分主题特化)
- 增量:任一源 sha 变 → 重生该 topic

mapping 直接嵌代码(不引入 yaml 依赖),决策 4 锁定的 4 主题:
1. matrix_v2_taxonomy
2. title_templates
3. tg_review_flow
4. publish_platforms
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
import logging

from meta_ops.librarian.change_detect import (
    sha256_file,
    load_manifest,
    write_manifest,
)
from meta_ops.librarian.llm_client import call_claude

log = logging.getLogger(__name__)

KNOWLEDGE_DOCS = Path("/opt/accelerator/knowledge/pulse/docs")
PULSE_CODE_ROOT = Path("/opt/accelerator/knowledge/pulse_src/code")
EXTRACTED_ROOT = Path("/opt/accelerator/knowledge/pulse/extracted")
EXTRACTED_MANIFEST = EXTRACTED_ROOT / "_meta" / "manifest.json"
PROMPT_TEMPLATE = Path("/opt/accelerator/prompts/extracted_v1.md")


@dataclass(frozen=True)
class TopicMapping:
    name: str
    description: str  # 写进 user prompt,告诉 LLM 输出焦点
    doc_sources: tuple[str, ...] = ()  # paths under KNOWLEDGE_DOCS
    code_sources: tuple[str, ...] = ()  # paths under PULSE_CODE_ROOT

    def all_sources(self) -> list[tuple[str, Path]]:
        out: list[tuple[str, Path]] = []
        for d in self.doc_sources:
            out.append((f"docs/{d}", KNOWLEDGE_DOCS / d))
        for c in self.code_sources:
            out.append((f"code/{c}", PULSE_CODE_ROOT / c))
        return out


TOPICS: tuple[TopicMapping, ...] = (
    TopicMapping(
        name="matrix_v2_taxonomy",
        description="Pulse 内容矩阵(matrix_v2)的分类法权威定义:类目层次、维度字段、tag 与 angle 的取值规则。",
        doc_sources=("01-system-architecture.md", "02-module-design.md"),
        code_sources=("version1/hk_node/services/history_engine/content_matrix.py",),
    ),
    TopicMapping(
        name="title_templates",
        description="标题模板的规则、占位符、变量,以及在内容生产里如何被装填和选择。",
        doc_sources=("02-module-design.md", "07-user-guide.md"),
        code_sources=(
            "version1/hk_node/services/content_engine/wechat_draft_adapter.py",
            "version1/hk_node/services/content_engine/wechat_tech_brief_renderer.py",
        ),
    ),
    TopicMapping(
        name="tg_review_flow",
        description="Telegram review 决策流的状态机:从文章生成到 approve/reject/edit,各动作触发什么下游、状态如何流转、超时如何降级。",
        doc_sources=("05-operator-manual.md", "07-user-guide.md"),
        code_sources=("version1/hk_node/services/publisher/telegram_review.py",),
    ),
    TopicMapping(
        name="publish_platforms",
        description="各发布平台(微信/小红书/微头条/视频号等)的发布流程差异:用什么通道(API/浏览器/手动)、字段映射、审核规则、可重试性。",
        doc_sources=("02-module-design.md", "05-operator-manual.md"),
        code_sources=(
            "version1/hk_node/services/publisher/browser_base.py",
            "version1/hk_node/services/publisher/telegram_review.py",
            "version1/hk_node/services/daily_workflow.py",
        ),
    ),
)


def _load_template() -> str:
    if not PROMPT_TEMPLATE.is_file():
        raise FileNotFoundError(f"prompt template missing: {PROMPT_TEMPLATE}")
    return PROMPT_TEMPLATE.read_text(encoding="utf-8")


def _load_sources(topic: TopicMapping) -> tuple[str, list[str]]:
    """Returns (concatenated source body for user message, list of missing display paths)."""
    parts: list[str] = []
    missing: list[str] = []
    for display, abs_path in topic.all_sources():
        if not abs_path.is_file():
            missing.append(display)
            continue
        text = abs_path.read_text(encoding="utf-8", errors="replace")
        parts.append(f"# 源:`{display}`\n\n```\n{text}\n```\n")
    return ("\n---\n\n".join(parts), missing)


def _sources_sha_map(topic: TopicMapping) -> dict[str, str]:
    """Per-topic, per-source sha map. Keys are namespaced so different
    topics that share a source still detect changes independently."""
    out: dict[str, str] = {}
    for display, abs_path in topic.all_sources():
        if abs_path.is_file():
            out[f"{topic.name}::{display}"] = sha256_file(abs_path)
    return out


def build_one(
    topic: TopicMapping,
    *,
    related_run_id: str | None = None,
) -> dict:
    body, missing = _load_sources(topic)
    if not body:
        return {
            "ok": False, "topic": topic.name, "regenerated": False,
            "missing_sources": missing,
            "cost_usd": 0.0, "error": "all sources missing",
        }

    system = _load_template()
    user = (
        f"# Extracted topic:`{topic.name}`\n\n"
        f"**目标**:{topic.description}\n\n"
        f"以下是 {len(topic.all_sources()) - len(missing)} 份源材料"
        f"(其中缺失 {len(missing)} 份:{missing if missing else '无'}):\n\n"
        + body
    )

    result = call_claude(
        kind="extracted",
        target_path=topic.name,
        prompt_template="prompts/extracted_v1.md",
        system=system,
        user=user,
        related_run_id=related_run_id,
    )

    if not result["ok"]:
        return {
            "ok": False, "topic": topic.name, "regenerated": False,
            "missing_sources": missing,
            "cost_usd": result["cost_usd"], "error": result["error"],
        }

    out_path = EXTRACTED_ROOT / f"{topic.name}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result["response_text"], encoding="utf-8")
    log.info("wrote extracted: %s (cost $%.4f)", out_path, result["cost_usd"])

    return {
        "ok": True, "topic": topic.name, "regenerated": True,
        "missing_sources": missing,
        "cost_usd": result["cost_usd"], "error": None,
    }


def build_all(
    *,
    related_run_id: str | None = None,
    force: bool = False,
) -> dict:
    EXTRACTED_ROOT.mkdir(parents=True, exist_ok=True)
    previous = load_manifest(EXTRACTED_MANIFEST)

    summary = {
        "topics_total": len(TOPICS),
        "topics_built": 0,
        "topics_skipped": 0,
        "topics_failed": 0,
        "total_cost_usd": 0.0,
        "details": [],
    }
    new_manifest: dict[str, str] = {}

    for topic in TOPICS:
        sha_map = _sources_sha_map(topic)
        new_manifest.update(sha_map)

        out_path = EXTRACTED_ROOT / f"{topic.name}.md"
        all_unchanged = bool(sha_map) and all(
            previous.get(k) == v for k, v in sha_map.items()
        )
        if all_unchanged and out_path.is_file() and not force:
            summary["topics_skipped"] += 1
            summary["details"].append({
                "topic": topic.name, "status": "skipped_unchanged", "cost_usd": 0.0,
            })
            continue

        result = build_one(topic, related_run_id=related_run_id)
        summary["total_cost_usd"] = round(
            summary["total_cost_usd"] + result["cost_usd"], 6
        )
        if result["ok"]:
            summary["topics_built"] += 1
            summary["details"].append({
                "topic": topic.name, "status": "built",
                "cost_usd": result["cost_usd"],
                "missing_sources": result["missing_sources"],
            })
        else:
            summary["topics_failed"] += 1
            summary["details"].append({
                "topic": topic.name, "status": "failed",
                "cost_usd": result["cost_usd"], "error": result["error"],
                "missing_sources": result["missing_sources"],
            })

    write_manifest(EXTRACTED_MANIFEST, new_manifest, EXTRACTED_ROOT)
    return summary


def main() -> dict:
    from meta_ops.common.logging_config import setup_logging
    import sys

    setup_logging("build_extracted")
    force = "--force" in sys.argv
    only = None
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            only = sys.argv[idx + 1]

    if only:
        topic = next((t for t in TOPICS if t.name == only), None)
        if topic is None:
            print(json.dumps({"error": f"unknown topic: {only}"}))
            return {}
        result = {"single_run": build_one(topic)}
    else:
        result = build_all(force=force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main()
