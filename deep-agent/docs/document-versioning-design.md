# 文档版本化管理设计方案

**版本**：1.0  
**日期**：2026-06-09  
**目标**：每个流程定稿文档都关联存档、评审意见留痕、变更有 Git 式记录、任务详情支持展示。

---

## 1. 设计理念

沿用"Git 的思维做数据库记录"：
- 文档**不可覆盖**，每次保存是**新版本**
- 评审意见作为**快照**与文档版本绑定
- 变更记录是**只增**的 append-only 日志
- 支持 `git diff` 风格的版本对比

---

## 2. 数据库设计

### 2.1 新增表

#### `documents` — 文档主表
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String(36) PK | UUID |
| `parent_id` | String(36) FK→parent_tasks | 归属的父任务 |
| `stage_id` | String(36) FK→stage_instances, nullable | 关联的阶段 |
| `doc_type` | String(32) | 类型：requirement / technical / code_review / design / other |
| `title` | String(255) | 文档标题 |
| `current_version` | Integer | 当前最新版本号 |
| `created_at` | DateTime | 创建时间 |
| `updated_at` | DateTime | 更新时间 |

#### `document_versions` — 文档版本表（append-only）
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String(36) PK | UUID |
| `document_id` | String(36) FK→documents | 归属文档 |
| `version` | Integer | 版本号（递增） |
| `content` | Text | 文档正文内容 |
| `author` | String(128) | 作者（AI/Boss/Agent名） |
| `change_summary` | String(500) | 变更摘要（自动生成） |
| `created_at` | DateTime | 保存时间 |

**约束**：`UNIQUE(document_id, version)` — 同文档版本号唯一

#### `review_records` — 评审记录表
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String(36) PK | UUID |
| `document_id` | String(36) FK→documents | 被评审的文档 |
| `stage_id` | String(36) FK→stage_instances, nullable | 关联的阶段 |
| `version` | Integer | 评审针对的版本号 |
| `reviewer` | String(128) | 评审者身份 |
| `decision` | String(16) | approve / reject / comment |
| `scores` | JSON | 各维度评分 |
| `comments` | Text | 评审意见正文 |
| `attachment_refs` | JSON | 附件引用列表 |
| `created_at` | DateTime | 评审时间 |

#### `change_log` — 变更日志表（append-only）
| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | Integer PK, autoincrement |  |
| `entity_type` | String(32) | documents / stages / tasks |
| `entity_id` | String(36) | 实体 ID |
| `action` | String(32) | created / versioned / reviewed / status_changed |
| `actor` | String(128) | 操作者 |
| `detail` | JSON | 操作详情 |
| `created_at` | DateTime | 时间 |

---

### 2.2 现有表变更

**`stage_instances`** 增加字段：
- `current_document_id` — 指向当前阶段的定稿文档

---

## 3. API 设计

### 3.1 文档 CRUD

```
POST   /api/documents/ 创建文档（含初始版本）
GET /api/documents/{doc_id}/ 获取文档（含最新内容）
PUT    /api/documents/{doc_id}/     保存新版本（不覆盖，只追加）
```

### 3.2 版本历史

```
GET    /api/documents/{doc_id}/versions/      列出所有版本
GET    /api/documents/{doc_id}/versions/{v}/ 获取指定版本内容
GET    /api/documents/{doc_id}/diff/?v1=N&v2=M  版本差异对比
```

### 3.3 评审

```
POST   /api/documents/{doc_id}/reviews/         提交评审记录
GET    /api/documents/{doc_id}/reviews/         某文档的评审历史
```

### 3.4 变更日志

```
GET    /api/change-log/?entity_type=documents&entity_id=xxx
```

### 3.5 任务详情关联

```
GET    /api/tasks/{parent_id}/documents/       某任务下所有文档
GET    /api/stages/{stage_id}/document/        某阶段关联的文档
```

---

## 4. 内容对比（Diff）算法

采用 **Line Diff**（简化版 unified diff）：
1. 按换行分割 content 为行列表
2. 对比 v1 和 v2 的行列表，输出 `+ - ` 前缀
3. 返回结构：`{ additions: [...], deletions: [...], unchanged: [...] }`

---

## 5.评审流程绑定

每个评审阶段（`requirement_review / technical_review / code_review`）完成后：
1. 阶段产物写入对应 `documents` 表，版本=1
2. 评审意见写入 `review_records`，关联文档ID + 版本号
3. 变更写入 `change_log`

---

## 6. 前端任务详情展示

任务详情页新增 **「文档 & 评审」** 区块：
- 文档列表（按 type 分组）
- 每个文档：标题 + 当前版本号 + 最新评审状态
- 点击展开版本时间线（垂直轴）
- 版本之间可点击「对比」按钮，横向展示 diff
- 评审意见以气泡形式展示在对应版本上

---

## 7. 实施计划

| 阶段 | 内容 | 并行 |
|------|------|------|
| P1 | 数据库模型（models.py）+ Repository +迁移脚本 | 子agent 1 |
| P2 | API路由（documents + reviews + change-log） | 子agent 2 |
| P3 | Diff 工具函数 + Service 层逻辑 | 子agent 3 |
| P4 | 前端文档展示组件（任务详情页嵌入） | 子agent 4 |
| P5 | 测试验证 + 数据初始化 | 主agent |

---

## 8. 约束与原则

1. **不覆盖原则**：任何文档更新只创建新版本，不修改历史版本
2. **Append-only**：change_log 只增不改不删
3. **向后兼容**：不修改现有 API 返回格式，新增字段可空
4. **阶段定稿绑定**：每个 stage 完成时自动创建/更新关联文档