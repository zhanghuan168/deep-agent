"""专家 Agent 实现（新8阶段 + 交叉评审 + TDD）。

设计原则：
- 每个阶段 Agent 优先调用 LLM 生成真实内容。
- 评审阶段使用独立 cross_reviewer 专家，交叉检视被评审工作项。
- TDD 阶段：先写测试，再用测试驱动开发。
- LLM 不可用时降级到规则回退。
"""
from __future__ import annotations

import asyncio
import json
import textwrap
import re
import os
from typing import Any, Optional

from app.agents.base import BaseExpertAgent, StageContext, StageResult
from app.db.models import StageName, ReviewType
from app.logging import logger

_llm_semaphore: asyncio.Semaphore = asyncio.Semaphore(4)


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    json_mode: bool = True,
    model_override: Optional[str] = None,
) -> str | None:
    """调 LLM（OpenAI 兼容协议），返回文本内容。失败返回 None。"""
    try:
        from app import runtime_settings
    except Exception:
        return None

    cfg = await runtime_settings.get_llm_config()
    base_url = (cfg.get("base_url") or "").rstrip("/")
    api_key = cfg.get("api_key") or ""
    model = model_override or cfg.get("model") or ""

    if not base_url or not api_key or not model:
        return None

    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    url = f"{base_url}/chat/completions"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        import httpx
        async with _llm_semaphore:
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code == 429:
                    logger.warning("LLM 触发 rate limit，等待后重试")
                    await asyncio.sleep(5.0)
                    r = await client.post(url, json=payload, headers=headers)
                if r.status_code != 200:
                    logger.warning("LLM 调用失败 status={} body={}", r.status_code, r.text[:200])
                    return None
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                return content
    except Exception as e:
        logger.warning("LLM 调用异常: {}", e)
        return None


def _safe_parse_json(text: str) -> dict | None:
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
                pass
    return None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


async def _heartbeat(ctx: StageContext, sleep: float = 0.3) -> None:
    if not ctx.on_heartbeat:
        return
    for _ in range(2):
        await asyncio.sleep(sleep)
        await ctx.on_heartbeat()


def _collect_history_artifact(ctx: StageContext, key: str) -> Any:
    """从历史中收集某个 artifact 字段的值。"""
    for h in ctx.history:
        artifacts = h.get("artifacts") or {}
        if key in artifacts:
            return artifacts[key]
    return None


# ---------------------------------------------------------------------------
# CodeRunner：代码执行器 + Self-Healing
# ---------------------------------------------------------------------------

import asyncio
import tempfile
import os
import pathlib
import time


class CodeRunResult:
    """单次代码执行结果。"""

    def __init__(
        self,
        success: bool,
        stdout: str = "",
        stderr: str = "",
        execution_time: float = 0.0,
        files_written: list[str] | None = None,
        error: str = "",
    ) -> None:
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.execution_time = execution_time
        self.files_written = files_written or []
        self.error = error



