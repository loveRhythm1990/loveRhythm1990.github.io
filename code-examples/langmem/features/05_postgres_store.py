"""功能 5：外部存储 —— 把记忆持久化到 Postgres（pgvector）。

InMemoryStore 进程重启即丢，生产环境用 AsyncPostgresStore（LangGraph Platform
部署时会自动提供一个 Postgres -backed store）。

先起一个带 pgvector 的 Postgres（Docker）：

    docker run -d --name langmem-pg \
      -e POSTGRES_PASSWORD=postgres -p 5442:5432 pgvector/pgvector:pg16

然后分两步验证"跨进程持久化"：

    python -m features.05_postgres_store write   # 提取记忆并写入 Postgres
    python -m features.05_postgres_store read    # 新进程直接读，记忆还在
"""

import asyncio
import sys

from langgraph.store.postgres import AsyncPostgresStore
from langmem import create_memory_store_manager

from features.common import EMBEDDING_DIMS, get_embeddings, get_llm

DB_URI = "postgresql://postgres:postgres@localhost:5442/postgres?sslmode=disable"
NAMESPACE = ("memories", "{user_id}")
CONFIG = {"configurable": {"user_id": "user-1"}}


async def write():
    async with AsyncPostgresStore.from_conn_string(
        DB_URI,
        index={"dims": EMBEDDING_DIMS, "embed": get_embeddings()},
    ) as store:
        await store.setup()  # 首次运行建表 + 启用 vector 扩展
        manager = create_memory_store_manager(
            get_llm(), namespace=NAMESPACE, store=store
        )
        await manager.ainvoke(
            {
                "messages": [
                    {"role": "user", "content": "I prefer dark mode in all my apps"},
                    {"role": "assistant", "content": "I'll remember that preference"},
                ]
            },
            config=CONFIG,
        )
        print("已写入 Postgres。")


async def read():
    async with AsyncPostgresStore.from_conn_string(
        DB_URI,
        index={"dims": EMBEDDING_DIMS, "embed": get_embeddings()},
    ) as store:
        # 语义检索：query 与记忆做向量相似度排序
        results = await store.asearch(("memories", "user-1"), query="UI theme preference")
        for item in results:
            print(f"  key={item.key[:8]} score={item.score:.4f} value={item.value}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "write"
    asyncio.run({"write": write, "read": read}[mode]())
