# LangMem 指南示例代码

配套博客：《LangMem 指南：给 Agent 加上长期记忆》。
所有代码基于真实安装的 `langmem` 包编写并实际运行验证过，不是伪代码。

## 快速开始

```bash
cd code-examples/langmem
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # 网络问题可换清华镜像
cp .env.example .env             # 填入你的 LLM 配置
python main.py                   # 或直接 python -m features.01_hot_path
```

## 5 个功能模块

| 序号 | 文件 | 内容 | 关键 API |
|------|------|------|---------|
| 1 | `features/01_hot_path.py` | Hot Path：agent 用工具主动记/查记忆，跨线程召回、按用户隔离 | `create_manage_memory_tool` / `create_search_memory_tool` |
| 2 | `features/02_background_reflection.py` | Background：对话后自动提取记忆，防抖合并 | `create_memory_store_manager` + `ReflectionExecutor` |
| 3 | `features/03_memory_manager.py` | 纯函数 Core API：三元组集合 / Profile / Episode 三种记忆形态 | `create_memory_manager` |
| 4 | `features/04_prompt_optimizer.py` | 用对话轨迹+反馈优化 system prompt | `create_prompt_optimizer` |
| 5 | `features/05_postgres_store.py` | 记忆持久化到 Postgres(pgvector)，跨进程验证 | `AsyncPostgresStore` |

功能 5 需要先启动 Postgres：

```bash
docker run -d --name langmem-pg \
  -e POSTGRES_PASSWORD=postgres -p 5442:5432 pgvector/pgvector:pg16
python -m features.05_postgres_store write   # 写入
python -m features.05_postgres_store read    # 新进程读取，验证持久化
```

## 两个环境注意点

1. **模型选择**：示例用 `deepseek-chat`。`deepseek-v4-flash` 这类思考模型在
   trustcall 强制指定 `tool_choice` 的环节会报 400（thinking mode 不支持），
   并被当成校验错误无限重试直至 `GraphRecursionError`。
2. **Embeddings**：DeepSeek 没有 embeddings 接口，示例用 `features/common.py`
   里的本地 `HashEmbeddings`（词袋哈希）演示向量检索链路，只表达词面重叠。
   生产环境换成真实 embedding 模型，如
   `OpenAIEmbeddings(model="text-embedding-3-small")`。

## 已知问题（langmem 0.0.30 实测）

`ReflectionExecutor` 的防抖存在竞态：worker 线程回收"已取消的旧任务"时会把
该线程**当前**的 pending 任务一并移出注册表，导致中间被取代的任务仍可能执行。
紧凑提交（<1s）时表现正确（只执行最后一次）；提交间隔大于 worker 轮询窗口时
可能出现"submit 3 次执行 2 次"。详见功能 2 与博客「记忆提取」一节。