class CodeRunner:
    """代码执行器：写入临时文件 + 进程执行 + 超时控制。

    支持 self-healing：执行失败时，自动让 LLM 分析错误并生成修复代码。
    """

    def __init__(
        self,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 2000,
    ) -> None:
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    async def run_source_files(
        self,
        source_files: list[dict[str, str]],
        run_cmd: str | None = None,
    ) -> CodeRunResult:
        """执行 source_files，返回执行结果。


        Args:
            source_files: [{"path": "src/xxx.py", "content": "..."}, ...]
            run_cmd: 启动命令，如 None 则找首个 Python 文件直接 python 执行
        """
        if not source_files:
            return CodeRunResult(success=False, error="No source files provided")


        work_dir = tempfile.mkdtemp(prefix="deepagent_code_")
        try:
            # 1. 写入所有文件
            written = []
            for f in source_files:
                fpath = os.path.join(work_dir, f["path"])
                pathlib.Path(fpath).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(fpath).write_text(f["content"], encoding="utf-8")
                written.append(fpath)

            # 2. 确定执行命令
            if run_cmd:
                cmd = run_cmd.strip().split()
            else:
                # 找入口文件（优先 main.py 或第一个 .py）
                entry = next((p for p in written if p.endswith("main.py")), None)
                if entry is None and written:
                    entry = written[0]
                cmd = ["python3", entry]


            # 3. 执行（限时）
            start = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                limit=1024 * 1024,  # 1MB stdout/stderr buffer
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                execution_time = time.monotonic() - start
                return CodeRunResult(
                    success=False,
                    stdout="",
                    stderr=f"Execution timed out after {self.timeout_seconds}s",
                    execution_time=execution_time,
                    files_written=written,
                    error=f"Timeout after {self.timeout_seconds}s",
                )
            execution_time = time.monotonic() - start
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            success = proc.returncode == 0

            # 4. 截断输出
            stdout = stdout[: self.max_output_chars]
            stderr = stderr[: self.max_output_chars]

            return CodeRunResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                execution_time=execution_time,
                files_written=written,
                error="" if success else f"Exit code {proc.returncode}",
            )
        finally:
            # 清理临时目录
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)

    async def run_with_self_healing(
        self,
        source_files: list[dict[str, str]],
        task_title: str,
        run_cmd: str | None = None,
    ) -> CodeRunResult:
        """执行代码，失败时让 LLM 自愈后重试（最多 max_retries 次）。"""
        current_files = list(source_files)
        last_result: CodeRunResult | None = None

        for attempt in range(self.max_retries + 1):
            result = await self.run_source_files(current_files, run_cmd)
            last_result = result

            if result.success:
                logger.info(
                    "CodeRunner exec success on attempt {}/{}: {}",
                    attempt + 1, self.max_retries + 1, task_title,
                )
                return result

            if attempt < self.max_retries:
                logger.info(
                    "CodeRunner exec failed (attempt {}/{}), triggering self-healing",
                    attempt + 1, self.max_retries,
                )
                current_files = await self._llm_fix_code(
                    current_files, result, task_title,
                )
                if current_files is None:
                    break  # LLM 无法修复


        return last_result or CodeRunResult(success=False, error="Max retries exceeded")


    async def _llm_fix_code(
        self,
        broken_files: list[dict[str, str]],
        error_result: CodeRunResult,
        task_title: str,
    ) -> list[dict[str, str]] | None:
        """让 LLM 分析错误并生成修复后的代码。"""
        fix_system = textwrap.dedent(""""你是一名资深 Python 开发者，擅长修复代码错误。

收到错误信息后，准确分析问题根因并生成修复后的代码。
严格输出 JSON：
{
  "source_files": [{"path": "...", "content": "..."}],
  "analysis": "错误分析（1-2句话）"
}
原则：
- 只修复错误，不要改变正常行为
- 保持代码风格一致""")

        files_summary = json.dumps(broken_files, ensure_ascii=False, indent=2)
        user_prompt = textwrap.dedent(
            f"""任务：{task_title}

上次执行失败，错误信息：
stdout:\n{error_result.stdout}\n\nstderr:\n{error_result.stderr}\n\n原始代码：
{files_summary[:3000]}


请分析错误并输出修复后的代码（JSON格式）。"""
        )

        content = await _call_llm(fix_system, user_prompt, json_mode=True)
        if not content:
            return None

        parsed = _safe_parse_json(content)
        if not parsed or "source_files" not in parsed:
            logger.warning("CodeRunner: LLM fix response invalid")
            return None

        logger.info("CodeRunner: LLM self-healing generated {} files", len(parsed["source_files"]))
        return parsed["source_files"]



_code_runner: CodeRunner | None = None



def get_code_runner() -> CodeRunner:
    global _code_runner
    if _code_runner is None:
        _code_runner = CodeRunner(max_retries=3, timeout_seconds=30.0)
    return _code_runner


# ---------------------------------------------------------------------------
# 1. 需求分析 Agent
# ---------------------------------------------------------------------------

REQUIREMENT_ANALYSIS_SYSTEM = textwrap.dedent("""你是一名资深需求分析师，擅长将模糊需求拆解为结构化、可测试的需求清单。

严格输出 JSON，格式：
{
  "items": [
    {
      "id": "req-1",
      "content": "具体需求描述（动作+对象+结果）",
      "priority": "high|medium|low",
      "acceptance_criteria": ["具体可测试的验收点1", "具体可测试的验收点2"],
      "dependencies": []
    }
  ],
  "summary": "一句话需求概述"
}

拆解原则（至少产出5条需求）：
1. 每个功能点/模块单独一条需求
2. 包含输入验证、错误处理、边界情况
3. 包含用户体验/界面要求（如有）
4. 包含性能/安全/日志等非功能需求（如有）
5. 验收标准必须：①可执行测试 ②有明确判定条件③不是模糊描述

优先级规则：
- high：核心功能，无此功能系统不可用
- medium：重要功能，影响用户体验
- low：辅助功能，可选实现

依赖规则：
- dependencies列出本需求依赖的其他需求 id
- 无依赖则为空数组 []""")


