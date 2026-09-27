"""
功能 4: 权限管理 - deepagents 的 FilesystemPermission

create_deep_agent 的 permissions 参数接收一组 FilesystemPermission
规则, 作用于内置的文件系统工具(write_file、read_file 等), 按声明
顺序匹配, 第一条匹配的规则生效, 没有匹配到则默认放行。mode 有三种:

- "deny": 工具直接返回 permission denied, 不会执行
- "allow": 放行(默认)
- "interrupt": 暂停等待人工审批, 内部会自动挂载 HumanInTheLoopMiddleware

注意 paths 是 glob 模式, 按路径段匹配: "/etc/*" 只匹配 /etc 的直接
子路径(如 /etc/config.yml), 不会匹配更深的路径(如
/etc/app/config.yml)。这是实测验证过的行为, 写规则时容易踩这个坑。
"""

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from deepagents import create_deep_agent, FilesystemPermission

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")


def create_guarded_agent():
    """创建一个带文件系统权限规则的 agent, 只用内置的 write_file 工具"""
    llm = ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE_URL)
    return create_deep_agent(
        model=llm,
        system_prompt="You are a file assistant. Use write_file when asked to write files.",
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/etc/*"], mode="deny"),
            FilesystemPermission(operations=["write"], paths=["/tmp/repairs/*"], mode="interrupt"),
        ],
        checkpointer=InMemorySaver(),
    )


def demo_deny(agent):
    """写 /etc 下的文件会被直接拒绝, 不会触发中断"""
    print("1. 写入 /etc/config.yml (mode=deny)")
    result = agent.invoke({
        "messages": [{"role": "user", "content": "Write the text 'hello' to /etc/config.yml"}]
    }, config={"configurable": {"thread_id": "deny-demo"}})
    print(f"   {result['messages'][-1].content}\n")


def demo_interrupt(agent):
    """写 /tmp/repairs 下的文件会暂停等待审批, 这里直接批准"""
    print("2. 写入 /tmp/repairs/fix.txt (mode=interrupt)")
    config = {"configurable": {"thread_id": "interrupt-demo"}}
    result = agent.invoke({
        "messages": [{"role": "user", "content": "Write the text 'patched' to /tmp/repairs/fix.txt"}]
    }, config=config)

    if "__interrupt__" in result:
        action = result["__interrupt__"][0].value["action_requests"][0]
        print(f"   中断: 工具 {action['name']} 请求执行 {action['args']}, 等待审批...")
        result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)

    print(f"   {result['messages'][-1].content}\n")


def main():
    """Main function"""
    print("文件系统权限示例\n")

    if not LLM_API_KEY or "your-key" in LLM_API_KEY.lower():
        print("需要在 .env 中配置有效的 LLM_API_KEY")
        return

    agent = create_guarded_agent()
    demo_deny(agent)
    demo_interrupt(agent)


if __name__ == "__main__":
    main()
