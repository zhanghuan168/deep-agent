# 智能项目管理 Agent 系统设计方案（Windows 轻量版）

**版本**：2.0  
**运行环境**：Windows 10/11，Python 3.10+  
**核心理念**：老板只负责下达目标与决策，项目经理 Agent 负责任务拆解、排期与跟进，专家子 Agent 按标准软件研发流程（设计→评审→开发→评审→测试→评审）执行具体工作。聊天窗口仅为非阻塞的任务入口，所有执行异步进行，通过 WebView 承载的控制台实现全生命周期可视化。

---

## 1. 概述

本方案在 Windows 环境下运行，后端使用轻量级组件（SQLite、自研内存队列），前端采用 WebView2 实现，便于将同一套前端界面嵌入桌面程序或多客户端容器。系统逻辑保持三级协作：老板通过聊天下达需求，项目经理 Agent 解析、确认并拆分为子工作项，每个工作项进入标准研发流水线，由设计师、开发、测试、评审等专家 Agent 执行。所有任务状态通过本地 API + WebSocket 推送至 WebView 控制台，支持实时看板、甘特图及人工干预。

---

## 2. 核心概念

| 概念 | 描述 |
|------|------|
| **老板** | 唯一需求方，通过 WebView 内聊天窗口下达目标、查询进度、审批异常。 |
| **项目经理 Agent** | 基于 Pydantic-DeepAgents 的任务协调者，负责对话分析、需求确认、任务拆解、排期与汇报。 |
| **专家 Agent** | 专职执行者，分为需求分析师、设计师、开发、测试、评审等，均运行在同一 Python 进程中，通过异步协程调度。 |
| **父任务 (Task)** | 老板需求转化而来的顶层管理单元，仅作为聚合与观测节点。 |
| **子工作项 (WorkflowTask)** | 不可再分的执行单元，必须遵循预定义研发流程模板。 |
| **流程引擎** | 驱动工作项按阶段流转、分配专家、触发评审的内部服务。 |
| **控制台** | WebView 承载的单页应用，提供任务树、甘特图、日志、评审待办等功能。 |

---

## 3. 系统架构（Windows 轻量版）

整体采用 **单进程多协程** 架构，各组件以 Python 模块或类形式存在，通过 asyncio 异步协同。不使用外部消息中间件，所有异步通信通过**自研任务队列**（基于 `asyncio.Queue`）和**事件总线**（观察者模式）实现。数据库使用 SQLite，通过 SQLAlchemy 或原生 aiosqlite 操作。

