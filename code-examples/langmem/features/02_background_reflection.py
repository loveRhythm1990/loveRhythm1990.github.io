"""功能 2：Background —— 对话后自动提取记忆 + ReflectionExecutor 防抖。

要点：
- create_memory_store_manager 是一个 Runnable：输入对话，自动搜相关记忆、
  让 LLM 提取/更新，最后直接写进 BaseStore。
- ReflectionExecutor 把 memory manager 包成"延迟执行器"：submit(..., after_seconds=N)
  之后，同一线程再有新消息会取消旧任务、以新 payload 重新排队（debounce），
  避免用户连续发消息时反复提取。

运行：python -m features.02_background_reflection
"""

import time

from langgraph.func import entrypoint
from langgraph.store.memory import InMemoryStore
from langmem import ReflectionExecutor, create_memory_store_manager

from features.common import EMBEDDING_DIMS, get_embeddings, get_llm

llm = get_llm()
store = InMemoryStore(index={"dims": EMBEDDING_DIMS, "embed": get_embeddings()})

memory_manager = create_memory_store_manager(
    llm,
    namespace=("memories", "{user_id}"),
)

# 包一层计数器，观察提取真正执行了几次
real_invoke = memory_manager.invoke
run_count = {"n": 0}


def counting_invoke(*args, **kwargs):
    run_count["n"] += 1
    return real_invoke(*args, **kwargs)


memory_manager.invoke = counting_invoke
executor = ReflectionExecutor(memory_manager, store=store)


# 实际应用里提取的应该是"截至目前的完整对话"，而不是最新一条消息，
# 否则被取消的前几次任务里的信息就丢了。这里用一个 buffer 模拟会话历史。
history: list[dict] = []


@entrypoint(store=store)
def chat(message: str):
    response = llm.invoke(message)
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": response.content})
    # 延迟 10 秒再提取；这期间同 thread 的新 submit 会取消旧任务并带上最新完整对话
    executor.submit({"messages": list(history)}, after_seconds=10)
    return response.content


config = {"configurable": {"user_id": "user-1", "thread_id": "t-1"}}

# 模拟用户 1 秒内连发 3 条：前两次提取任务会被取消，只有最后一次真正执行
print("连发 3 条消息（每条都会 submit 一个 3 秒后执行的提取任务）...")
chat.invoke("I like dogs.", config=config)
chat.invoke("My dog's name is Fido.", config=config)
chat.invoke("He is a golden retriever.", config=config)

print("等待 13 秒让防抖窗口过去...")
time.sleep(13)
print(f"\nsubmit 了 3 次，提取实际执行次数：{run_count['n']}（前 2 次被取消）")

print("\n最终提取到的记忆（来自最后一次提取，覆盖完整对话）：")
for item in store.search(("memories", "user-1")):
    print(" ", item.value)

executor.shutdown()
