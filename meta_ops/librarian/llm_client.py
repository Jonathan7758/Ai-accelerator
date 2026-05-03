"""Phase 2: Anthropic SDK 封装 + 双轨留痕(决策 1 = C)。

每次 LLM 调用:
1. 调 messages.create(),走 prompt caching(system 部分 cache_control: ephemeral)
2. 全量 prompt + response 写 knowledge/_meta/llm_calls.jsonl
3. 摘要(token / cost / status)写 DB 表 l2_llm_calls
4. 返回 {ok, response_text, usage, cost_usd, duration_seconds, error}

设计点:
- 失败优雅:返回 ok=False + error,绝不抛异常往上拖
- 默认 Sonnet 4.6(spec §0.4),需要更高质量再切 Opus
- DB 写失败不连累 jsonl 写;jsonl 写失败也不连累 DB(双轨容错)
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import os
import time
import uuid

import anthropic
from dotenv import load_dotenv

from meta_ops.common.db import get_local_db

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
JSONL_PATH = Path("/opt/accelerator/knowledge/_meta/llm_calls.jsonl")

# USD per 1M tokens (cached pricing snapshot)
PRICING = {
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00,
        "cache_read": 0.30, "cache_write_5m": 3.75,
    },
    "claude-opus-4-7": {
        "input": 5.00, "output": 25.00,
        "cache_read": 0.50, "cache_write_5m": 6.25,
    },
    "claude-haiku-4-5": {
        "input": 1.00, "output": 5.00,
        "cache_read": 0.10, "cache_write_5m": 1.25,
    },
}


def _client() -> anthropic.Anthropic:
    load_dotenv("/opt/accelerator/.env")
    return anthropic.Anthropic()


def estimate_cost_usd(model: str, usage) -> float:
    pricing = PRICING.get(model)
    if not pricing or usage is None:
        return 0.0
    input_t = getattr(usage, "input_tokens", 0) or 0
    output_t = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    total = (
        input_t * pricing["input"]
        + output_t * pricing["output"]
        + cache_read * pricing["cache_read"]
        + cache_create * pricing["cache_write_5m"]
    ) / 1_000_000
    return round(total, 6)


def call_claude(
    *,
    kind: str,
    target_path: str,
    prompt_template: str,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    related_run_id: str | None = None,
) -> dict:
    """单一 LLM 调用入口。返回 dict;不抛异常。"""
    started_at = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)

    response = None
    err_msg: str | None = None
    try:
        client = _client()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as e:
        err_msg = f"{type(e).__name__}: {str(e)[:300]}"
        log.error("Anthropic APIError: %s", err_msg)
    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)[:300]}"
        log.error("Unexpected LLM error: %s", err_msg)

    ended_at = datetime.now(timezone.utc)
    duration = round(time.monotonic() - started_mono, 3)

    status = "ok" if response is not None else "failed"
    response_text = ""
    if response is not None:
        for block in response.content:
            if block.type == "text":
                response_text += block.text

    cost = estimate_cost_usd(model, response.usage if response else None)

    _write_jsonl(
        kind=kind, target_path=target_path, prompt_template=prompt_template,
        model=model, system=system, user=user, response=response,
        started_at=started_at, ended_at=ended_at, status=status,
        error_msg=err_msg,
    )
    _write_db(
        kind=kind, related_run_id=related_run_id, target_path=target_path,
        prompt_template=prompt_template, model=model,
        usage=(response.usage if response else None),
        started_at=started_at, ended_at=ended_at, duration=duration,
        status=status, error_msg=err_msg, cost_usd=cost,
    )

    return {
        "ok": status == "ok",
        "response_text": response_text if response else None,
        "usage": _usage_dict(response.usage) if response else None,
        "cost_usd": cost,
        "duration_seconds": duration,
        "error": err_msg,
    }


def _usage_dict(usage) -> dict:
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def _write_jsonl(
    *, kind, target_path, prompt_template, model, system, user, response,
    started_at, ended_at, status, error_msg,
) -> None:
    entry = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "target_path": target_path,
        "prompt_template": prompt_template,
        "model": model,
        "system": system,
        "user": user if len(user) <= 200_000 else user[:200_000] + "...[truncated]",
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "status": status,
        "error": error_msg,
    }
    if response is not None:
        entry["response_text"] = "".join(
            b.text for b in response.content if b.type == "text"
        )
        entry["usage"] = _usage_dict(response.usage)
        entry["request_id"] = getattr(response, "_request_id", None)
    try:
        with JSONL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.error("jsonl write failed: %s", e)


def _write_db(
    *, kind, related_run_id, target_path, prompt_template, model, usage,
    started_at, ended_at, duration, status, error_msg, cost_usd,
) -> None:
    try:
        db = get_local_db()
        try:
            with db.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO l2_llm_calls (
                        kind, related_run_id, target_path, prompt_template, model,
                        input_tokens, output_tokens,
                        cache_read_tokens, cache_creation_tokens,
                        estimated_cost_usd, status, error_message,
                        started_at, ended_at, duration_seconds
                    )
                    VALUES (%s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s)
                    """,
                    (
                        kind, related_run_id, target_path, prompt_template, model,
                        getattr(usage, "input_tokens", None) if usage else None,
                        getattr(usage, "output_tokens", None) if usage else None,
                        getattr(usage, "cache_read_input_tokens", None) if usage else None,
                        getattr(usage, "cache_creation_input_tokens", None) if usage else None,
                        cost_usd, status, error_msg,
                        started_at, ended_at, duration,
                    ),
                )
            db.commit()
        finally:
            db.close()
    except Exception as e:
        log.error("DB write to l2_llm_calls failed: %s", e)
