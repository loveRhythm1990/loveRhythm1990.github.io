"""
功能 5: 上下文管理 - checkpointer 持久化 + SummarizationMiddleware

deepagents/LangChain 的上下文管理靠两个真实机制, 不是自己维护一个
消息列表:

- checkpointer: 给 create_deep_agent 传一个 checkpointer(比如
  InMemorySaver), 每次 invoke() 时带上相同的 thread_id, 图状态
  (包括完整对话历史)会在调用之间自动持久化, 不需要手动管理。
- SummarizationMiddleware: 监控消息数量或 token 数, 达到 trigger
  阈值后自动把旧消息压缩成摘要, 只保留最近 keep 指定的消息量, 防止
  长对话把上下文窗口撑爆。摘要生成失败时会直接抛错, 不会伪造摘要。

以下用极小的 trigger 阈值(messages=4)人为触发压缩, 便于在几轮对话
内就能观察到效果; 实际项目里阈值应该按模型的上下文窗口设置得大得多。
"""

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents.middleware import SummarizationMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from deepagents import create_deep_agent

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")


def create_context_managed_agent():
    """创建一个带自动摘要中间件的 agent, checkpointer 负责跨轮持久化"""
    llm = ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE_URL)
    return create_deep_agent(
        model=llm,
        system_prompt="You are a helpful assistant. Keep answers to one short sentence.",
        middleware=[
            SummarizationMiddleware(model=llm, trigger=("messages", 4), keep=("messages", 2))
        ],
        checkpointer=InMemorySaver(),
    )


def main():
    """Main function"""
    print("上下文管理示例\n")

    if not LLM_API_KEY or "your-key" in LLM_API_KEY.lower():
        print("需要在 .env 中配置有效的 LLM_API_KEY")
        return

    agent = create_context_managed_agent()
    config = {"configurable": {"thread_id": "demo-session"}}

    print("1. 连续对话, 观察消息数量在触发阈值后不再无限增长")
    facts = [
        "My favorite color is blue.",
        "I have a dog named Rex.",
        "I live in Berlin.",
        "My job is a chef.",
    ]
    for fact in facts:
        result = agent.invoke({"messages": [{"role": "user", "content": fact}]}, config=config)
        print(f"   已说: {fact!r}, 当前消息数: {len(result['messages'])}")

    print("\n2. 触发摘要之后, 再问几轮之前的事实, 验证信息没有丢失")
    result = agent.invoke({"messages": [{"role": "user", "content": "What city do I live in?"}]}, config=config)
    print(f"   回答: {result['messages'][-1].content}")
    print(f"   当前消息数: {len(result['messages'])}")


if __name__ == "__main__":
    main()