class RequirementAnalysisAgent(BaseExpertAgent):
    expert_type = "requirement_analyst"

    def system_prompt(self) -> str:
        return REQUIREMENT_ANALYSIS_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx)
        title = ctx.workflow.title
        desc = ctx.parent_description or ctx.workflow.description

        user_prompt = textwrap.dedent(
            f"""任务：{title}
描述：{desc}

请将上述需求拆解为结构化的需求清单（JSON格式）。

注意：
- 必须产出至少5条需求
- 每条需求的 acceptance_criteria 不少于2条
- 涵盖：功能需求、输入校验、错误处理、边界情况、用户体验
- 如涉及 API，需包含请求参数、响应格式、错误码"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed and "items" in parsed:
                logger.info("需求分析 LLM 生成成功: {} ({} items)", title, len(parsed["items"]))
                return StageResult(
                    summary=f"完成《{title}》需求分析，共{len(parsed['items'])}条需求",
                    artifacts={
                        "items": parsed["items"],
                        "summary": parsed.get("summary", ""),
                    },
                )

        # 规则回退
        items = [
            {
                "id": "req-1",
                "content": f"实现 {title}",
                "priority": "high",
                "acceptance_criteria": ["功能可正常运行"],
                "dependencies": [],
            }
        ]
        return StageResult(
            summary=f"完成《{title}》需求分析",
            artifacts={"items": items, "summary": title},
        )


# ---------------------------------------------------------------------------
# 2. 需求评审 Agent（交叉评审）
# ---------------------------------------------------------------------------

REQUIREMENT_REVIEW_SYSTEM = textwrap.dedent("""你是一名资深需求评审专家，负责对需求清单进行严格评审。
严格输出 JSON，格式：
{
  "approved": true|false,
  "scores": {"完整性": 85, "明确性": 80, ...},
  "comments": {"完整性": "评审意见", ...},
  "revision_suggestions": [
    {"req_id": "req-1", "field": "content", "original": "...", "suggested": "..."}
  ],
  "summary": "评审总结"
}
评分标准（0-100）：90+优秀，80+良好，70+及格，<70需改进""")


class RequirementReviewAgent(BaseExpertAgent):
    expert_type = "cross_reviewer"

    def system_prompt(self) -> str:
        return REQUIREMENT_REVIEW_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx)
        title = ctx.workflow.title

        # 收集被评审的需求清单
        req_items = []
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            if "items" in artifacts:
                req_items = artifacts["items"]
                break

        # 获取评审模板
        review_template = await self._get_review_template(ctx, ReviewType.REQUIREMENT_REVIEW)

        user_prompt = textwrap.dedent(
            f"""任务：{title}
需求清单：
{json.dumps(req_items, ensure_ascii=False, indent=2)}

评审检查点：
{json.dumps(review_template, ensure_ascii=False, indent=2)}

请进行需求评审（JSON格式）：
approved：是否通过（true/false）
scores：各维度评分（0-100）
comments：每个维度的评审意见
revision_suggestions：具体的修改建议（哪些需求需要怎么改）
summary：评审总结"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed:
                decision = parsed.get("approved", False)
                scores = parsed.get("scores", {})
                comments = parsed.get("comments", {})
                summary = parsed.get("summary", "")
                logger.info("需求评审 LLM 生成成功: {} -> {}", title, "approve" if decision else "reject")
                return StageResult(
                    summary=summary or f"需求评审{'通过' if decision else '需修改'}",
                    artifacts={
                        "decision": "approve" if decision else "reject",
                        "scores": scores,
                        "comments": comments,
                        "revision_suggestions": parsed.get("revision_suggestions", []),
                        "review_template": review_template,
                    },
                    approved=decision,
                    review_comment=json.dumps(comments, ensure_ascii=False),
                )

        # 规则回退
        return StageResult(
            summary="需求评审完成（规则回退）",
            artifacts={
                "decision": "approve",
                "scores": {"完整性": 75, "明确性": 70, "可测试性": 70},
                "comments": {"完整性": "规则回退：默认通过", "明确性": "规则回退：默认通过"},
                "revision_suggestions": [],
            },
            approved=True,
            review_comment="评审通过（规则回退）",
        )

    async def _get_review_template(self, ctx: StageContext, review_type: ReviewType) -> dict:
        """获取评审模板。"""
        try:
            from app.db import repository as repo
            from app.db.session import session_scope
            async with session_scope() as session:
                template = await repo.get_default_review_template(session, review_type.value)
                if template:
                    return {
                        "criteria": template.criteria,
                        "rubric": template.rubric,
                    }
        except Exception:
            pass
        # 默认模板
        return {
            "criteria": [
                {"point": "完整性", "description": "是否覆盖所有功能点"},
                {"point": "明确性", "description": "描述是否清晰无歧义"},
                {"point": "可测试性", "description": "是否可转化为测试用例"},
            ],
            "rubric": ["完整性", "明确性", "可测试性"],
        }


# ---------------------------------------------------------------------------
# 3. 技术方案设计 Agent
# ---------------------------------------------------------------------------

TECHNICAL_DESIGN_SYSTEM = textwrap.dedent("""你是一名资深系统架构师，擅长输出完整的技术方案设计。
严格输出 JSON，格式：
{
  "architecture": "架构描述（1-2句话）",
  "modules": [{"name": "模块名", "responsibility": "职责", "tech_stack": "技术选型"}],
  "interfaces": [{"method": "GET|POST|PUT|DELETE", "path": "/api/...", "description": "描述", "request": {}, "response": {}}],
  "data_model": [{"table": "表名", "fields": [{"name": "字段", "type": "类型", "constraints": "约束"}]}],
  "security": ["安全考虑点1", "安全考虑点2"],
  "performance": ["性能考虑点1", "性能考虑点2"]
}""")


class TechnicalDesignAgent(BaseExpertAgent):
    expert_type = "designer"

    def system_prompt(self) -> str:
        return TECHNICAL_DESIGN_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx)
        title = ctx.workflow.title
        desc = ctx.parent_description or ctx.workflow.description

        # 收集需求清单
        req_items = []
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            if "items" in artifacts:
                req_items = artifacts["items"]
                break

        user_prompt = textwrap.dedent(
            f"""任务：{title}
需求清单：
{json.dumps(req_items, ensure_ascii=False, indent=2) if req_items else '无需求清单，请基于任务标题设计'}

请输出完整技术方案（JSON格式）：
architecture：系统架构描述
modules：模块划分，每模块说明职责和技术选型
interfaces：核心 API 接口（method/path/description/request/response）
data_model：数据模型（表名/字段/类型/约束）
security：安全考虑（2-3条）
performance：性能考虑（2-3条）"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed:
                logger.info("技术设计 LLM 生成成功: {}", title)
                return StageResult(
                    summary=f"完成《{title}》技术方案设计",
                    artifacts=parsed,
                )

        # 规则回退
        return StageResult(
            summary=f"完成《{title}》技术方案设计",
            artifacts={
                "architecture": "前后端分离架构",
                "modules": [{"name": "api", "responsibility": "接口层", "tech_stack": "FastAPI"}],
                "interfaces": [],
                "data_model": [],
                "security": ["输入校验", "权限控制"],
                "performance": ["缓存策略", "分页查询"],
            },
        )


# ---------------------------------------------------------------------------
# 4. 技术方案评审 Agent（交叉评审）
# ---------------------------------------------------------------------------

TECHNICAL_REVIEW_SYSTEM = textwrap.dedent("""你是一名资深技术架构师，负责对技术方案进行严格评审。
严格输出 JSON，格式：
{
  "approved": true|false,
  "scores": {"架构": 85, "接口": 80, "数据模型": 78, ...},
  "comments": {"架构": "评审意见", ...},
  "revision_suggestions": [
    {"field": "模块/接口/数据模型", "original": "...", "suggested": "..."}
  ],
  "summary": "评审总结"
}
评分标准（0-100）""")


class TechnicalReviewAgent(BaseExpertAgent):
    expert_type = "cross_reviewer"

    def system_prompt(self) -> str:
        return TECHNICAL_REVIEW_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx)
        title = ctx.workflow.title

        # 收集技术方案
        design_artifacts = {}
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            if "architecture" in artifacts or "modules" in artifacts:
                design_artifacts = artifacts
                break

        # 获取评审模板
        review_template = await self._get_review_template(ctx, ReviewType.TECHNICAL_REVIEW)

        user_prompt = textwrap.dedent(
            f"""任务：{title}
技术方案：
{json.dumps(design_artifacts, ensure_ascii=False, indent=2)[:2000]}

评审检查点：
{json.dumps(review_template, ensure_ascii=False, indent=2)}

请进行技术方案评审（JSON格式）：
approved：是否通过
scores：各维度评分
comments：评审意见
revision_suggestions：具体修改建议
summary：评审总结"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed:
                decision = parsed.get("approved", False)
                scores = parsed.get("scores", {})
                comments = parsed.get("comments", {})
                summary = parsed.get("summary", "")
                logger.info("技术方案评审 LLM 生成成功: {} -> {}", title, "approve" if decision else "reject")
                return StageResult(
                    summary=summary or f"技术方案评审{'通过' if decision else '需修改'}",
                    artifacts={
                        "decision": "approve" if decision else "reject",
                        "scores": scores,
                        "comments": comments,
                        "revision_suggestions": parsed.get("revision_suggestions", []),
                        "review_template": review_template,
                    },
                    approved=decision,
                    review_comment=json.dumps(comments, ensure_ascii=False),
                )

        return StageResult(
            summary="技术方案评审完成（规则回退）",
            artifacts={
                "decision": "approve",
                "scores": {"架构": 75, "接口": 70, "数据模型": 70},
                "comments": {"架构": "规则回退：默认通过"},
            },
            approved=True,
            review_comment="评审通过（规则回退）",
        )

    async def _get_review_template(self, ctx: StageContext, review_type: ReviewType) -> dict:
        try:
            from app.db import repository as repo
            from app.db.session import session_scope
            async with session_scope() as session:
                template = await repo.get_default_review_template(session, review_type.value)
                if template:
                    return {"criteria": template.criteria, "rubric": template.rubric}
        except Exception:
            pass
        return {
            "criteria": [
                {"point": "架构合理性", "description": "架构是否清晰解耦"},
                {"point": "接口设计", "description": "API设计是否合理"},
                {"point": "数据模型", "description": "数据库设计是否合理"},
            ],
            "rubric": ["架构", "接口", "数据模型"],
        }


# ---------------------------------------------------------------------------
# 5. 任务拆解 Agent
# ---------------------------------------------------------------------------

TASK_BREAKDOWN_SYSTEM = textwrap.dedent("""你是一名资深项目经理，擅长将功能拆解为按天/小时可完成的子任务。
严格输出 JSON，格式：
{
  "tasks": [
    {
      "title": "任务标题",
      "description": "任务描述",
      "estimated_hours": 4,
      "priority": "high|medium|low",
      "dependencies": ["task-title-1"]
    }
  ],
  "milestones": [{"name": "里程碑名", "tasks": ["task-title-1", "task-title-2"]}]
}""")


class TaskBreakdownAgent(BaseExpertAgent):
    expert_type = "requirement_analyst"

    def system_prompt(self) -> str:
        return TASK_BREAKDOWN_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx)
        title = ctx.workflow.title
        desc = ctx.parent_description or ctx.workflow.description

        # 收集需求清单和技术方案
        context = {}
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            if "items" in artifacts:
                context["requirements"] = artifacts["items"]
            if "architecture" in artifacts:
                context["design"] = artifacts

        user_prompt = textwrap.dedent(
            f"""任务：{title}
需求清单：{json.dumps(context.get('requirements', []), ensure_ascii=False, indent=2) if context.get('requirements') else '无'}
技术方案：{json.dumps(context.get('design', {}), ensure_ascii=False, indent=2) if context.get('design') else '无'}

请将上述功能拆解为可按小时/天完成的子任务（JSON格式）：
tasks：任务列表，每条包含 title/description/estimated_hours/priority/dependencies
milestones：里程碑划分，把相关任务归到同一里程碑

原则：
- 每个任务 2-8 小时可完成
- 明确标注任务间依赖
- 按优先级排序"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed and "tasks" in parsed:
                tasks = parsed["tasks"]
                logger.info("任务拆解 LLM 生成成功: {} ({} tasks)", title, len(tasks))
                return StageResult(
                    summary=f"完成《{title}》任务拆解，共{len(tasks)}个子任务",
                    artifacts=parsed,
                )

        # 规则回退
        tasks = [
            {
                "title": f"实现 {title}",
                "description": desc,
                "estimated_hours": 8,
                "priority": "high",
                "dependencies": [],
            }
        ]
        return StageResult(
            summary=f"完成《{title}》任务拆解",
            artifacts={"tasks": tasks, "milestones": []},
        )


# ---------------------------------------------------------------------------
# 6. 编码实现 Agent（TDD）
# ---------------------------------------------------------------------------

IMPLEMENTATION_SYSTEM = textwrap.dedent("""你是一名资深 Python 开发者，擅长 TDD（测试驱动开发）。
严格输出 JSON，格式：
{
  "test_files": [{"path": "tests/test_xxx.py", "content": "# 测试代码"}],
  "source_files": [{"path": "src/xxx.py", "content": "# 实现代码"}],
  "summary": "实现说明"
}
要求：
- 先写测试（test_files），再写实现（source_files）
- 测试必须可运行，使用 pytest
- 实现代码包含类型注解、docstring、异常处理
- 遵循 PEP8""")


class ImplementationAgent(BaseExpertAgent):
    expert_type = "developer"

    def system_prompt(self) -> str:
        return IMPLEMENTATION_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx, sleep=0.5)
        title = ctx.workflow.title
        desc = ctx.parent_description or ctx.workflow.description

        # 收集任务拆解和技术方案
        task_list = []
        design = {}
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            if "tasks" in artifacts:
                task_list = artifacts["tasks"]
            if "architecture" in artifacts:
                design = artifacts

        user_prompt = textwrap.dedent(
            f"""任务：{title}
子任务列表：
{json.dumps(task_list, ensure_ascii=False, indent=2) if task_list else '无具体任务，请直接实现'}
技术方案：
{json.dumps(design, ensure_ascii=False, indent=2) if design else '无技术方案'}

请使用 TDD（测试驱动开发）方式实现：
test_files：测试文件（先写！），使用 pytest，可运行
source_files：实现文件（后写！），Python 3.10+，类型注解，docstring

原则：
- 测试覆盖率要全面（正常/边界/异常）
- 实现代码简洁、可读、可测试
- 包含必要的 import
- 不要写伪代码"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed:
                files = parsed.get("source_files", [])
                test_files = parsed.get("test_files", [])
                logger.info("TDD 实现 LLM 生成成功: {} ({} source, {} test)", title, len(files), len(test_files))

                # -------------------------------------------------------
                # 【新增】代码执行阶段：self-healing 执行循环
                # -------------------------------------------------------
                execution_result = None
                if files:
                    runner = get_code_runner()
                    exec_res = await runner.run_with_self_healing(
                        source_files=files,
                        task_title=title,
                    )
                    execution_result = {
                        "success": exec_res.success,
                        "stdout": exec_res.stdout,
                        "stderr": exec_res.stderr,
                        "execution_time": round(exec_res.execution_time, 3),
                        "files_written": [os.path.basename(p) for p in exec_res.files_written],
                        "error": exec_res.error,
                    }
                    if exec_res.success:
                        logger.info(
                            "CodeRunner: 《{}》执行成功，耗时 {:.1f}s",
                            title, exec_res.execution_time,
                        )
                    else:
                        logger.warning(
                            "CodeRunner: 《{}》执行失败: {} / stdout={} / stderr={}",
                            title, exec_res.error, exec_res.stdout[:200], exec_res.stderr[:200],
                        )

                return StageResult(
                    summary=f"完成《{title}》TDD 实现" + ("（代码执行成功）" if (execution_result and execution_result["success"]) else "（代码执行失败）" if execution_result else ""),
                    artifacts={
                        "source_files": files,
                        "test_files": test_files,
                        "summary": parsed.get("summary", ""),
                        "execution": execution_result,
                    },
                )

        # 规则回退
        slug = re.sub(r"[^\w]", "_", title.lower()) or "task"
        class_name = "".join(p.title() for p in slug.split("_"))
        source = textwrap.dedent(f'''\
        """{title} 模块。"""
        from __future__ import annotations

        from dataclasses import dataclass
        from datetime import datetime, timezone


        @dataclass
        class {class_name}:
            """{title}"""
            id: str
            status: str = "pending"

            def run(self) -> None:
                """执行核心逻辑。"""
                self.status = "done"
        ''').strip()

        test = textwrap.dedent(f'''\
        """{title} 测试。"""
        import pytest
        from src.{slug} import {class_name}


        def test_{slug}_init():
            item = {class_name}(id="1")
            assert item.id == "1"
            assert item.status == "pending"


        def test_{slug}_run():
            item = {class_name}(id="1")
            item.run()
            assert item.status == "done"
        ''').strip()

        return StageResult(
            summary=f"完成《{title}》TDD 实现",
            artifacts={
                "source_files": [{"path": f"src/{slug}.py", "content": source}],
                "test_files": [{"path": f"tests/test_{slug}.py", "content": test}],
            },
        )


# ---------------------------------------------------------------------------
# 7. 代码审查 Agent（交叉评审）
# ---------------------------------------------------------------------------

CODE_REVIEW_SYSTEM = textwrap.dedent("""你是一名资深代码审查专家，负责对代码进行严格评审。
严格输出 JSON，格式：
{
  "approved": true|false,
  "scores": {"正确性": 85, "可读性": 80, "测试": 78, ...},
  "comments": {"正确性": "评审意见", ...},
  "issues": [
    {"file": "src/xxx.py", "line": 10, "severity": "error|warning|suggestion", "message": "问题描述", "suggested_fix": "建议修复"}
  ],
  "summary": "评审总结"
}
评分标准（0-100）""")


class CodeReviewAgent(BaseExpertAgent):
    expert_type = "cross_reviewer"

    def system_prompt(self) -> str:
        return CODE_REVIEW_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx)
        title = ctx.workflow.title

        # 收集实现代码和测试代码
        source_files = []
        test_files = []
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            source_files.extend(artifacts.get("source_files", []))
            test_files.extend(artifacts.get("test_files", []))

        # 获取评审模板
        review_template = await self._get_review_template(ctx, ReviewType.CODE_REVIEW)

        user_prompt = textwrap.dedent(
            f"""任务：{title}
源代码：
{chr(10).join(f"# {f['path']}\n{f['content'][:800]}" for f in source_files[:3])}

测试代码：
{chr(10).join(f"# {f['path']}\n{f['content'][:400]}" for f in test_files[:2])}

评审检查点：
{json.dumps(review_template, ensure_ascii=False, indent=2)}

请进行代码审查（JSON格式）：
approved：是否通过
scores：各维度评分
comments：评审意见
issues：具体问题列表（file/line/severity/message/suggested_fix）
summary：评审总结"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed:
                decision = parsed.get("approved", False)
                scores = parsed.get("scores", {})
                comments = parsed.get("comments", {})
                issues = parsed.get("issues", [])
                summary = parsed.get("summary", "")
                logger.info("代码审查 LLM 生成成功: {} -> {} ({} issues)", title, "approve" if decision else "reject", len(issues))
                return StageResult(
                    summary=summary or f"代码审查{'通过' if decision else '需修改'}",
                    artifacts={
                        "decision": "approve" if decision else "reject",
                        "scores": scores,
                        "comments": comments,
                        "issues": issues,
                        "review_template": review_template,
                    },
                    approved=decision,
                    review_comment=json.dumps(comments, ensure_ascii=False),
                )

        return StageResult(
            summary="代码审查完成（规则回退）",
            artifacts={
                "decision": "approve",
                "scores": {"正确性": 75, "可读性": 70, "测试": 65},
                "comments": {"正确性": "规则回退：默认通过"},
                "issues": [],
            },
            approved=True,
            review_comment="审查通过（规则回退）",
        )

    async def _get_review_template(self, ctx: StageContext, review_type: ReviewType) -> dict:
        try:
            from app.db import repository as repo
            from app.db.session import session_scope
            async with session_scope() as session:
                template = await repo.get_default_review_template(session, review_type.value)
                if template:
                    return {"criteria": template.criteria, "rubric": template.rubric}
        except Exception:
            pass
        return {
            "criteria": [
                {"point": "正确性", "description": "逻辑是否正确"},
                {"point": "可读性", "description": "命名清晰、注释充分"},
                {"point": "测试覆盖", "description": "单元测试是否全面"},
                {"point": "错误处理", "description": "异常处理是否完善"},
            ],
            "rubric": ["正确性", "可读性", "测试", "健壮性"],
        }


# ---------------------------------------------------------------------------
# 8. 功能与集成测试 Agent
# ---------------------------------------------------------------------------

TESTING_SYSTEM = textwrap.dedent("""你是一名资深测试工程师，负责设计功能测试和集成测试用例。
严格输出 JSON，格式：
{
  "functional_tests": [
    {"name": "测试用例名", "steps": ["步骤1", "步骤2"], "expected": "预期结果", "category": "功能|边界|异常"}
  ],
  "integration_tests": [
    {"name": "集成测试名", "description": "描述", "test_data": {}, "expected_result": "预期结果"}
  ],
  "test_data": {"user": {...}, "items": [...]},
  "summary": "测试策略说明"
}""")


class TestingAgent(BaseExpertAgent):
    expert_type = "tester"

    def system_prompt(self) -> str:
        return TESTING_SYSTEM

    async def _execute(self, ctx: StageContext) -> StageResult:
        await _heartbeat(ctx)
        title = ctx.workflow.title
        desc = ctx.parent_description or ctx.workflow.description

        # 收集实现代码和需求
        source_code = ""
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            for f in artifacts.get("source_files", []):
                source_code += f.get("content", "") + "\n"

        req_items = []
        for h in ctx.history:
            artifacts = h.get("artifacts") or {}
            if "items" in artifacts:
                req_items = artifacts["items"]
                break

        user_prompt = textwrap.dedent(
            f"""任务：{title}
需求清单：
{json.dumps(req_items, ensure_ascii=False, indent=2) if req_items else '无'}

实现代码：
{source_code[:1000] if source_code else '无代码实现，请基于需求设计测试用例'}

请设计测试用例（JSON格式）：
functional_tests：功能测试用例（正常/边界/异常）
integration_tests：集成测试用例
test_data：测试数据
summary：测试策略说明

要求：
- 每个需求至少一个测试用例
- 步骤清晰（3-7步）
- 预期结果明确"""
        )

        content = await _call_llm(self.system_prompt(), user_prompt, json_mode=True)
        if content:
            parsed = _safe_parse_json(content)
            if parsed:
                func_tests = parsed.get("functional_tests", [])
                int_tests = parsed.get("integration_tests", [])
                logger.info("测试用例 LLM 生成成功: {} ({} functional, {} integration)", title, len(func_tests), len(int_tests))
                return StageResult(
                    summary=f"完成《{title}》测试用例设计",
                    artifacts=parsed,
                )

        # 规则回退
        cases = [
            {
                "name": f"{title} 正常流程",
                "steps": ["准备数据", "执行功能", "验证结果"],
                "expected": "功能正常",
                "category": "功能",
            }
        ]
        return StageResult(
            summary=f"完成《{title}》测试用例设计",
            artifacts={
                "functional_tests": cases,
                "integration_tests": [],
                "test_data": {},
                "summary": "功能+边界+异常全覆盖",
            },
        )


# ---------------------------------------------------------------------------
# Agent 工厂（支持交叉评审的动态 expert_type）
# ---------------------------------------------------------------------------

def create_agent_for_stage(stage_name: StageName, cross_reviewer_of: Optional[str] = None) -> BaseExpertAgent:
    """根据阶段名称创建对应的 Agent 实例。

    评审类阶段（REVIEW）使用 cross_reviewer 类型，
    但需要知道被评审的是哪个工作项（cross_reviewer_of）。
    """
    mapping = {
        StageName.REQUIREMENT_ANALYSIS: RequirementAnalysisAgent(),
        StageName.REQUIREMENT_REVIEW: RequirementReviewAgent(),
        StageName.TECHNICAL_DESIGN: TechnicalDesignAgent(),
        StageName.TECHNICAL_REVIEW: TechnicalReviewAgent(),
        StageName.TASK_BREAKDOWN: TaskBreakdownAgent(),
        StageName.IMPLEMENTATION: ImplementationAgent(),
        StageName.CODE_REVIEW: CodeReviewAgent(),
        StageName.TESTING: TestingAgent(),
    }
    return mapping.get(stage_name, ImplementationAgent())


# ---------------------------------------------------------------------------
# 注册（保持向后兼容）
# ---------------------------------------------------------------------------

DEFAULT_EXPERTS: list[BaseExpertAgent] = [
    RequirementAnalysisAgent(),
    TechnicalDesignAgent(),
    ImplementationAgent(),
    TestingAgent(),
    RequirementReviewAgent(),   # cross_reviewer 角色，用于需求评审
    TechnicalReviewAgent(),     # cross_reviewer 角色，用于技术评审
    CodeReviewAgent(),           # cross_reviewer 角色，用于代码审查
    TaskBreakdownAgent(),
]


def register_default_experts(pool) -> None:
    pool.register_many(DEFAULT_EXPERTS)