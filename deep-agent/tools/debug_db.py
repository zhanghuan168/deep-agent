"""debug: check DB Chinese encoding"""
import asyncio
from app.db.session import engine, session_scope
from sqlalchemy import text
from app.db import repository as repo


async def main():
    # 1) PRAGMA encoding
    async with engine.begin() as conn:
        r = await conn.exec_driver_sql("PRAGMA encoding")
        print("PRAGMA encoding:", list(r))
        r = await conn.exec_driver_sql("PRAGMA journal_mode")
        print("PRAGMA journal_mode:", list(r))

    # 2) write/read cn test
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP TABLE IF EXISTS _test_cn")
        await conn.exec_driver_sql("CREATE TABLE _test_cn (txt TEXT)")
        await conn.exec_driver_sql("INSERT INTO _test_cn VALUES ('你好世界')")
        r = await conn.exec_driver_sql("SELECT txt, hex(txt) FROM _test_cn")
        for row in r:
            print("test_cn row:", repr(row[0]), "hex:", row[1])
        await conn.exec_driver_sql("DROP TABLE _test_cn")

    # 3) read existing rows via repository
    async with session_scope() as s:
        from app.db.models import ParentTask
        from sqlalchemy import select
        stmt = select(ParentTask).order_by(ParentTask.created_at.desc()).limit(3)
        for p in (await s.execute(stmt)).scalars():
            print("parent:", repr(p.title), "created:", p.created_at)

asyncio.run(main())