```
┌───────────────────────────────────────────────────────────┐
│                    Windows 进程                            │
│                                                           │
│  ┌──────────────────┐         ┌─────────────────────────┐ │
│  │   WebView2 容器    │◄─────►│   FastAPI 后端（主线程）  │ │
│  │ (前端 SPA 页面)   │WebSocket│   - 聊天 API             │ │
│  │  - 聊天面板       │ HTTP    │   - 控制台 API          │ │
│  │  - 任务看板       │         │   - WebSocket 推送      │ │
│  └──────────────────┘         └───────────┬─────────────┘ │
│                                           │                │
│                          ┌────────────────▼──────────────┐ │
│                          │     核心业务模块（异步）       │ │
│                          │  - 项目经理 Agent             │ │
│                          │  - 任务调度器                 │ │
│                          │  - 流程引擎                   │ │
│                          │  - 专家池管理                 │ │
│                          │  - 心跳与超时监控             │ │
│                          │  - 自研任务队列 (asyncio)     │ │
│                          │  - 事件总线 (观察者模式)      │ │
│                          └───────────┬──────────────────┘ │
│                                      │                     │
│                          ┌───────────▼──────────────────┐ │
│                          │   SQLite 数据库               │ │
│                          │   - 任务/工作项/阶段/日志     │ │
│                          │   - 会话历史                  │ │
│                          │   - 队列持久化（可选）        │ │
│                          └──────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

- **前端**：使用 Vue/React 构建的单页应用，打包为静态资源，由 FastAPI 托管或直接通过 WebView2 加载本地 HTML。
- **WebView2**：Windows 内置现代浏览器控件，支持 WebSocket 和最新 Web API，可实现与桌面应用的深度集成（如托盘通知、系统菜单）。未来若需迁移至其他客户端（如移动端），只需用对应平台 WebView 加载相同前端页面，后端保持不变。
- **所有异步任务（Agent 执行、阶段流转、心跳检查等）**均由 asyncio 协程在同一个事件循环中运行，通过自研队列解耦。
- **数据库**：SQLite，使用 WAL 模式支持高并发读，仅有一份文件，无需独立服务。

---

## 4. 组件详细设计

### 4.1 项目经理 Agent

不变，基于 `pydantic_ai.Agent`，配备工具集，所有工具调用均为异步非阻塞（内部通过 `asyncio.create_task` 或放入队列）。

**工具调用与后台解耦**：确认计划时，`confirm_and_plan` 将父任务状态改为 `CONFIRMED`，并把父任务 ID 放入 `scheduler_queue`，然后立即返回文本，后续由调度器协程处理。

### 4.2 任务模型

同原方案，数据库表设计为：
- `parent_tasks`
- `workflow_tasks`
- `stage_instances`
- `task_logs`
- `conversation_history`

所有表使用 SQLite，通过 aiosqlite 实现异步访问。主键采用 UUID 字符串。

### 4.3 自研任务队列

使用 `asyncio.Queue` 实现内存队列，并提供简单的持久化备份（可选）。系统启动时若队列中有未处理任务，从数据库恢复加载。

```python
class TaskQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
    
    async def put(self, item):
        await self.queue.put(item)
    
    async def get(self):
        return await self.queue.get()
```

为每个逻辑通道创建单独的队列实例：
- `scheduler_queue`：待规划父任务
- `workflow_queue`：待执行的 WorkflowTask
- `stage_queue`：待阶段流转的工作项（由流程引擎使用）

### 4.4 事件总线与 WebSocket 推送

实现轻量级发布订阅：
- `EventBus` 类，持有事件类型与回调列表。
- 所有状态变更、进度更新、日志添加时调用 `EventBus.publish(event_type, data)`。
- WebSocket 管理器订阅这些事件，将更新推送到控制台前端。

```python
class EventBus:
    def __init__(self):
        self.subscribers = {}
    
    def subscribe(self, event_type, callback):
        ...
    
    async def publish(self, event_type, data):
        for cb in self.subscribers.get(event_type, []):
            await cb(data)
```

### 4.5 任务调度器

一个常驻协程，从 `scheduler_queue` 获取待规划父任务：
1. 调用内嵌的规划 Agent 拆分工作项。
2. 创建工作项记录，状态 `CREATED`。
3. 将工作项放入 `workflow_queue`。
4. 更新父任务状态为 `SCHEDULED`。

### 4.6 流程引擎

常驻协程从 `workflow_queue` 获取工作项，驱动其生命周期：
- 根据模板确定当前阶段，从专家池获取专家实例。
- 执行阶段任务，等待返回产物。
- 自动触发评审（若需要）。
- 评审通过则推进阶段，否则重试或标记失败。
- 每个状态变更发布事件，并更新进度。

由于所有专家 Agent 运行在同一进程中，阶段执行就是调用对应 Agent 的 `run()` 方法，可以等待完成。为了不阻塞其他任务，流程引擎可以为每个工作项启动一个独立协程处理。

### 4.7 专家 Agent 池

各类专家 Agent 均为 `pydantic_ai.Agent` 实例，在系统启动时初始化并注册到池中，类型固定。因为轻量级环境，专家实例可复用，无需动态加载，使用简单的字典管理。

```python
expert_pool = {
    "designer": designer_agent,
    "developer": developer_agent,
    "tester": tester_agent,
    "reviewer": reviewer_agent,
}
```

分配时直接通过类型获取，若要支持多实例并发，可为每个类型初始化多个 Agent 副本，由池进行轮转。

### 4.8 心跳与超时监控

使用单个定时协程，每 10 秒扫描所有 `IN_PROGRESS` 的工作项，检查 `heartbeat_at` 字段。若超时，根据策略自动重试或标记失败，并发布事件。工作项执行过程中，专家 Agent 通过调用 `update_heartbeat(task_id)` 工具更新数据库中对应记录的 `heartbeat_at`。

### 4.9 控制台

前端 SPA 通过 WebView2 加载，与后端通过 HTTP API 和 WebSocket 通信。主要页面：
- **任务看板**：按状态列展示父任务，点击展开工作项树。
- **甘特图**：基于工作项阶段时间线绘制。
- **详情抽屉**：阶段日志、评审意见。
- **评审待办中心**：列出需人工确认的评审项。

WebView2 可在启动时通过自定义协议加载本地资源，或从 `http://localhost:8000` 加载。若需嵌入其他应用（如 Electron 的 webview），只需改变加载地址，前端代码不变。

