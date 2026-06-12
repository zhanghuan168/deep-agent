"""LLM 驱动的项目管理 Agent — 修正版。

设计原则：
1. Agent 接收消息后，**先把描述优化**（让用户的话更有逻辑性）。
2. 优化后的描述 + 历史 + 工具列表一起发给 LLM。
3. LLM 决策：
   - `action=chat`：直接回复（聊天/咨询）
   - `action=ask_to_create`：建议创建任务，但**先问用户**——不直接调工具
4. 用户点"确认"后，Agent 才真正调 create_task 工具。
5. 工具执行完，再让 LLM 决定最终回复。

LLM 输出的 JSON：
{
  "action": "chat" | "ask_to_create",
  "text": "回复给用户的内容（直接显示）",
  "optimized_intent": "优化后的用户需求（内部用）",
  "params": { "title": ..., "description": ... }  // 仅 ask_to_create
}
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import repository as repo
from app.db.models import (
    ConversationRole,
    ParentTaskStatus,
    WorkflowTaskStatus,
)
from app.db.session import session_scope
from app.infra import event_bus
from app.infra.bus import Events
from app.infra.queues import (
    SchedulerQueueItem,
    scheduler_queue,
    workflow_queue,
    WorkflowQueueItem,
)
from app.logging import logger
from app import runtime_settings


# ---------------------------------------------------------------------------
# LLM 客户端（OpenAI 兼容协议）
# ---------------------------------------------------------------------------


async def _llm_chat_json(messages: list[dict], llm_cfg: dict) -> Optional[dict]:
    """调 LLM（OpenAI 兼容协议），期望返回 JSON。失败返回 None。"""
    import httpx

    base_url = (llm_cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        return None
    api_key = llm_cfg.get("api_key") or "ollama"
    model = llm_cfg.get("model") or "qwen2.5:7b"
    temperature = float(llm_cfg.get("temperature") or 0.2)
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.warning("LLM 返回非 200: {} {}", r.status_code, r.text[:200])
                return None
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            return _safe_parse_json(content)
    except Exception as e:
        logger.warning("LLM 调用失败: {}", e)
        return None


# ---------------------------------------------------------------------------
# OpenAI Tools 定义（供 LLM 调用任务管理工具）
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "列出所有父任务（最近50条），包含每个任务的状态和工作项数量。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "获取指定任务的详细信息，包含所有工作项和进度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "父任务 ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "创建新任务并自动拆解工作项。需要用户提供任务标题和描述。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务标题"},
                    "description": {"type": "string", "description": "任务详细描述"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_task",
            "description": "启动一个已创建但未开工的任务，使其进入执行队列。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "父任务 ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_task",
            "description": "暂停一个正在执行中的任务（任务状态变为 blocked）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "父任务 ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_task",
            "description": "重新启动一个被暂停的任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "父任务 ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_task",
            "description": "停止一个任务（状态变为 failed），不可恢复。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "父任务 ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "删除一个任务（软删除）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "父任务 ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_logs",
            "description": "获取任务的执行日志（最近50条）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "父任务 ID"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_experts",
            "description": "列出系统中所有可用的专家 Agent 类型。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# 工具名 → 函数映射
_TOOL_HANDLERS: dict[str, callable] = {}


def _register_tool(name: str, func: callable) -> None:
    _TOOL_HANDLERS[name] = func


def _resolve_tool_args(tool_call: dict) -> dict:
    """从 LLM tool_call 中提取函数参数。"""
    try:
        args_str = tool_call["function"]["arguments"]
        if isinstance(args_str, str):
            return json.loads(args_str)
        return args_str
    except Exception:
        return {}


async def _execute_tool_call(tool_call: dict) -> dict:
    """执行单个 tool_call，返回结果字符串。"""
    func_name = tool_call["function"]["name"]
    handler = _TOOL_HANDLERS.get(func_name)
    if not handler:
        return json.dumps({"ok": False, "error": f"未知工具: {func_name}"})
    args = _resolve_tool_args(tool_call)
    try:
        result = handler(**args)
        # 如果是 coroutine，要 await
        import asyncio
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, dict):
            return json.dumps(result)
        return str(result)
    except TypeError:
        # 参数不匹配，尝试无参数调用
        try:
            result = handler()
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, dict):
                return json.dumps(result)
            return str(result)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})



async def _llm_chat_with_tools(
    messages: list[dict],
    llm_cfg: dict,
    parent_id: str,
) -> tuple[str, list[dict]]:
    """带工具调用的 LLM 对话循环。

    Returns:
        (final_text, all_messages): 最终回复文本，以及包含 tool_calls 的完整消息历史
    """
    import httpx

    base_url = (llm_cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        return "LLM 未配置", messages
    api_key = llm_cfg.get("api_key") or "ollama"
    model = llm_cfg.get("model") or "qwen2.5:7b"
    temperature = float(llm_cfg.get("temperature") or 0.2)
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    max_iterations = 10  # 防止死循环
    for iteration in range(max_iterations):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "tools": TOOLS,
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code != 200:
                    logger.warning("LLM tool-call 失败 status={} body={}", r.status_code, r.text[:200])
                    return f"LLM 调用失败（{r.status_code}）", messages
                data = r.json()
                msg = data["choices"][0]["message"]
                messages.append(msg)

                # 检查是否需要调用工具
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    # 最终回复
                    return (msg.get("content") or "").strip(), messages

                # 执行所有 tool_calls（串行）
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    logger.info("LLM 调用工具: {} (parent_id={})", func_name, parent_id)
                    tool_result = await _execute_tool_call(tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": func_name,
                        "content": tool_result,
                    })
        except Exception as e:
            logger.warning("LLM tool-call 异常: {}", e)
            return f"LLM 调用异常: {e}", messages

    return "工具调用超出最大次数限制（可能存在循环）", messages


async def _llm_streaming_chat(
    messages: list[dict],
    llm_cfg: dict,
):
    """流式调用 LLM，yield 每个 token。"""
    import httpx

    base_url = (llm_cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        return
    api_key = llm_cfg.get("api_key") or "ollama"
    model = llm_cfg.get("model") or "qwen2.5:7b"
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(llm_cfg.get("temperature") or 0.2),
        "stream": True,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as r:
                if r.status_code != 200:
                    return
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        delta = json.loads(data_str)
                    except Exception:
                        continue
                    delta_content = delta.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                    if delta_content:
                        yield delta_content
    except Exception as e:
        logger.warning("LLM 流式调用异常: {}", e)
        return


def _safe_parse_json(text: str) -> Optional[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# Agent 工具：消息优化 + 历史管理
# ---------------------------------------------------------------------------


async def _optimize_message(raw: str) -> str:
    """Agent 的核心能力之一：让用户的话更有逻辑性。

    用 LLM 重写（更结构化），无 LLM 时做轻量整理。
    """
    raw = (raw or "").strip()
    if not raw:
        return raw

    cfg = await runtime_settings.get_llm_config()
    if not (cfg.get("provider") and cfg.get("model")):
        # 无 LLM：保持原文
        return raw

    try:
        import httpx

        base_url = (cfg.get("base_url") or "").rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = base_url + "/v1"
        api_key = cfg.get("api_key") or "ollama"
        model = cfg.get("model") or "qwen2.5:7b"

        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一个消息规范化助手。任务：把用户可能模糊、口语化、不完整的需求描述，"
                        "重写得更清晰、更有逻辑性、补全隐含的背景。\n"
                        "规则：\n"
                        "1. 保留用户原意，不改变核心诉求\n"
                        "2. 补全合理的目标和验收点（如果能从原文推断）\n"
                        "3. 保持简洁，1-3 句话\n"
                        "4. 不要回答用户的问题，只规范化描述\n"
                        "5. 输出 JSON：{\"optimized\": \"...\"}"
                    ),
                },
                {"role": "user", "content": raw},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                return raw
            content = r.json()["choices"][0]["message"]["content"]
            parsed = _safe_parse_json(content)
            if parsed and "optimized" in parsed:
                return parsed["optimized"].strip()
    except Exception as e:
        logger.warning("消息优化失败: {}", e)
    return raw


# ---------------------------------------------------------------------------
# Agent 主入口
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """你是一个 AI 项目管理助手。

