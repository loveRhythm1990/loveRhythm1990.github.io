"""
功能 6: 诊断工作流 - 主 agent 自主委派给子 agent

create_deep_agent 的 subagents 参数注册的每个子 agent, 都通过内置的
task 工具被主 agent 调用, 不需要在 system_prompt 里手写"遇到 X 就
委派给 Y"这类规则。主 agent 只看每个子 agent 的 description, 自己
决定要不要调用、调用几次、传什么任务描述。下面的例子没有告诉模型
"请委派给 db-specialist", 只给了一个包含两个问题的事件描述, 模型
自己决定拆成两次 task 调用分别委派。

打印每一步的 AIMessage 里的 tool_calls, 就能看到这个委派过程,
而不需要额外的日志系统。
"""

import os
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")


@tool
def check_db_connectivity(host: str, port: int) -> dict:
    """Check database connectivity to the given host and port"""
    return {"host": host, "port": port, "status": "unreachable"}


@tool
def check_disk_usage(path: str) -> dict:
    """Check disk usage percentage at the given path"""
    return {"path": path, "usage_percent": 92}


def create_incident_agent():
    """创建带两个子 agent 的主 agent, 委派逻辑完全由模型自主决定"""
    llm = ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE_URL)
    return create_deep_agent(
        model=llm,
        system_prompt="You are an incident commander investigating a production issue.",
        subagents=[
            {
                "name": "db-specialist",
                "description": "Diagnoses database connectivity problems.",
                "tools": [check_db_connectivity],
            },
            {
                "name": "disk-specialist",
                "description": "Diagnoses disk space problems.",
                "tools": [check_disk_usage],
            },
        ],
    )


def print_tool_calls(messages):
    """打印每一条 AIMessage 发出的工具调用, 用来观察委派过程"""
    for msg in messages:
        if msg.__class__.__name__ == "AIMessage" and getattr(msg, "tool_calls", None):
            for call in msg.tool_calls:
                print(f"   调用工具: {call['name']}, 参数: {call['args']}")


def main():
    """Main function"""
    print("诊断工作流示例: 主 agent 自主委派给子 agent\n")

    if not LLM_API_KEY or "your-key" in LLM_API_KEY.lower():
        print("需要在 .env 中配置有效的 LLM_API_KEY")
        return

    agent = create_incident_agent()
    result = agent.invoke({
        "messages": [{"role": "user", "content": (
            "Incident: the app cannot connect to the database at db.internal:5432, "
            "and disk usage on /var looks high. Investigate both."
        )}]
    })

    print("委派过程:")
    print_tool_calls(result["messages"])

    print(f"\n最终结论: {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