---

## 5. 系统启动与运行

**启动流程**：
1. 初始化 SQLite 数据库，创建表。
2. 初始化所有专家 Agent。
3. 启动 FastAPI 应用（包括 WebSocket 端点）。
4. 启动后台协程：任务调度器、流程引擎工作者、心跳监控、事件总线。
5. 打开 WebView2 窗口，导航至 `http://localhost:8000`。
6. 老板即可在聊天面板下达指令。

**多客户端扩展**：若将来需要在其他设备或应用中显示控制台，只需启动一个独立的 WebView 容器（如 Android WebView、CEF 等）并指向同一后端地址（需网络可达）。后端可在 `0.0.0.0` 监听，配合简单认证即可。

---

## 6. 技术栈清单

| 层次 | 技术 | 说明 |
|------|------|------|
| Agent 框架 | Pydantic-DeepAgents | Agent 定义、工具、对话管理 |
| LLM | 本地模型 (Ollama) 或云端 API | 可根据需求切换，推荐支持函数调用的模型 |
| 后端框架 | FastAPI | 异步 HTTP 与 WebSocket |
| 数据库 | SQLite + aiosqlite | 单文件，零配置，WAL 模式 |
| 队列 | asyncio.Queue + 可选 SQLite 队列表 | 纯内存，满足轻量需求 |
| 事件总线 | 自研观察者模式 | 解耦状态变更与通知 |
| 前端 | Vue3 + Element Plus (或 React + Ant Design) | 单页应用，打包为静态文件 |
| 桌面容器 | Microsoft Edge WebView2 | Windows 原生控件，免费且高性能 |
| 进程管理 | 单个 Python 进程 asyncio | 简化部署，无外部依赖 |

---

## 7. 扩展性设计

尽管当前采用轻量级单进程方案，核心接口已预留扩展空间：
- **队列替换**：TaskQueue 可随时替换为 Redis 或 RabbitMQ 适配器，无需修改调用方。
- **专家池横向扩展**：可通过 HTTP/gRPC 调用独立进程的专家服务，实现分布式执行。
- **数据库迁移**：SQLite 可平滑升级为 PostgreSQL，仅需更改连接字符串和少量 SQL 方言。
- **多客户端 UI**：前端独立于容器，任何支持 WebView 的平台均可集成，后端提供统一 API。

---

## 8. 总结

本方案专为 Windows 环境设计，通过 WebView2 + 轻量后端完美实现了“老板—项目经理—专家团队”协作系统。无重型中间件，部署简单，启动即用，同时保留了向分布式架构演进的弹性。通过标准软件研发流程的强制执行与实时看板，真正实现了“老板指方向，AI 带团队”的智能项目管理体验。