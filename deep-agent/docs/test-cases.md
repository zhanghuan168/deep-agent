# Deep Agent ZH · API 测试用例（v2.5）

> 覆盖 5 类端点：chat / parents CRUD / workflow / settings / heartbeat。
> 基于最新一次需求（**LLM 驱动 + 工具调用 + 改称呼 + 任务启停**）。

---

## 1. 聊天接口 `/api/chat` （LLM-driven Agent）

### 1.1 闲聊路径（intent=chat，**不**拆任务）

| # | 输入 | 预期 |
|---|---|---|
| 1.1.1 | "你能做什么" | `intent=chat`，PM 回复能力介绍 |
| 1.1.2 | "你是谁" | `intent=chat`，PM 自我介绍 |
| 1.1.3 | "怎么用" | `intent=chat`，PM 给操作指引 |
| 1.1.4 | "你好" | `intent=chat`，PM 问候回复 |
| 1.1.5 | "谢谢" | `intent=chat`，PM "不客气" |
| 1.1.6 | "?" 单独 | `intent=chat`，引导具体需求 |
| 1.1.7 | "这个系统有什么功能" | `intent=chat` |

### 1.2 任务路径（intent=tool，调用 create_task）

| # | 输入 | 预期 |
|---|---|---|
| 1.2.1 | "做一个会记账的微信小程序" | `intent=tool`，tool_call=create_task，**PM 回复必须列出拆解的工作项**（老板要看到） |
| 1.2.2 | "开发一个登录模块" | `intent=tool`，create_task |
| 1.2.3 | "调研竞品" | `intent=tool`，create_task |
| 1.2.4 | "重构 user service" | `intent=tool`，create_task |
| 1.2.5 | "加一个导出 Excel 功能" | `intent=tool`，create_task |

**关键：PM 回复要列任务！** 例如：
```
已创建任务《会记账的微信小程序》，拆出 5 个工作项：
  1. 需求分析
  2. UI 设计
  3. 后端 API 开发
  4. 前端页面开发
  5. 测试用例
```

### 1.3 工具调用路径（已配置 LLM 时）

| # | 输入 | 预期 LLM 决策 |
|---|---|---|
| 1.3.1 | "列出所有任务" | tool_call=`list_tasks`，PM 列出 |
| 1.3.2 | "查看任务 abc123" | tool_call=`get_task` |
| 1.3.3 | "删除任务 abc123" | tool_call=`delete_task` |
| 1.3.4 | "启动任务 abc123" | tool_call=`start_task` |
| 1.3.5 | "停止任务 abc123" | tool_call=`stop_task` |
| 1.3.6 | "暂停任务 abc123" | tool_call=`pause_task` |
| 1.3.7 | "继续任务 abc123" | tool_call=`resume_task` |
| 1.3.8 | "任务 abc123 的日志" | tool_call=`get_task_logs` |
| 1.3.9 | "有哪些专家" | tool_call=`list_experts` |
| 1.3.10 | "查看 LLM 配置" | tool_call=`list_settings` |

### 1.4 边界

| # | 输入 | 预期 |
|---|---|---|
| 1.4.1 | "" 空字符串 | `intent=chat`，PM 引导 |
| 1.4.2 | "   " 空白 | `intent=chat` |
| 1.4.3 | 100+ 字超长需求 | `intent=tool`，create_task 正常拆解 |
| 1.4.4 | 老板继续提需求（已有 parent_id） | 根据内容决定（refine / append / confirm） |
| 1.4.5 | LLM 不可用 | 走规则回退 |

---

## 2. 父任务 CRUD `/api/parents`

| # | 方法 | 路径 | 输入 | 预期 |
|---|---|---|---|---|
| 2.1 | GET | `/api/parents` | - | 列出所有 |
| 2.2 | GET | `/api/parents/{id}` | - | 详情含工作项和阶段 |
| 2.3 | PATCH | `/api/parents/{id}/status` | `{status: "in_progress"}` | 改状态、发布事件 |
| 2.4 | PATCH | `/api/parents/{id}/status` | `{status: "unknown"}` | 400 错误 |
| 2.5 | PATCH | `/api/parents/{id}/status` | 缺 status | 400 错误 |
| 2.6 | PATCH | `/api/parents/不存在/status` | - | 404 错误 |

### 状态机

```
draft → confirmed → scheduled → in_progress → completed
                                ↘ blocked → in_progress
                                ↘ failed
```

| 状态 | 能否 start_task | 能否 stop_task | 能否 pause_task | 能否 resume_task |
|---|---|---|---|---|
| draft | ✓ | ✗ | ✗ | ✗ |
| confirmed | ✓ | ✗ | ✗ | ✗ |
| scheduled | ✓ | ✓ | ✗ | ✗ |
| in_progress | ✗（已运行） | ✓ | ✓ | ✗ |
| blocked | ✗ | ✓ | ✗ | ✓ |
| completed | ✗ | ✗ | ✗ | ✗ |
| failed | ✓ | ✗ | ✗ | ✗ |

