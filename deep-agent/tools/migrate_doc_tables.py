"""文档版本化四张新表的迁移脚本（幂等执行）。"""
import asyncio
import sqlite3
from pathlib import Path

DB_PATH = "/root/.openclaw/workspace/deep-agent/data/dagent.db"

SQL_CREATE_TABLES = """
-- documents 主表
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(36) PRIMARY KEY,
    parent_id VARCHAR(36) NOT NULL,
    stage_id VARCHAR(36),
    doc_type VARCHAR(32) NOT NULL,
    title VARCHAR(255) NOT NULL,
    current_version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES parent_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (stage_id) REFERENCES stage_instances(id) ON DELETE SET NULL
);

-- document_versions 版本表（append-only）
CREATE TABLE IF NOT EXISTS document_versions (
    id VARCHAR(36) PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    author VARCHAR(128) NOT NULL,
    change_summary VARCHAR(500) DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE(document_id, version)
);

-- review_records 评审记录表
CREATE TABLE IF NOT EXISTS review_records (
    id VARCHAR(36) PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    stage_id VARCHAR(36),
    version INTEGER NOT NULL,
    reviewer VARCHAR(128) NOT NULL,
    decision VARCHAR(16) NOT NULL,
    scores TEXT,
    comments TEXT DEFAULT '',
    attachment_refs TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY (stage_id) REFERENCES stage_instances(id) ON DELETE SET NULL
);

-- change_log 变更日志表（append-only）
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type VARCHAR(32) NOT NULL,
    entity_id VARCHAR(36) NOT NULL,
    action VARCHAR(32) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_changelog_entity (entity_type, entity_id),
    INDEX ix_changelog_created (created_at)
);

-- stage_instances 新增字段（current_document_id）
-- SQLite 不支持 IF NOT EXISTS ADD COLUMN 语法，直接执行，重复执行会报错故用 Python 处理
"""

SQL_ADD_CURRENT_DOC_ID = """
ALTER TABLE stage_instances ADD COLUMN current_document_id VARCHAR(36);
"""

SQL_ADD_INDEX = """
CREATE INDEX IF NOT EXISTS ix_documents_parent ON documents(parent_id);
CREATE INDEX IF NOT EXISTS ix_documents_stage ON documents(stage_id);
CREATE INDEX IF NOT EXISTS ix_doc_versions_doc ON document_versions(document_id);
CREATE INDEX IF NOT EXISTS ix_review_records_doc ON review_records(document_id);
"""


def migrate():
    path = Path(DB_PATH)
    if not path.exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 创建四张新表
    for stmt in SQL_CREATE_TABLES.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            cursor.execute(stmt)
            print(f"  ✅ {stmt[:60]}...")
        except Exception as e:
            print(f"  ⚠️  {stmt[:40]}... → {e}")

    # 2. stage_instances 新增 current_document_id 字段
    cursor.execute("PRAGMA table_info(stage_instances)")
    cols = [row[1] for row in cursor.fetchall()]
    if "current_document_id" not in cols:
        try:
            cursor.execute(SQL_ADD_CURRENT_DOC_ID.strip())
            print("  ✅ stage_instances.current_document_id added")
        except Exception as e:
            print(f"  ⚠️  current_document_id → {e}")
    else:
        print("  ℹ️  stage_instances.current_document_id already exists")

    # 3. 创建索引
    for idx_stmt in SQL_ADD_INDEX.strip().split(";"):
        idx_stmt = idx_stmt.strip()
        if idx_stmt:
            try:
                cursor.execute(idx_stmt)
                print(f"  ✅ index created")
            except Exception as e:
                print(f"  ⚠️  index → {e}")

    conn.commit()
    conn.close()
    print("\n✅ 迁移完成")


if __name__ == "__main__":
    migrate()