【职责】
1. **理解用户意图**（你看到的消息已经被 Agent 优化过，比原文更结构化）
2. **决定是否需要创建任务**：
   - 闲聊/咨询/能力介绍 → 直接回复（action=chat）
   - 描述了一个具体要做的事情 → 询问用户是否要创建任务（action=ask_to_create）
3. 你的回复会**直接显示给用户**。

【输出格式（必须 JSON）】
{
  "action": "chat" | "ask_to_create",
  "text": "你的回复内容（直接显示给用户，简洁自然，1-3 句话）",
  "optimized_intent": "对用户需求的更准确理解（可选）",
  "params": { "title": "...", "description": "..." }  // 仅 ask_to_create
}

【action=chat】直接回复
{
  "action": "chat",
  "text": "我是 AI 助手，能帮你..."
}

【action=ask_to_create】询问用户是否要创建任务
- 你需要先**询问**用户是否要创建（text 里体现"是否/可以/要不要"）
- 给出你的"建议拆解"（不必给出完整 work_items，Agent 会自动拆）
- params 里给出**建议的 title 和 description**
{
  "action": "ask_to_create",
  "text": "我帮您创建这个任务：《会记账的微信小程序》。开始拆解吗？",
  "params": {
    "title": "会记账的微信小程序",
    "description": "用户需要构建一个支持日常开支记录和按月统计的微信小程序，含登录、记账、统计、报表四大功能"
  }
}
"""


@dataclass
class ChatReply:
    text: str
    parent_id: Optional[str] = None
    intent: str = "chat"  # "chat" | "ask_to_create" | "tool"
    plan: Optional[dict[str, Any]] = None  # 任务参数（ask_to_create 时是建议参数）


# ---------------------------------------------------------------------------
# 历史 + 保存
# ---------------------------------------------------------------------------


async def _get_history(parent_id: Optional[str]) -> list[dict]:
    if not parent_id:
        return []
    async with session_scope() as s:
        items = await repo.list_conversations(s, parent_id, limit=20)
    out = []
    for it in items:
        if it.role.value == "boss":
            out.append({"role": "user", "content": it.content})
        else:
            out.append({"role": "assistant", "content": it.content})
    return out


async def _save_chat(
    parent_id: str,
    role: str,
    content: str,
    data: Optional[dict[str, Any]] = None,
) -> None:
    async with session_scope() as s:
        await repo.add_conversation(
            s,
            ConversationRole(role),
            content,
            parent_id=parent_id,
            data=data,
        )


async def _get_pending_task_params(parent_id: str) -> Optional[dict]:
    """取最近一条 ask_to_create 的 params。"""
    async with session_scope() as s:
        items = await repo.list_conversations(s, parent_id, limit=20)
    for it in reversed(items):
        if it.role.value == "project_manager" and it.data and "pending_task" in it.data:
            return it.data["pending_task"]
    return None


# ---------------------------------------------------------------------------
# 任务 CRUD 工具（实际执行）
# ---------------------------------------------------------------------------


async def _tool_create_task(title: str, description: str, parent_id_hint: Optional[str] = None) -> dict:
    """创建任务 + 拆解工作项 + 入调度队列。所有 ORM 操作在同一 session 内完成。"""
    from app.pm import planner

    if not title:
        return {"ok": False, "error": "title 必填"}
    plan = await planner.make_plan(title, description)
    try:
        async with session_scope() as s:
            # 找到或创建 parent
            if parent_id_hint:
                parent = await repo.get_parent_task(s, parent_id_hint)
            else:
                parent = None
            if not parent:
                parent = await repo.create_parent_task(
                    s,
                    title=title,
                    description=description,
                    plan=plan,
                    status=ParentTaskStatus.DRAFT,
                )
            parent_id = parent.id
            await repo.add_log(
                s, f"任务已创建（{len(plan.get('work_items', []))} 个工作项）", parent_id=parent_id
            )
            # 在 session 内创建工作项
            work_items_data = []
            for item in plan.get("work_items", []):
                wt = await repo.create_workflow_task(
                    s,
                    parent_id=parent_id,
                    title=item.get("title", "未命名"),
                    description=item.get("description", ""),
                    priority=int(item.get("priority", 5)),
                    inputs=item.get("inputs"),
                )
                work_items_data.append({"id": wt.id, "title": wt.title})
            # 在 session 内取 parent 详情（避免 detached）
            result = {
                "id": parent_id,
                "title": title,
                "description": description,
                "status": parent.status.value,
                "work_items": work_items_data,
                "plan": plan,
            }
    except Exception as e:
        logger.exception("create_task 失败")
        return {"ok": False, "error": str(e)}

    # 出 session 后入队
    for w in work_items_data:
        await workflow_queue.put(WorkflowQueueItem(workflow_id=w["id"], parent_id=parent_id))
    await event_bus.publish(Events.PARENT_CREATED, {"parent_id": parent_id, "title": title})
    return {"ok": True, "data": result}


async def _tool_list_tasks() -> dict:
    async with session_scope() as s:
        parents = await repo.list_parent_tasks(s)
    items = [
        {
            "id": p.id,
            "title": p.title,
            "status": p.status.value,
            "work_items": len(p.workflow_tasks or []),
        }
        for p in parents[:50]
    ]
    return {"ok": True, "data": items}


async def _tool_get_task(task_id: str) -> dict:
    async with session_scope() as s:
        p = await repo.get_parent_task(s, task_id)
        if not p:
            return {"ok": False, "error": "任务不存在"}
        # 在 session 内序列化
        data = {
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "status": p.status.value,
            "work_items": [
                {"id": w.id, "title": w.title, "status": w.status.value, "progress": w.progress}
                for w in (p.workflow_tasks or [])
            ],
        }
    return {"ok": True, "data": data}


async def _tool_delete_task(task_id: str) -> dict:
    if not task_id:
        return {"ok": False, "error": "id 必填"}
    async with session_scope() as s:
        p = await repo.get_parent_task(s, task_id)
        if not p:
            return {"ok": False, "error": "任务不存在"}
        await s.delete(p)
    await event_bus.publish(Events.PARENT_STATUS, {"parent_id": task_id, "status": "deleted"})
    return {"ok": True, "data": {"id": task_id, "deleted": True}}


async def _tool_start_task(task_id: str) -> dict:
    if not task_id:
        return {"ok": False, "error": "id 必填"}
    async with session_scope() as s:
        p = await repo.get_parent_task(s, task_id)
        if not p:
            return {"ok": False, "error": "任务不存在"}
        if p.status in (ParentTaskStatus.IN_PROGRESS, ParentTaskStatus.COMPLETED):
            return {"ok": False, "error": f"任务当前状态 {p.status.value}，无法启动"}
        await repo.update_parent_status(s, task_id, ParentTaskStatus.CONFIRMED)
        await repo.add_log(s, "通过工具启动任务", parent_id=task_id)
        for w in p.workflow_tasks or []:
            if w.status in (WorkflowTaskStatus.CREATED, WorkflowTaskStatus.FAILED):
                await workflow_queue.put(WorkflowQueueItem(workflow_id=w.id, parent_id=task_id))
    await scheduler_queue.put(SchedulerQueueItem(parent_id=task_id))
    await event_bus.publish(Events.PARENT_STATUS, {"parent_id": task_id, "status": "in_progress"})
    return {"ok": True, "data": {"id": task_id, "started": True}}


async def _tool_stop_task(task_id: str) -> dict:
    if not task_id:
        return {"ok": False, "error": "id 必填"}
    async with session_scope() as s:
        p = await repo.get_parent_task(s, task_id)
        if not p:
            return {"ok": False, "error": "任务不存在"}
        await repo.update_parent_status(s, task_id, ParentTaskStatus.FAILED)
        await repo.add_log(s, "通过工具停止任务", parent_id=task_id)
        for w in p.workflow_tasks or []:
            if w.status == WorkflowTaskStatus.IN_PROGRESS:
                await repo.update_workflow_status(s, w.id, status=WorkflowTaskStatus.CANCELLED)
    await event_bus.publish(Events.PARENT_STATUS, {"parent_id": task_id, "status": "failed"})
    return {"ok": True, "data": {"id": task_id, "stopped": True}}


async def _tool_pause_task(task_id: str) -> dict:
    if not task_id:
        return {"ok": False, "error": "id 必填"}
    async with session_scope() as s:
        p = await repo.get_parent_task(s, task_id)
        if not p:
            return {"ok": False, "error": "任务不存在"}
        if p.status != ParentTaskStatus.IN_PROGRESS:
            return {"ok": False, "error": f"任务未在执行中（{p.status.value}）"}
        await repo.update_parent_status(s, task_id, ParentTaskStatus.BLOCKED)
        await repo.add_log(s, "通过工具暂停任务", parent_id=task_id)
    await event_bus.publish(Events.PARENT_STATUS, {"parent_id": task_id, "status": "blocked"})
    return {"ok": True, "data": {"id": task_id, "paused": True}}


async def _tool_resume_task(task_id: str) -> dict:
    if not task_id:
        return {"ok": False, "error": "id 必填"}
    async with session_scope() as s:
        p = await repo.get_parent_task(s, task_id)
        if not p:
            return {"ok": False, "error": "任务不存在"}
        if p.status != ParentTaskStatus.BLOCKED:
            return {"ok": False, "error": f"任务未暂停（{p.status.value}）"}
        await repo.update_parent_status(s, task_id, ParentTaskStatus.IN_PROGRESS)
        await repo.add_log(s, "通过工具继续任务", parent_id=task_id)
        for w in p.workflow_tasks or []:
            if w.status in (WorkflowTaskStatus.CREATED, WorkflowTaskStatus.FAILED):
                await workflow_queue.put(WorkflowQueueItem(workflow_id=w.id, parent_id=task_id))
    await event_bus.publish(Events.PARENT_STATUS, {"parent_id": task_id, "status": "in_progress"})
    return {"ok": True, "data": {"id": task_id, "resumed": True}}


async def _tool_get_logs(task_id: str, limit: int = 50) -> dict:
    async with session_scope() as s:
        logs = await repo.list_logs(s, parent_id=task_id, limit=limit)
    return {
        "ok": True,
        "data": [
            {"level": l.level.value, "message": l.message, "created_at": l.created_at.isoformat()}
            for l in logs
        ],
    }


async def _tool_list_experts() -> dict:
    from app.agents.pool import expert_pool
    return {"ok": True, "data": expert_pool.types()}




# ---------------------------------------------------------------------------
# 注册所有工具（在所有 _tool_* 函数定义之后）
# ---------------------------------------------------------------------------
_register_tool("list_tasks", _tool_list_tasks)
_register_tool("get_task", _tool_get_task)
_register_tool("create_task", _tool_create_task)
_register_tool("start_task", _tool_start_task)
_register_tool("pause_task", _tool_pause_task)
_register_tool("resume_task", _tool_resume_task)
_register_tool("stop_task", _tool_stop_task)
_register_tool("delete_task", _tool_delete_task)
_register_tool("get_task_logs", _tool_get_logs)
_register_tool("list_experts", _tool_list_experts)
# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _looks_like_confirm(message: str) -> bool:
    m = (message or "").strip().lower()
    if not m:
        return False
    return any(k in m for k in ("确认", "可以", "开工", "开始", "ok", "好", "同意", "yes", "y", "创建吧", "建吧")) and len(m) <= 12


def _looks_like_cancel(message: str) -> bool:
    m = (message or "").strip().lower()
    return any(k in m for k in ("取消", "不要", "算了", "no", "n", "算了"))


async def _create_chat_parent(text: str) -> str:
    async with session_scope() as s:
        p = await repo.create_parent_task(
            s,
            title=text[:30] or "对话",
            description=text,
            plan={"work_items": []},
            status=ParentTaskStatus.DRAFT,
        )
    await event_bus.publish(Events.PARENT_CREATED, {"parent_id": p.id, "title": p.title})
    return p.id


async def chat(message: str, parent_id: Optional[str] = None) -> ChatReply:
    """主入口：所有聊天内容由 LLM 决定。

    流程：
    1. Agent 优化消息（让用户的话更逻辑性）
    2. 优化后的消息 + 历史 → LLM
    3. LLM 决定 action=chat 或 action=ask_to_create
    4. 若是 ask_to_create，不立即调工具，等用户确认
    """
    raw = (message or "").strip()
    if not raw:
        return ChatReply(text="需要我帮您做什么？", intent="chat")

    # 1) 优化消息
    optimized = await _optimize_message(raw)
    logger.info("原始: {!r} → 优化: {!r}", raw[:50], optimized[:50])

    # 2) 没有 parent_id 就建一个
    if not parent_id:
        parent_id = await _create_chat_parent(raw)

    # 3) 拉历史
    history = await _get_history(parent_id)

    # 4) 构造 messages（带优化后的描述）
    # history[-1] 就是刚 append 进去的用户消息，不要重复加
    system = SYSTEM_PROMPT
    messages = [{"role": "system", "content": system}] + history

    # 5) 调 LLM（带工具调用）
    llm_cfg = await runtime_settings.get_llm_config()
    final_text = "（LLM 不可用）"
    if llm_cfg.get("provider") and llm_cfg.get("model"):
        final_text, messages = await _llm_chat_with_tools(messages, llm_cfg, parent_id or "")
    else:
        fallback_reply = await _fallback_no_llm(raw, parent_id)
        if isinstance(fallback_reply, ChatReply):
            final_text = fallback_reply.text
        else:
            final_text = fallback_reply

    # 6) 保存用户消息
    await _save_chat(parent_id, "boss", raw, data={"optimized": optimized})
    await _save_chat(parent_id, "project_manager", final_text)
    await event_bus.publish(Events.CHAT_MESSAGE, {
        "parent_id": parent_id,
        "role": "project_manager",
        "content": final_text,
        "intent": "chat",
    })
    return ChatReply(text=final_text, parent_id=parent_id, intent="chat")


# ---------------------------------------------------------------------------
# 确认创建任务（用户点"确认"按钮触发）
# ---------------------------------------------------------------------------


async def confirm_create_task(parent_id: str) -> ChatReply:
    """用户确认创建任务：取最近 ask_to_create 的 params，调用 create_task 工具。"""
    params = await _get_pending_task_params(parent_id)
    if not params:
        return ChatReply(text="没有待创建的任务。", parent_id=parent_id, intent="chat")

    title = params.get("title", "")
    description = params.get("description", "")
    result = await _tool_create_task(title, description, parent_id_hint=parent_id)
    if not result.get("ok"):
        text = f"创建失败：{result.get('error', '未知错误')}"
        await _save_chat(parent_id, "project_manager", text)
        return ChatReply(text=text, parent_id=parent_id, intent="chat")

    data = result["data"]
    wb_count = len(data.get("work_items", []))
    # 列出工作项
    wb_lines = [f"  {i+1}. {w['title']}" for i, w in enumerate(data["work_items"])]
    text = (
        f"已创建任务《{data['title']}》，拆出 {wb_count} 个工作项：\n"
        + "\n".join(wb_lines)
        + "\n\n确认开工吗？"
    )
    # 标记 ask_to_create（再次确认开工）
    await _save_chat(
        parent_id,
        "project_manager",
        text,
        data={"pending_start": data["id"], "action": "ask_to_start", "task": data},
    )
    await event_bus.publish(Events.CHAT_MESSAGE, {
        "parent_id": parent_id,
        "role": "project_manager",
        "content": text,
        "intent": "ask_to_start",
        "data": data,
    })
    return ChatReply(text=text, parent_id=parent_id, intent="ask_to_start", plan=data)


async def confirm_start_task(parent_id: str) -> ChatReply:
    """用户确认开工：把已创建的任务投入调度。"""
    async with session_scope() as s:
        items = await repo.list_conversations(s, parent_id, limit=20)
    pending = None
    for it in reversed(items):
        if it.role.value == "project_manager" and it.data and "pending_start" in it.data:
            pending = it.data["pending_start"]
            break
    if not pending:
        return ChatReply(text="没有待开工的任务。", parent_id=parent_id, intent="chat")

    result = await _tool_start_task(pending)
    if not result.get("ok"):
        text = f"启动失败：{result.get('error', '未知错误')}"
        await _save_chat(parent_id, "project_manager", text)
        return ChatReply(text=text, parent_id=parent_id, intent="chat")

    text = "已开工！专家团队开始执行，实时进度会推送到看板。"
    await _save_chat(parent_id, "project_manager", text)
    await event_bus.publish(Events.CHAT_MESSAGE, {
        "parent_id": parent_id,
        "role": "project_manager",
        "content": text,
        "intent": "chat",
    })
    return ChatReply(text=text, parent_id=parent_id, intent="chat")


# ---------------------------------------------------------------------------
# 规则回退（无 LLM 时）
# ---------------------------------------------------------------------------


async def _fallback_no_llm(raw: str, parent_id: str) -> ChatReply:
    """没配 LLM 时，简单规则：
    - 短消息/问候/问句 → 直接回复
    - 其他 → 建议创建（不调工具，等用户确认）
    """
    await _save_chat(parent_id, "boss", raw)

    msg = raw.strip()
    if any(k in msg for k in ("你能做什么", "有什么功能", "功能介绍", "怎么用", "如何使用", "你会什么", "有什么能力")):
        text = (
            "我是 AI 项目管理助手。可以：\n"
            "• 优化你的需求描述\n"
            "• 拆解任务并调度专家团队（需求/设计/开发/测试/评审）\n"
            "• CRUD 任务（创建/查询/删除/启动/停止/暂停/继续）\n"
            "• 跟踪进度\n\n"
            "想配 LLM 的话，点右上角头像→系统设置。"
        )
    elif any(k in msg for k in ("你好", "hi", "hello", "在吗")):
        text = "你好！需要我做什么？"
    elif any(k in msg for k in ("谢谢", "thanks")):
        text = "不客气！"
    elif any(k in msg for k in ("你是", "你叫")):
        text = "我是 AI 项目管理助手，由 LLM 驱动。"
    elif len(msg) <= 6 or msg.endswith("?") or msg.endswith("？") or msg.endswith("吗"):
        text = "这个我不太确定。你可以直接说想做什么，我来帮你拆解。"
    else:
        # 任务描述：询问用户是否要创建
        text = f"我帮您创建任务吗？\n\n描述：{msg[:80]}{'...' if len(msg) > 80 else ''}"
        await _save_chat(
            parent_id, "project_manager", text,
            data={
                "pending_task": {"title": msg[:30] or "新任务", "description": msg},
                "action": "ask_to_create",
            },
        )
        await event_bus.publish(Events.CHAT_MESSAGE, {
            "parent_id": parent_id, "role": "project_manager", "content": text, "intent": "ask_to_create",
            "params": {"title": msg[:30], "description": msg},
        })
        return ChatReply(text=text, parent_id=parent_id, intent="ask_to_create", plan={"title": msg[:30], "description": msg})

    await _save_chat(parent_id, "project_manager", text)
    await event_bus.publish(Events.CHAT_MESSAGE, {
        "parent_id": parent_id, "role": "project_manager", "content": text, "intent": "chat",
    })
    return ChatReply(text=text, parent_id=parent_id, intent="chat")
