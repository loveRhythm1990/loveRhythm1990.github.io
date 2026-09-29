---
layout: post
title: "LangMem 指南：给 Agent 加上长期记忆"
date: 2026-09-29 15:30:00 +0800
tags:
    - LangChain
---

## 简介

[LangMem](https://github.com/langchain-ai/langmem) 是 LangChain 官方维护的独立 Python 开源库（`pip install langmem`），[官方文档](https://langchain-ai.github.io/langmem/)的定位是：

> "LangMem helps agents learn and adapt from their interactions over time. It provides tooling to extract important information from conversations, optimize agent behavior through prompt refinement, and maintain long-term memory."

它要解决的问题：LLM 本身没有记忆，每次对话都是一张白纸。想让 agent 记住"用户偏好深色模式"、"上次那个方案被否决了"这类信息，并在后续对话（甚至是完全不同的会话）里用上，就得自己造一套记忆的提取、存储、检索、更新机制——LangMem 把这套机制做成了标准件。

LangMem 里每种记忆操作都遵循同一个模式：

1. 接收对话内容和当前记忆状态
2. 让一个 LLM 决定如何扩充或整合记忆状态
3. 返回更新后的记忆状态

本文所有代码基于  langmem 0.0.30 + langchain 1.4.3 + langgraph 1.2.12，完整可运行版本见
[code-examples/langmem](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/INDEX.md)。

---

## 典型用法

最常见的用法是给 agent 装上两个记忆工具，让它在对话中自己决定何时记、何时查（即 Hot Path，下一节细讲）：

```python
from langgraph.prebuilt import create_react_agent
from langgraph.store.memory import InMemoryStore
from langmem import create_manage_memory_tool, create_search_memory_tool

store = InMemoryStore(
    index={"dims": 384, "embed": embeddings}  # 不配 index 就没有语义检索
)

agent = create_react_agent(
    llm,
    tools=[
        create_manage_memory_tool(namespace=("memories", "{user_id}")),
        create_search_memory_tool(namespace=("memories", "{user_id}")),
    ],
    store=store,
    checkpointer=MemorySaver(),
)
```

`namespace` 里的 `{user_id}` 是占位符，运行时从 `config["configurable"]` 里取值填充，实现按用户隔离。实测效果（完整代码见
[features/01_hot_path.py](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/features/01_hot_path.py)）：

```
[thread-a] 问偏好     -> I don't have anything saved about a display mode preference...
[thread-a] 告知偏好   -> Got it — saved: you prefer dark mode. I'll default to that going forward.
[thread-b] 跨线程问   -> Here's what I have on file: You prefer dark mode...
[thread-c] 另一个用户 -> I searched my long-term memory but couldn't find any display preferences...

store 里 user-1 的原始记忆条目：
  ('memories', 'user-1') 97b46f05 -> {'content': 'User prefers dark mode as their display mode/theme.'}
```

几个值得注意的点：

- **thread-b 是全新对话，但 agent 依然记得偏好**。这就是 store 和 checkpointer 的分工：checkpointer 管单个 thread 的对话历史（短期记忆），store 管跨 thread 的长期记忆。
- **thread-c 换了 user_id，namespace 隔离生效**，搜不到别人的记忆。
- 存储结构是最朴素的三元组：namespace（目录）+ key（UUID）+ value（`{"content": ...}` 字典）。
- `create_manage_memory_tool` 和 `create_search_memory_tool` 返回的都是**单个**普通 LangChain Tool：前者内部用 `action` 参数区分 create/update/delete，后者只管语义检索，两者共享 store 和 namespace 但互不耦合。
- 一个版本坑：`from langgraph.prebuilt import create_react_agent` 在 LangGraph 1.0 已标记废弃，会提示改用 `langchain.agents.create_agent`，目前只是警告，官方文档示例还在用旧导入。

---

## Namespace：记忆的组织与隔离

所有 Stateful API（记忆工具、store manager）都要传一个 `namespace` 参数，这是 LangMem 组织记忆的统一方式，几个要点值得说清楚。

**类型是元组，字符串也可以。** 参数签名是 `namespace: tuple[str, ...] | str`，传字符串时内部会包装成单元素元组（`langmem/utils.py` 的 `NamespaceTemplate`）：

```python
self.template = template if isinstance(template, tuple) else (template,)
```

**`{user_id}` 这类占位符是模板变量，运行时才填充。** 每次调用时从 `config["configurable"]` 里取同名 key 的值做替换，替换后的具体元组才真正用于读写：

```python
namespace=("memories", "{user_id}")
+ config={"configurable": {"user_id": "user-1"}}
→ 实际读写 ("memories", "user-1")
```

`configurable` 里找不到对应 key 会抛 `ConfigurationError`。LangGraph 语境下常用的占位符是 `{langgraph_user_id}`。

**隔离的语义是"目录前缀"，不是"把整个元组当 key"。** `BaseStore` 的存储模型是两级结构：namespace 元组相当于目录路径，条目另有 key（UUID）作为目录下的唯一 ID。检索时按前缀匹配——`InMemoryStore` 的实现核心就是一行：

```python
namespace[: len(namespace_prefix)] == namespace_prefix
```

用文件系统类比最直观：`("memories", "user-1")` 相当于 `/memories/user-1/`，`store.search(("memories", "user-1"))` 相当于 `ls /memories/user-1/*`。上一节实测里 thread-c（user-2）搜不到 user-1 的记忆，就是这个前缀隔离的效果。

常见组织模式：

| 组织方式 | namespace 示例 | 用途 |
|---------|---------------|------|
| 按用户 | `("memories", "{user_id}")` | 用户间隔离 |
| 按助手 | `("memories", "{assistant_id}")` | 一个助手跨用户共享的记忆 |
| 按组织+用户 | `("memories", "{org_id}", "{user_id}")` | 既能按组织检索，又能按用户隔离 |
| 细分类型 | `("memories", "{user_id}", "manual")` | 同一用户下再分手动/自动等类型 |
{: .capability-table}

前缀匹配是双向的：`search(("memories",))` 能命中所有用户的记忆。这个特性可以用来做多 agent 的"读写分离"——**写到自己的子目录，读整个团队目录**：

```python
# agent_a：写入 ("memories", "team_a", "agent_a")，但能搜到整个 ("memories", "team_a")
create_manage_memory_tool(namespace=("memories", "team_a", "agent_a"))
create_search_memory_tool(namespace=("memories", "team_a"))
```

agent_b 同理。每个 agent 的写入互不干扰，搜索时却能看到队友记下的所有东西。反过来说，如果需要严格隔离，查询时就必须带上完整前缀，不能只搜顶层目录。

---

## Background 与 HotPath 的区别

记忆有两种形成时机，官方文档用一张图概括：

![Hot path vs background memory processing](/pics/langmem-hot_path_vs_background.png)

| 形成方式 | 延迟影响 | 生效速度 | 处理开销 | 适用场景 |
|---------|---------|---------|---------|---------|
| Hot Path（热路径） | 增加对话延迟 | 立即 | 对话过程中 | 关键上下文需要立刻记住 |
| Background（后台） | 无 | 延迟 | 对话间隙/之后 | 模式分析、总结、深度提取 |
{: .capability-table}

**Hot Path** 是"有意识"的记忆：把 `manage_memory`/`search_memory` 两个工具塞进 agent 的工具箱，模型在对话过程中自己判断"这个值得记"并调用工具。优点是立即生效、实现简单；缺点是增加了感知延迟，而且多了一类工具决策，可能干扰 agent 完成用户本来的需求。上一节的例子就是 Hot Path。

**Background** 是"潜意识"的记忆：对话照常进行，事后再让一个 memory manager 回顾对话、提取记忆。不增加延迟、不干扰 agent 决策，提取也更充分。典型写法（完整代码见
[features/02_background_reflection.py](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/features/02_background_reflection.py)）：

```python
memory_manager = create_memory_store_manager(llm, namespace=("memories", "{user_id}"))
executor = ReflectionExecutor(memory_manager, store=store)

@entrypoint(store=store)
def chat(message: str):
    response = llm.invoke(message)
    history.extend([{"role": "user", "content": message},
                    {"role": "assistant", "content": response.content}])
    # 延迟 10 秒再提取；这期间同 thread 的新 submit 会取消旧任务
    executor.submit({"messages": list(history)}, after_seconds=10)
    return response.content
```

`ReflectionExecutor` 解决一个现实问题：用户连续发消息时，每条都提取一遍既浪费 token 又只能拿到半截上下文。`submit(..., after_seconds=N)` 会把提取任务推迟 N 秒，这期间同一线程再有新提交，旧任务直接取消、以最新对话重新排队——即防抖（debounce）。实测用户连发 3 条消息，最终只提取 1~2 次（为什么是"1~2"而不是确定的 1，见「记忆提取」一节的实测踩坑）。

---

## 三种记忆类型

LangMem 的记忆分类借鉴了人类记忆结构，每种类型服务不同目的：

| 记忆类型 | 存什么 | 例子 | 典型存储形态 |
|---------|--------|------|-------------|
| Semantic 语义记忆 | 事实与知识 | 用户偏好、知识三元组 | Profile 或 Collection |
| Episodic 情景记忆 | 过往经验 | few-shot 示例、成功对话的复盘 | Collection |
| Procedural 程序记忆 | 系统行为 | agent 的核心性格与响应模式 | Prompt 规则或 Collection |
{: .capability-table}

### Semantic：Collection 与 Profile 两种形态

语义记忆有两种组织方式。**Collection** 是大家直觉中的长期记忆：每条记忆是一份独立文档，新对话来了就插入/更新/删除：

![Collection update process](/pics/langmem-update-list.png)

**Profile** 则是一份单一文档，代表"当前状态"（用户的名字、偏好、目标等），新信息来了就原地更新这份文档，而不是新建：

![Profile update process](/pics/langmem-update-profile.png)

选择依据：需要快速拿到当前状态、或者想把档案直接展示给用户编辑，用 Profile；需要跨大量交互累积知识、按相关性召回，用 Collection。

### Episodic：记录"怎么做成一件事"

情景记忆不存事实，存**完整经验**：当时的处境、推理过程、采取的行动、为什么奏效。LangMem 用一个四段式的 `Episode` schema 来提取：

```python
class Episode(BaseModel):
    observation: str  # 处境与相关上下文
    thoughts: str     # 关键考量与推理过程
    action: str       # 做了什么
    result: str       # 结果如何、为什么有效
```

实测用"用户借助家谱知识理解了二叉树"这段对话提取，agent 复盘出的经验相当像样（完整代码见
[features/03_memory_manager.py](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/features/03_memory_manager.py)）：

```
observation='用户没有 CS 背景，但提供了领域类比线索（家谱）'
thoughts='用户已经告诉我该怎么教他：把陌生概念映射到熟悉领域。二叉树结构上几乎就是家谱
         （节点=人，边=父子关系），唯一区别是最多两个孩子'
action='把二叉树定义为"每个家长最多两个孩子的家谱"，并给出 Bob/Amy/Carl 的极简 ASCII 图'
result='用户立刻理解并主动把类比延伸到二叉搜索树。用他自己的领域做框架让他变成了对话的主动方'
```

### Procedural：直接改写 system prompt

程序记忆编码"agent 应该怎么做事"，载体就是 system prompt 本身——根据反馈和经历持续改写提示词：

![Instructions update process](/pics/langmem-update-instructions.png)

这就是 `create_prompt_optimizer` 干的事，单独在「优化 Prompt」一节讲。

---

## 记忆提取

### create_memory_manager：纯函数提取

`create_memory_manager` 是 Core API 的入口，签名上最要紧的是三个开关：

```python
create_memory_manager(
    model,
    schemas=(Memory,),        # 记忆的 Pydantic schema，可传多个
    instructions=...,          # 提取指令（默认值见附录）
    enable_inserts=True,       # 允许新建
    enable_updates=True,       # 允许更新
    enable_deletes=False,      # 允许删除（默认关！）
)
```

读源码（`langmem/knowledge/extraction.py`）后几个实现细节值得知道：

- 底层是 [trustcall](https://github.com/hinthornw/trustcall) 的 `create_extractor`：你传的每个 schema 都变成 LLM 的一个工具，模型通过并行工具调用来"写"记忆；更新已有记忆时走 JSONPatch 机制，删除则返回 `RemoveDoc` 对象。
- 提示词组装方式：system 固定是 `"You are a memory subroutine for an AI."`，user 消息 = 你的 instructions + 一段固定后缀（"Enrich, prune, and organize memories... All operations must be done in single parallel multi-tool call."）+ 用 `<session_{uuid}>` 标签包裹的对话全文。
- 支持多步提取：传 `max_steps=N` 时，第二轮起工具列表里会加一个 `Done` 工具，模型可以反复修补记忆、完成后调用 `Done` 收尾；默认 `max_steps=1`，即一轮并行工具调用出所有结果。
- Profile 模式的实现就是 `enable_inserts=False`：不许新建，模型只能更新既有文档。

实测三轮对话的增删改（模型 deepseek-chat）：

```
第 1 轮 "Alice manages the ML team and mentors Bob"：
  [Triple] Alice manages ML team / Alice mentors Bob / Bob member of ML team

第 2 轮 "Bob now leads the ML team and the NLP project"：
  [Triple] Bob leads ML team / Bob leads NLP project   <- 新增
  [RemoveDoc] json_doc_id='f0d1f791...'                <- 删除过时的 "Alice manages ML team"
  [Triple] Alice mentors Bob                           <- 保留

第 3 轮 "Alice left the company"：
  [Triple] Alice left the company                      <- 新增
  [RemoveDoc] ...                                      <- 继续清理 Alice 的过时角色
```

### create_memory_store_manager：带上存储的完整闭环

`create_memory_store_manager` 在上面纯函数的基础上接了 `BaseStore`，数据流变成全自动：

```
对话 -> 先在 store 里搜相关记忆（query_limit=5，可用 query_model 换个快模型生成搜索词）
     -> 连同既有记忆一起交给 LLM 提取/更新
     -> 结果自动 upsert/delete 回 store
```

它还支持 `default` / `default_factory` 参数：没有任何记忆时先初始化一份默认值（比如应用级的默认偏好），之后在这份默认值上演化——很适合 Profile 场景。

### ReflectionExecutor 的实现与实测踩坑

`ReflectionExecutor` 是个工厂函数，按入参分两种实现（`langmem/reflection.py`）：

- **RemoteReflectionExecutor**（reflector 传字符串）：通过 LangGraph SDK 调远端服务，`runs.create(..., after_seconds=N, multitask_strategy="rollback")`，防抖由 LangGraph Platform 服务端保证，适合 serverless 部署。
- **LocalReflectionExecutor**（reflector 传 Runnable）：本地后台 worker 线程 + `PriorityQueue`（按执行时间排序）+ `_pending_tasks` 字典（按 thread_id 索引）。`submit` 时若同 thread 已有 pending 任务，就 `cancel_event.set()` + `future.cancel()` 取消旧任务。

**实测踩坑：本地防抖有竞态，中间任务可能漏网执行。** 实测用真实 LLM 对话（每次提交间隔 1~4 秒）连发 3 条消息，提取执行了 **2 次** 而不是预期的 1 次。对照实验确认了边界：

```
场景 A：1 秒内紧凑 submit 3 次   -> 实际执行 1 次（只有最后一次）✓
场景 B：每隔 1.5 秒 submit 一次  -> 实际执行 2 次（中间任务漏网）✗
```

根因在 worker 回收已取消任务时的这段逻辑：

```python
execute_at, task = self._task_queue.get(timeout=1)
if task.future.cancelled():
    self._pending_tasks.pop(task.thread_id, None)  # 问题在这
    continue
```

worker 丢弃一个已取消的旧任务时，按 thread_id 从 `_pending_tasks` 里 pop——但此时字典里存的已经是**更新的任务**了，这一 pop 把新任务从"可取消注册表"里误删。之后再有 submit 到来，发现该线程没有 pending 任务可取消，于是队列里那个中间任务没人拦，到点照样执行。时序如下：

```
t=1.0  submit task1                    _pending_tasks[t1] = task1
t=2.2  submit task2, 取消 task1        _pending_tasks[t1] = task2
t=3.0  worker 取出 task1, 发现已取消 -> pop("t1") 误删了 task2 的注册项
t=6.3  submit task3, 发现 t1 无 pending 可取消
t=12.2 task2 到点执行（本应被取消）     <- 漏网
t=16.3 task3 执行
```

紧凑提交时（场景 A）所有取消都发生在 worker 回收之前，所以表现正确；提交间隔超过 worker 约 1 秒的轮询窗口就可能踩中。修复方向是 pop 前校验身份（`if self._pending_tasks.get(tid) is task`）。实际影响可控：多提取一次只是多花一次 LLM 调用的钱，记忆内容最终一致。

**另一个坑在模型侧**：最初用 `deepseek-v4-flash` 跑记忆更新时直接死循环到 `GraphRecursionError: Recursion limit of 25 reached`。开 debug 发现 trustcall 的"更新既有记忆"环节会强制指定 `tool_choice`，而思考模型报 `400 - Thinking mode does not support this tool_choice`；这个 400 被 trustcall 当成"校验错误"无限重试。换成非思考模型 `deepseek-chat` 后一切正常。首轮纯提取（`tool_choice="any"`）不受影响，只有带 existing 记忆的更新路径会触发。

---

## 优化 Prompt

程序记忆的落地 API 是 `create_prompt_optimizer(model, kind=...)`：输入"对话轨迹 + 可选反馈 + 当前 prompt"，输出改写后的 prompt。三种策略（`langmem/prompts/` 目录）：

| 策略 | LLM 调用次数 | 实现方式 | 适用 |
|------|-------------|---------|------|
| `prompt_memory` | 1 次 | 单发 metaprompt，结构化输出 `{logic, update_prompt, new_prompt}` | 简单场景，最便宜 |
| `metaprompt` | 1~5 次 | 反思循环，每步 1 次调用 | 性价比平衡 |
| `gradient` | 2~10 次 | 每步 2 次调用：先 think/critique 找问题，再 recommend 决策 | 最彻底，最贵 |
{: .capability-table}

### create_prompt_optimizer 的实现

读源码后，"gradient" 这个名字其实是个隐喻——它跟数值梯度没有关系，整套机制是**用工具调用搭出来的反思循环**（`langmem/prompts/gradient.py`）：

1. 定义三个"假工具"：`think`（思考）、`critique`（批判）、`recommend`（决策，带 `warrants_adjustment`/`hypotheses`/`full_recommendations` 参数），用 trustcall 的 `create_extractor` 强制模型以工具调用形式输出。
2. 反思循环按步数强制切换 `tool_choice`：前 `min_reflection_steps` 步只允许 think/critique（强制充分反思），中间步三选一，最后一步强制 recommend（必须给结论）。
3. 若 `warrants_adjustment=False`：原样返回旧 prompt，不浪费修改。
4. 若需要调整：把 hypotheses + recommendations 填进第二个提示词（`DEFAULT_GRADIENT_METAPROMPT`），再做一次结构化抽取得到 `improved_prompt`。

也就是说"梯度"对应的是：think/critique ≈ 计算梯度（找方向），recommend ≈ 走一步梯度下降（应用更新）。`metaprompt` 是同一个循环的简化版（每步一次调用），`prompt_memory` 则退化成单发调用。三个策略的默认提示词全文见附录。

实测效果（两条"用户嫌太理论、要代码"的负反馈轨迹，完整代码见
[features/04_prompt_optimizer.py](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/features/04_prompt_optimizer.py)），原始 prompt 只有一句 `You are a helpful assistant.`，优化后：

```
===== kind=metaprompt =====
You are a helpful assistant.

Response style guidelines:
- Prefer concrete, practical answers over theory. When a user asks "how do I do X"...
- Be concise. Do not open with background, history, or conceptual framing...
- If the user signals they want less explanation or more code (e.g., "just give me
  the code"), drop the prose immediately...

Example of desired style:        <- 自动合成了 few-shot 示例
User: How do I read a file in Python?
Assistant: ```python
with open("file.txt", "r") as f: ...
```

===== kind=gradient =====
You are a helpful assistant.

Prefer concrete, practical answers over theoretical explanation.
- For programming or how-to questions, lead with a short, runnable code example...
- Assume the user wants the answer, not a lecture: get to the point quickly.
```

注意 metaprompt 版本自动合成了一段 few-shot 示例——这正是默认提示词里"对风格类问题优先考虑合成 few-shot 示例"的策略在起作用。另外还有一个 `create_multi_prompt_optimizer`，面向多 prompt 组成的复合系统（比如 researcher + writer 的多 agent 流水线）：先做信用分配（把整体表现归因到各环节 prompt），再逐个优化需要调整的 prompt。

---

## 如何将记忆存到外部存储器

LangMem 的 Core API 不绑定存储，但 Stateful 层（store manager、记忆工具）都落在 LangGraph 的 `BaseStore` 接口上，所以换存储就是换实现：

| 实现 | 用途 |
|------|------|
| `InMemoryStore` | 开发调试，进程重启即丢 |
| `AsyncPostgresStore` | 生产自建，配合 pgvector 做向量检索 |
| LangGraph Platform | 部署平台自动提供 Postgres-backed store，零配置 |
{: .capability-table}

存储模型两个核心概念：**namespace**（多级元组，类似文件目录，支持 `{user_id}` 这类模板变量运行时填充，用来按用户/团队/应用隔离）和**检索**（按 key 直取、按语义相似度搜索、按元数据过滤；语义搜索要求建 store 时配 `index={"dims": ..., "embed": ...}`）。

实测用 Docker 起一个 pgvector Postgres，验证跨进程持久化（完整代码见
[features/05_postgres_store.py](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/features/05_postgres_store.py)）：

```bash
docker run -d --name langmem-pg \
  -e POSTGRES_PASSWORD=postgres -p 5442:5432 pgvector/pgvector:pg16
```

```python
from langgraph.store.postgres import AsyncPostgresStore
from langmem import create_memory_store_manager

DB_URI = "postgresql://postgres:postgres@localhost:5442/postgres?sslmode=disable"

async with AsyncPostgresStore.from_conn_string(
    DB_URI, index={"dims": 384, "embed": embeddings}
) as store:
    await store.setup()  # 首次运行建表 + 启用 vector 扩展
    manager = create_memory_store_manager(
        llm, namespace=("memories", "{user_id}"), store=store
    )
    await manager.ainvoke({"messages": [...]}, config={"configurable": {"user_id": "user-1"}})
```

分两个进程验证——第一个进程写入，第二个进程（全新启动）直接语义检索：

```
$ python -m features.05_postgres_store write
已写入 Postgres。

$ python -m features.05_postgres_store read
  key=b0a56bba score=0.2041 value={'kind': 'Memory', 'content': {'content':
  'User preference: prefers dark mode in all apps (stated explicitly, general/global
   scope — applies across applications, not just one). Confidence: high (direct self-report).'}}
```

两个 embeddings 相关的坑（本文示例的应对方式见
[features/common.py](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/features/common.py)）：

- **不配 index 就没有真正的检索**：`InMemoryStore()` 不传 index 不报错，但 `search()` 只是原样返回该 namespace 下全部条目，score 全是 None。条目少时看起来"检索对了"，条目一多就退化成全量塞给模型。
- **embed 用的是独立凭证**：`"openai:text-embedding-3-small"` 这类配置走 `OPENAI_API_KEY`，跟主模型的 key 无关；DeepSeek 这类对话服务根本没有 embeddings 接口（实测 404）。本文示例用了一个本地词袋哈希 embedding 演示完整链路（只表达词面重叠），生产环境必须换真实 embedding 模型。

---

## 附录

以下提示词均取自 `langmem 0.0.30` 源码原文。

### 记忆提取提示词（_MEMORY_INSTRUCTIONS）

`create_memory_manager` / `create_memory_store_manager` 的默认提取指令（[langmem/knowledge/extraction.py#L185](https://github.com/langchain-ai/langmem/blob/main/src/langmem/knowledge/extraction.py#L185)）：

```text
You are a long-term memory manager maintaining a core store of semantic, procedural, and episodic memory. These memories power a life-long learning agent's core predictive model.

What should the agent learn from this interaction about the user, itself, or how it should act? Reflect on the input trajectory and current memories (if any).

1. **Extract & Contextualize**  
   - Identify essential facts, relationships, preferences, reasoning procedures, and context
   - Caveat uncertain or suppositional information with confidence levels (p(x)) and reasoning
   - Quote supporting information when necessary

2. **Compare & Update**  
   - Attend to novel information that deviates from existing memories and expectations.
   - Consolidate and compress redundant memories to maintain information-density; strengthen based on reliability and recency; maximize SNR by avoiding idle words.
   - Remove incorrect or redundant memories while maintaining internal consistency

3. **Synthesize & Reason**  
   - What can you conclude about the user, agent ("I"), or environment using deduction, induction, and abduction?
   - What patterns, relationships, and principles emerge about optimal responses?
   - What generalizations can you make?
   - Qualify conclusions with probabilistic confidence and justification

As the agent, record memory content exactly as you'd want to recall it when predicting how to act or respond. 
Prioritize retention of surprising (pattern deviation) and persistent (frequently reinforced) information, ensuring nothing worth remembering is forgotten and nothing false is remembered. Prefer dense, complete memories over overlapping ones.
```

实际调用时还会拼上固定后缀和会话内容（[extraction.py#L468 `_prepare_messages`](https://github.com/langchain-ai/langmem/blob/main/src/langmem/knowledge/extraction.py#L468)）：

```text
# system
You are a memory subroutine for an AI.

# user = {instructions} + 固定后缀 + <session_{uuid}> 包裹的对话全文
Enrich, prune, and organize memories based on any new information. If an existing memory is incorrect or outdated, update it based on the new information. All operations must be done in single parallel multi-tool call. Avoid duplicate extractions.
```

### 提示词优化的提示词

**prompt_memory 策略**（`INSTRUCTION_REFLECTION_PROMPT`，单发调用，结构化输出 `{logic, update_prompt, new_prompt}`；[langmem/prompts/prompt.py#L3](https://github.com/langchain-ai/langmem/blob/main/src/langmem/prompts/prompt.py#L3)，多轨迹版本 `INSTRUCTION_REFLECTION_MULTIPLE_PROMPT` 在 [L39](https://github.com/langchain-ai/langmem/blob/main/src/langmem/prompts/prompt.py#L39)）：

```text
You are helping an AI agent improve. You can do this by changing their system prompt.

These is their current prompt:
<current_prompt>
{current_prompt}
</current_prompt>

Here was the agent's trajectory:
<trajectory>
{trajectory}
</trajectory>

Here is the user's feedback:

<feedback>
{feedback}
</feedback>

Here are instructions for updating the agent's prompt:

<instructions>
{instructions}
</instructions>

Based on this, return an updated prompt

You should return the full prompt, so if there's anything from before that you want to include, make sure to do that. Feel free to override or change anything that seems irrelevant. You do not need to update the prompt - if you don't want to, just return `update_prompt = False` and an empty string for new prompt.
```

**metaprompt 策略**（`DEFAULT_METAPROMPT`，反思循环每步使用；[langmem/prompts/metaprompt.py#L27](https://github.com/langchain-ai/langmem/blob/main/src/langmem/prompts/metaprompt.py#L27)）：

```text
You are helping an AI assistant learn by optimizing its prompt.

## Background

Below is the current prompt:

<current_prompt>
{prompt}
</current_prompt>

The developer provided these instructions regarding when/how to update:

<update_instructions>
{update_instructions}
</update_instructions>

## Session Data
Analyze the session(s) (and any user feedback) below:

<trajectories>
{trajectories}
</trajectories>

## Instructions

1. Reflect on the agent's performance on the given session(s) and identify any real failure modes (e.g., style mismatch, unclear or incomplete instructions, flawed reasoning, etc.).
2. Recommend the minimal changes necessary to address any real failures. If the prompt performs perfectly, simply respond with the original prompt without making any changes.
3. Retain any f-string variables in the existing prompt exactly as they are (e.g. {{variable_name}}).

IFF changes are warranted, focus on actionable edits. Be concrete. Edits should be appropriate for the identified failure modes. For example, consider synthetic few-shot examples for style or clarifying decision boundaries, or adding or modifying explicit instructions for conditionals, rules, or logic fixes; or provide step-by-step reasoning guidelines for multi-step logic problems if the model is failing to reason appropriately.
```

**gradient 策略**由两段提示词组成。第一段 `DEFAULT_GRADIENT_PROMPT` 负责"找问题"（配合 think/critique/recommend 工具；[langmem/prompts/gradient.py#L16](https://github.com/langchain-ai/langmem/blob/main/src/langmem/prompts/gradient.py#L16)）：

```text
You are reviewing the performance of an AI assistant in a given interaction. 

## Instructions

The current prompt that was used for the session is provided below.

<current_prompt>
{prompt}
</current_prompt>

The developer provided the following instructions around when and how to update the prompt:

<update_instructions>
{update_instructions}
</update_instructions>

## Session data

Analyze the following trajectories (and any associated user feedback) (either conversations with a user or other work that was performed by the assistant):

<trajectories>
{trajectories}
</trajectories>

## Task

Analyze the conversation, including the user’s request and the assistant’s response, and evaluate:
1. How effectively the assistant fulfilled the user’s intent.
2. Where the assistant might have deviated from user expectations or the desired outcome.
3. Specific areas (correctness, completeness, style, tone, alignment, etc.) that need improvement.

If the prompt seems to do well, then no further action is needed. We ONLY recommend updates if there is evidence of failures.
When failures occur, we want to recommend the minimal required changes to fix the problem.

Focus on actionable changes and be concrete.

1. Summarize the key successes and failures in the assistant’s response. 
2. Identify which failure mode(s) best describe the issues (examples: style mismatch, unclear or incomplete instructions, flawed logic or reasoning, hallucination, etc.).
3. Based on these failure modes, recommend the most suitable edit strategy. For example, consider::
   - Use synthetic few-shot examples for style or clarifying decision boundaries.
   - Use explicit instruction updates for conditionals, rules, or logic fixes.
   - Provide step-by-step reasoning guidelines for multi-step logic problems.
4. Provide detailed, concrete suggestions for how to update the prompt accordingly.

But remember, the final updated prompt should only be changed if there is evidence of poor performance, and our recommendations should be minimally invasive.
Do not recommend generic changes that aren't clearly linked to failure modes.

First think through the conversation and critique the current behavior.
If you believe the prompt needs to further adapt to the target context, provide precise recommendations.
Otherwise, mark `warrants_adjustment` as False and respond with 'No recommendations.'
```

第二段 `DEFAULT_GRADIENT_METAPROMPT` 负责"应用更新"（[langmem/prompts/gradient.py#L67](https://github.com/langchain-ai/langmem/blob/main/src/langmem/prompts/gradient.py#L67)）：

```text
You are optimizing a prompt to handle its target task more effectively.

<current_prompt>
{current_prompt}
</current_prompt>

We hypothesize the current prompt underperforms for these reasons:

<hypotheses>
{hypotheses}
</hypotheses>

Based on these hypotheses, we recommend the following adjustments:

<recommendations>
{recommendations}
</recommendations>

Respond with the updated prompt. Remember to ONLY make changes that are clearly necessary. Aim to be minimally invasive:
```

---

## 参考

- [LangMem 官方文档](https://langchain-ai.github.io/langmem/)
- [LangMem GitHub 仓库](https://github.com/langchain-ai/langmem)
- [本文示例代码](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/langmem/INDEX.md)
- [LangGraph BaseStore 文档](https://langchain-ai.github.io/langgraph/reference/store/)