---

## 3. 工作项 + 阶段

| # | 方法 | 路径 | 预期 |
|---|---|---|---|
| 3.1 | GET | `/api/workflows/{id}` | 工作项详情含阶段 |
| 3.2 | GET | `/api/logs?parent_id=...` | 父任务日志 |
| 3.3 | GET | `/api/logs?workflow_id=...` | 工作项日志 |
| 3.4 | GET | `/api/conversation/{parent_id}` | 对话历史 |
| 3.5 | POST | `/api/stages/{stage_id}/review` `{decision:"approve"}` | 评审通过，re-enqueue |
| 3.6 | POST | `/api/stages/{stage_id}/review` `{decision:"reject"}` | 评审拒绝，标 failed |
| 3.7 | POST | `/api/stages/{stage_id}/review` `{decision:"xxx"}` | 400 错误 |

---

## 4. 设置 `/api/settings`

| # | 方法 | 路径 | 预期 |
|---|---|---|---|
| 4.1 | GET | `/api/settings` | 返回所有 + 脱敏 key |
| 4.2 | PUT | `/api/settings` `{settings: {...}}` | 批量更新 |
| 4.3 | POST | `/api/settings/test` | 测试连通性（ok/fail） |
| 4.4 | GET | 验证 key=`llm.api_key` 脱敏为 `***xxxx` |

---

## 5. 健康 + WebSocket

| # | 方法 | 路径 | 预期 |
|---|---|---|---|
| 5.1 | GET | `/api/health` | 200，返回队列长度 + 专家列表 |
| 5.2 | WS | `/api/ws` | 收到事件推送 |

事件类型：
- `parent.created` / `parent.scheduled` / `parent.status` / `parent.confirmed`
- `workflow.created` / `workflow.status` / `workflow.progress`
- `stage.status` / `stage.review_needed`
- `chat.message` `{parent_id, role, content, intent, tool?}`

---

## 6. 端到端流程

### 6.1 老板下达需求 → 任务跑完

1. POST /api/chat `{"message":"做一个会记账的小程序"}` → 创建 parent + work_items，PM 回复列出任务列表
2. 老板看到任务列表（前端看板实时刷新）
3. 系统自动调度（create_task 工具内已包含入队）
4. 各工作项依次跑：requirement → design → design_review → development → code_review → testing → test_review
5. 父任务状态变 in_progress → completed
6. WebSocket 推送整个过程的事件

### 6.2 老板中途用工具启停

1. POST /api/chat `{"message":"暂停任务 abc"}` → tool=pause_task
2. PATCH /api/parents/abc/status in_progress 已经走 in_progress → blocked
3. POST /api/chat `{"message":"继续任务 abc"}` → tool=resume_task → blocked → in_progress
4. 工作项继续执行

### 6.3 老板删任务

1. POST /api/chat `{"message":"删除任务 abc"}` → tool=delete_task
2. 父任务 + 工作项 + 日志 cascade 删除
3. 看板刷新消失

---

## 7. 已知 bug / 待修

### 7.1 DetachedInstanceError（运行时报错）

**复现**：POST /api/chat 消息含任务关键词，触发 `_tool_create_task` → `_parent_to_dict`

**堆栈**：
```
File "D:\project\work\deep-agent-zh\app\pm\conversational.py", line 322, in _tool_create_task
    return {"ok": True, "data": _parent_to_dict(parent)}
File "D:\project\work\deep-agent-zh\app\pm\conversational.py", line 292, in _parent_to_dict
    for w in (p.workflow_tasks or [])
sqlalchemy.orm.exc.DetachedInstanceError: Parent instance <ParentTask at 0x...> is not bound to a Session; 
lazy load operation of attribute 'workflow_tasks' cannot proceed
```

**根因**：`_tool_create_task` 在 `async with session_scope() as s:` 里创建 parent，但退出 context 时 session 关闭了，parent 变成 detached。然后 `_parent_to_dict` 里访问 `p.workflow_tasks` 触发 lazy load，但 session 已关闭。

**修复思路**：
- 方案 A：在 `_tool_create_task` 里**仍在 session 内**就调用 `_parent_to_dict`（在 `async with` 块内）
- 方案 B：把 `workflow_tasks` 关系用 `selectinload` 提前加载
- 方案 C：让 `_parent_to_dict` 接受一个可选的 session 参数，在外部 session 内调用

**用户说"先不要改，把测试用例输出一份"** → 等用户确认后再修。

### 7.2 中文乱码

- 旧数据是历史遗留（**今天 11:15 之前**插入的）。
- 新数据写入 + 读出**正常**（已验证 test_cn_insert.py）。
- 实际页面显示乱码可能是**会话/数据缓存**问题。重启服务后清理。
- 建议：在 PM 工具改造完成后，**清掉旧数据**重新跑测试。

