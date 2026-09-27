"""
功能 1: 基础示例 - 最小化 deep agent

用真正的 deepagents 包(create_deep_agent), 不是通用的
langchain.agents.create_agent。create_deep_agent 默认会自带一批
文件系统工具(ls、read_file、write_file、edit_file、glob、grep)和
execute、task 工具, 这里传入的自定义工具是在这些默认工具之上追加的,
不是替换。
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")


# 自定义工具 - 会追加到 create_deep_agent 的默认工具集之上
@tool
def add_numbers(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


@tool
def multiply_numbers(a: int, b: int) -> int:
    """Multiply two numbers"""
    return a * b


@tool
def get_current_time() -> str:
    """Get current time"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_basic_agent():
    """创建一个带自定义工具的 deep agent"""
    llm = ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE_URL)
    return create_deep_agent(
        model=llm,
        tools=[add_numbers, multiply_numbers, get_current_time],
        system_prompt="You are a math assistant. Use tools to solve problems."
    )


def main():
    """Main function"""
    print("基础 deep agent 示例\n")

    if not LLM_API_KEY or "your-key" in LLM_API_KEY.lower():
        print("需要在 .env 中配置有效的 LLM_API_KEY")
        return

    try:
        agent = create_basic_agent()
        result = agent.invoke({
            "messages": [
                {"role": "user", "content": "Calculate 5 + 3, then multiply by 4"}
            ]
        })
        print(f"结果: {result['messages'][-1].content}")

    except Exception as e:
        print(f"错误: {e}")


if __name__ == "__main__":
    main()
