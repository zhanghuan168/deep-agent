"""任务拆解器（planner）。

提供两个能力：
- `make_plan(title, description)` —— 把老板的描述拆成 work_items。
- `refine_plan(plan, feedback)` —— 根据老板反馈调整计划。

如果 pydantic-ai 与 LLM 可用，会走模型；否则走规则回退。
LLM 配置从运行时 settings 读取（前端 /api/settings 可改），不再依赖环境变量。
"""
from __future__ import annotations

import json
import re
import textwrap
from typing import Any

from app.logging import logger
from app import runtime_settings


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

PLAN_SCHEMA_HINT = textwrap.dedent(
    """
    {
      "summary": "一句话总结",
      "work_items": [
        {
          "title": "工作项标题",
          "description": "简短描述",
          "priority": 1-10,
          "inputs": { "任意": "上下文" }
        }
      ]
    }
    """
).strip()


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


async def make_plan(title: str, description: str) -> dict[str, Any]:
    """把老板的需求拆成结构化计划。"""
    llm_plan = await _try_llm_plan(title, description)
    if llm_plan is not None:
        return llm_plan
    return _rule_based_plan(title, description)


async def refine_plan(plan: dict[str, Any], feedback: str) -> dict[str, Any]:
    """根据反馈微调计划（合并/拆分/重命名）。"""
    items = plan.get("work_items", [])
    if not items:
        return _rule_based_plan(plan.get("summary", "未命名"), feedback)
    feedback_lower = feedback.lower()
    new_items: list[dict[str, Any]] = []
    for item in items:
        title = item.get("title", "")
        if any(k in title.lower() for k in re.findall(r"\w+", feedback_lower)[:5]):
            # 命中关键词：在 description 追加反馈
            item = dict(item)
            item["description"] = (item.get("description") or "") + f"\n[反馈] {feedback}"
        new_items.append(item)
    if "拆分" in feedback or "细化" in feedback:
        new_items = _split_items(new_items)
    if "合并" in feedback:
        new_items = _merge_items(new_items)
    plan = dict(plan)
    plan["work_items"] = new_items
    plan["summary"] = plan.get("summary", "") + f"（已根据反馈调整: {feedback[:50]}）"
    return plan


# ---------------------------------------------------------------------------
# 内部：LLM（可选）
# ---------------------------------------------------------------------------


async def _try_llm_plan(title: str, description: str) -> dict[str, Any] | None:
    """尝试调用 LLM。如果模型不可用或调用失败，返回 None。"""
    cfg = await runtime_settings.get_llm_config()
    provider = (cfg.get("provider") or "").lower()
    model = cfg.get("model") or ""
    base_url = (cfg.get("base_url") or "").rstrip("/")
    api_key = cfg.get("api_key") or ""

    if not provider or not model:
        return None

    # 先尝试直接用 httpx 调用（OpenAI 兼容协议）
    result = await _llm_plan_via_httpx(provider, model, base_url, api_key, title, description)
    if result is not None:
        return result

    # 降级到规则版
    return None


async def _llm_plan_via_httpx(
    provider: str, model: str, base_url: str, api_key: str, title: str, description: str
) -> dict[str, Any] | None:
    """直接用 httpx 调 LLM（OpenAI 兼容协议），绕过 pydantic-ai。"""
    if not base_url:
        return None

    import httpx, re

    # 确保 base_url 有 /v1 后缀
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    url = f"{base_url}/chat/completions"

    system_prompt = (
        "你是一名项目经理，擅长把模糊需求拆成可执行的工作项。"
        f"严格输出 JSON，结构示例：\n{PLAN_SCHEMA_HINT}"
    )
    prompt = f"标题：{title}\n描述：{description}\n请输出 JSON 计划。"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        async with _llm_semaphore:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code == 429:
                    logger.warning("LLM rate limit，等待后重试")
                    await asyncio.sleep(5.0)
                    r = await client.post(url, json=payload, headers=headers)
                if r.status_code != 200:
                    logger.warning("LLM 调用失败 status={} body={}", r.status_code, r.text[:200])
                    return None
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            logger.info("LLM 计划生成成功 (provider={} model={})", provider, model)
            return _safe_parse_json(content)
    except Exception as e:
        logger.warning("LLM 计划生成失败: {}", e)
        return None


def _build_model_string(
    provider: str, model: str, base_url: str, api_key: str
) -> str | None:
    """根据 provider 构造 pydantic-ai 接受的 model 字符串。

    pydantic-ai 用 "<provider>:<model>" 形式，例如 "openai:gpt-4o-mini"。
    我们把"openai 兼容"协议（Ollama / Deepseek / GLM / vLLM）也走 openai 适配器。
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if not model:
        return None

    if provider in ("openai", "ollama", "deepseek", "glm", "openai_compat", "vllm", "minimax"):
        return f"openai:{model}"
    if provider == "anthropic":
        return f"anthropic:{model}"
    if provider == "gemini":
        return f"gemini:{model}"
    # 未知 provider 退回到 openai
    logger.warning("未知 provider: {}, 按 openai 处理", provider)
    return f"openai:{model}"


def _safe_parse_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出 JSON。"""
    text = text.strip()
    # 去掉 markdown code fence
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# 内部：规则回退
# ---------------------------------------------------------------------------


def _rule_based_plan(title: str, description: str) -> dict[str, Any]:
    """无 LLM 时的兜底实现。"""
    logger.info("使用规则版规划: {}", title)
    sentences = _split_sentences(description or title)
    work_items: list[dict[str, Any]] = []
    if not sentences:
        sentences = [title or "实现核心功能"]
    for idx, sent in enumerate(sentences, start=1):
        work_items.append(
            {
                "title": f"{sent[:30]}（{idx}）" if len(sent) > 30 else sent,
                "description": sent,
                "priority": min(10, 5 + idx),
                "inputs": {"raw": sent, "source_title": title},
            }
        )
    # 至少保证一个工作项
    if not work_items:
        work_items.append(
            {
                "title": title or "未命名",
                "description": description or "",
                "priority": 5,
            }
        )
    return {"summary": title or "未命名", "work_items": work_items}


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[。.!?？;\n；\u3001]+", text)
    return [p.strip() for p in parts if p.strip()]


def _split_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """简单拆分：把第一个工作项按句子拆成多个。"""
    if not items:
        return items
    first = items[0]
    desc = first.get("description", "")
    sentences = _split_sentences(desc)
    if len(sentences) <= 1:
        return items
    new_items = []
    for s in sentences:
        new_items.append(
            {
                "title": s[:30] or first.get("title", ""),
                "description": s,
                "priority": first.get("priority", 5),
            }
        )
    return new_items + items[1:]


def _merge_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) < 2:
        return items
    head, *rest = items
    merged = dict(head)
    merged["description"] = (head.get("description", "") + "\n---\n" + "\n---\n".join(
        r.get("description", "") for r in rest
    )).strip()
    return [merged]