---

## 8. 自动化测试脚本（建议）

```python
# tools/test_api.py
import pytest
from fastapi.testclient import TestClient
from app.api.app import create_app
from app.db.session import init_db
import os

@pytest.fixture
def client():
    os.environ["DAGENT_DB_PATH"] = "data/test.db"
    if os.path.exists("data/test.db"):
        os.remove("data/test.db")
    import asyncio
    asyncio.run(init_db())
    app = create_app()
    with TestClient(app) as c:
        yield c

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_chitchat_what_can_you_do(client):
    r = client.post("/api/chat", json={"message": "你能做什么"})
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == "chat"
    assert "能力" in j["content"] or "能做" in j["content"]

def test_chitchat_who_are_you(client):
    r = client.post("/api/chat", json={"message": "你是谁"})
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == "chat"

def test_task_create_lists_work_items(client):
    r = client.post("/api/chat", json={"message": "做一个会记账的微信小程序"})
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == "tool"
    assert j["data"]["tool_call"]["action"] == "create_task"
    # 关键：PM 回复必须包含工作项列表
    content = j["content"]
    assert "1." in content or "①" in content, f"PM 回复没列任务: {content}"

def test_task_create_succeeds(client):
    r = client.post("/api/chat", json={"message": "做一个测试任务"})
    j = r.json()
    parent_id = j["parent_id"]
    # 验证任务真的创建了
    r2 = client.get(f"/api/parents/{parent_id}")
    assert r2.status_code == 200
    assert r2.json()["title"] is not None
    # 验证有工作项
    assert len(r2.json()["workflow_tasks"]) > 0

def test_list_tasks_tool(client):
    r = client.post("/api/chat", json={"message": "列出所有任务"})
    assert r.status_code == 200
    j = r.json()
    assert j["data"]["tool_call"]["action"] == "list_tasks"

def test_get_settings_no_api_key(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    s = r.json()["settings"]
    api_key = s.get("llm.api_key", "")
    # 脱敏
    assert api_key == "" or api_key.startswith("***")

def test_put_settings(client):
    r = client.put("/api/settings", json={"settings": {
        "llm.provider": "deepseek",
        "llm.base_url": "https://api.deepseek.com/v1",
        "llm.model": "deepseek-chat",
        "llm.api_key": "sk-test-1234",
    }})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["settings"]["llm.api_key"].startswith("***")

def test_delete_task(client):
    # 先创建
    r = client.post("/api/chat", json={"message": "做一个临时任务"})
    parent_id = r.json()["parent_id"]
    # 删
    r2 = client.post("/api/chat", json={"message": f"删除任务 {parent_id}"})
    assert r2.status_code == 200
    r3 = client.get(f"/api/parents/{parent_id}")
    assert r3.status_code == 404

def test_start_task_already_running(client):
    r = client.post("/api/chat", json={"message": "做一个跑起来的任务"})
    parent_id = r.json()["parent_id"]
    # 模拟 in_progress
    # 通过 /api/parents/{id}/status 改 in_progress
    client.patch(f"/api/parents/{parent_id}/status", json={"status": "in_progress"})
    # 再 start
    r2 = client.post("/api/chat", json={"message": f"启动任务 {parent_id}"})
    # 期望：工具返回失败，PM 提示"已在执行中"
    assert not r2.json()["data"]["tool_result"]["ok"]
```

---

## 9. 手动测试脚本（用 curl）

```bash
# 健康
curl http://127.0.0.1:8765/api/health

# 闲聊
curl -X POST http://127.0.0.1:8765/api/chat -H "Content-Type: application/json" -d '{"message":"你能做什么"}'

# 任务
curl -X POST http://127.0.0.1:8765/api/chat -H "Content-Type: application/json" -d '{"message":"做一个会记账的微信小程序"}'

# 列出任务
curl -X POST http://127.0.0.1:8765/api/chat -H "Content-Type: application/json" -d '{"message":"列出所有任务"}'

# 修改状态
curl -X PATCH http://127.0.0.1:8765/api/parents/{id}/status -H "Content-Type: application/json" -d '{"status":"in_progress"}'

# 设置
curl http://127.0.0.1:8765/api/settings
```

---

## 10. 验收 checklist

- [ ] 1.1.x 闲聊路径不拆任务
- [ ] 1.2.x 任务路径**必须列出**工作项
- [ ] 1.3.x 工具调用路径能执行 CRUD
- [ ] 2.x PATCH 状态机正确
- [ ] 3.x 评审能 approve/reject
- [ ] 4.x settings key 脱敏
- [ ] 5.x WebSocket 推送
- [ ] 6.x 端到端流程跑通
- [ ] 7.x 修 DetachedInstanceError
- [ ] 8.x 自动化测试通过
