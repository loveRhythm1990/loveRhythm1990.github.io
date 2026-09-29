"""功能 1：Hot Path —— Agent 在对话中主动管理自己的长期记忆。

要点：
- create_manage_memory_tool / create_search_memory_tool 是两个普通工具，
  绑定在 LangGraph 的 BaseStore 上，由模型自己决定何时记、何时查。
- namespace 里的 {user_id} 占位符在运行时从 config["configurable"] 填充，
  实现按用户隔离。
- checkpointer 管"单线程对话历史"（短期），store 管"跨线程记忆"（长期）：
  thread-b 是全新对话，但 agent 依然记得 thread-a 里存下的偏好。

运行：python -m features.01_hot_path
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.store.memory import InMemoryStore
from langmem import create_manage_memory_tool, create_search_memory_tool

from features.common import EMBEDDING_DIMS, get_embeddings, get_llm

llm = get_llm()

store = InMemoryStore(
    index={"dims": EMBEDDING_DIMS, "embed": get_embeddings()}  # 不配 index 就没有语义检索
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

config_a = {"configurable": {"thread_id": "thread-a", "user_id": "user-1"}}

# 第一轮：agent 还什么都不记得
r = agent.invoke(
    {"messages": [{"role": "user", "content": "Do you know which display mode I prefer?"}]},
    config=config_a,
)
print("[thread-a] 问偏好 ->", r["messages"][-1].content[:100])

# 第二轮：告诉它偏好，agent 会主动调用 manage_memory 存下来
r = agent.invoke(
    {"messages": [{"role": "user", "content": "I prefer dark mode. Remember that."}]},
    config=config_a,
)
print("[thread-a] 告知偏好 ->", r["messages"][-1].content[:100])

# 换一个全新的 thread（短期记忆隔离），长期记忆依然能搜到
config_b = {"configurable": {"thread_id": "thread-b", "user_id": "user-1"}}
r = agent.invoke(
    {"messages": [{"role": "user", "content": "What are my display preferences?"}]},
    config=config_b,
)
print("[thread-b] 跨线程问偏好 ->", r["messages"][-1].content[:200])

# 另一个用户：namespace 隔离，搜不到 user-1 的记忆
config_c = {"configurable": {"thread_id": "thread-c", "user_id": "user-2"}}
r = agent.invoke(
    {"messages": [{"role": "user", "content": "What are my display preferences?"}]},
    config=config_c,
)
print("[thread-c] 另一个用户 ->", r["messages"][-1].content[:200])

print("\nstore 里 user-1 的原始记忆条目：")
for item in store.search(("memories", "user-1")):
    print(" ", item.namespace, item.key[:8], "->", item.value)
