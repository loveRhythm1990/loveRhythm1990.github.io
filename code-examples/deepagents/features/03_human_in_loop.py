"""
功能 3: 人机协同 - 用 deepagents 的 interrupt_on 实现真实的审批中断

deepagents 包的 create_deep_agent 原生支持 interrupt_on 参数(不需要
自己手动拼装 middleware): 传入 interrupt_on={"工具名": {...}} 后,
它会在内部自动挂载 LangChain 的 HumanInTheLoopMiddleware。模型一旦
决定调用被保护的工具, 中间件会在工具真正执行前调用 LangGraph 的
interrupt(), 暂停整个图的执行并把这次请求返回给调用方; 调用方之后
用 Command(resume=...) 把决定送回去, 图会从暂停点继续。这是官方的
人机协同机制, 不是自己在工具里手写的等待逻辑。

注意: create_deep_agent 默认自带一批工具(ls、read_file、write_file、
edit_file、glob、grep、execute、task), 这是 DeepAgents 作为"agent
harness"的标准行为, 不是本示例额外加的。没配置真实的文件系统后端时,
这些工具会返回空结果, 模型有时会先尝试用它们查看目标路径, 这也是
真实行为, 不是本示例的 bug。

真实场景中, Agent 是长期后台运行的进程, 审批方是独立的 API 服务
或人工审批界面(网页、Slack bot、CLI 工具等)。本示例用
multiprocessing 启动两个真实的操作系统进程还原这个模型:

- Agent 进程: 调用 agent.invoke(), 收到中断后把 HITLRequest 写入
  共享文件, 再阻塞轮询决定文件, 收到后用 Command(resume=...) 恢复
  执行。checkpointer 用 InMemorySaver 即可, 因为暂停和恢复都发生
  在同一个 Agent 进程内, 不需要跨进程持久化图状态。
- Approver 进程: 独立进程, 轮询共享文件里的待审批请求, 写回决定。

简化点: Approver 收到请求后自动批准, 真实场景中它应该是一个被动
的 API 服务, 等待用户从另一个客户端发来 HTTP 请求驱动决定, 见下方
ApproverProcess.execute 里的说明和 curl 示例。
"""

import json
import os
import time
from pathlib import Path
from typing import Dict
from multiprocessing import Process

from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from deepagents import create_deep_agent

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")

# 共享状态存储目录, 两个进程都通过读写这里的文件通信
STATE_DIR = Path("/tmp/deepagents_approval")
STATE_DIR.mkdir(exist_ok=True)

PENDING_FILE = STATE_DIR / "pending.json"
APPROVED_FILE = STATE_DIR / "approved.json"


def load_json_file(filepath: Path) -> Dict:
    """从文件加载 JSON"""
    if filepath.exists():
        return json.loads(filepath.read_text())
    return {}


def save_json_file(filepath: Path, data: Dict):
    """保存数据到 JSON 文件"""
    filepath.write_text(json.dumps(data, indent=2))


# 工具本身不含任何审批逻辑, 审批完全由下面的中间件负责拦截
@tool
def delete_logs(path: str, size_gb: int) -> str:
    """Delete old log files at the given path to free disk space."""
    return f"Deleted logs at {path}, freed {size_gb}GB"


def create_ops_agent():
    """创建真实的 deep agent, 用 interrupt_on 拦截 delete_logs 调用"""
    llm = ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE_URL)
    return create_deep_agent(
        model=llm,
        tools=[delete_logs],
        system_prompt=(
            "You are an ops assistant. When asked to free disk space, call "
            "delete_logs directly with the path and size given in the request. "
            "Do not inspect the filesystem first — this environment has no real "
            "filesystem backend, so ls/read_file/grep will not show real data."
        ),
        interrupt_on={"delete_logs": {"allowed_decisions": ["approve", "reject"]}},
        # 暂停和恢复都发生在本进程内, InMemorySaver 足够保存图状态
        checkpointer=InMemorySaver(),
    )


def wait_for_decision(req_id: str, timeout: int = 60) -> Dict:
    """轮询等待 Approver 进程写回的决定"""
    start = time.time()
    while time.time() - start < timeout:
        approved = load_json_file(APPROVED_FILE)
        if req_id in approved:
            return approved[req_id]
        time.sleep(0.5)
    return {"type": "reject", "message": "approval timed out"}


def run_agent_process():
    """独立进程入口: 调用真实 Agent, 收到 HumanInTheLoopMiddleware 的中断后
    暂停, 把请求交给 Approver 进程, 收到决定后用 Command(resume=...) 恢复"""
    print("[Agent] 启动\n")
    try:
        agent = create_ops_agent()
        config = {"configurable": {"thread_id": "demo-1"}}

        result = agent.invoke(
            {"messages": [
                {"role": "user", "content": "Disk space is low, please delete old logs at /var/log (150GB) to free space"}
            ]},
            config=config,
        )

        if "__interrupt__" in result:
            hitl_request = result["__interrupt__"][0].value
            action = hitl_request["action_requests"][0]

            req_id = f"req_{int(time.time() * 1000)}"
            pending = load_json_file(PENDING_FILE)
            pending[req_id] = {"operation": action["name"], "details": action["args"]}
            save_json_file(PENDING_FILE, pending)
            print(f"[Agent] 中间件触发中断, 请求审批: {action['name']} {action['args']} (id={req_id})")

            decision = wait_for_decision(req_id)

            # Command(resume=...) 是发给 LangGraph 的图, 不是发给 LLM 的。
            # 中间件里暂停的地方是 interrupt(hitl_request)["decisions"] 这一行,
            # 这次 resume 会让那次调用直接返回 {"decisions": [decision]},
            # 而不是重新跑一遍图。LLM 本身感知不到 Command/resume/decision
            # 这些概念: 如果 decision 是 approve, 中间件原样放行原来的
            # tool_call, delete_logs 真正执行, 模型看到的是正常的
            # ToolMessage; 如果是 reject, 中间件根本不执行工具, 而是伪造
            # 一条 status="error" 的 ToolMessage(同一个 tool_call_id)塞进
            # 消息列表。两种情况在消息结构上完全一样, 模型只是把它当成
            # "这次工具调用的返回结果"来处理, 分不清结果是真实执行的还是
            # 中间件编出来的拒绝理由。
            result = agent.invoke(Command(resume={"decisions": [decision]}), config=config)

        print(f"\n[Agent] 最终回复: {result['messages'][-1].content}\n")
    except Exception as e:
        print(f"[Agent] 错误: {e}")


# ==================== Approver 进程 ====================

class ApproverProcess:
    """审批进程 - 处理审批请求"""

    def list_pending_requests(self) -> Dict:
        """列出所有待审批请求"""
        return load_json_file(PENDING_FILE)

    def approve(self, req_id: str):
        """批准请求, 写回的决定格式和 HumanInTheLoopMiddleware 的 Decision 一致"""
        pending = load_json_file(PENDING_FILE)
        approved = load_json_file(APPROVED_FILE)

        if req_id in pending:
            pending.pop(req_id)
            approved[req_id] = {"type": "approve"}
            save_json_file(PENDING_FILE, pending)
            save_json_file(APPROVED_FILE, approved)
            return True
        return False

    def reject(self, req_id: str, reason: str):
        """拒绝请求"""
        pending = load_json_file(PENDING_FILE)
        approved = load_json_file(APPROVED_FILE)

        if req_id in pending:
            pending.pop(req_id)
            approved[req_id] = {"type": "reject", "message": reason}
            save_json_file(PENDING_FILE, pending)
            save_json_file(APPROVED_FILE, approved)
            return True
        return False

    def execute(self):
        """审批进程执行流程: 轮询等待新请求, 出现后立即处理"""
        print("[Approver] 启动, 等待审批请求...")

        seen = set()
        deadline = time.time() + 60
        while time.time() < deadline:
            pending = self.list_pending_requests()
            new_ids = set(pending) - seen

            for req_id in new_ids:
                req = pending[req_id]
                print(f"[Approver] 收到请求 {req_id}")
                print(f"[Approver] 操作: {req['operation']}, 详情: {req['details']}")

                # 简化点: 这里直接调用 self.approve() 自动批准。
                # 真实场景中这一步不存在——审批方是被动的 API 服务,
                # 在这里等待的应是用户发来的 HTTP 请求, 例如:
                #   curl -X POST http://approver-service/approve \
                #     -d '{"approval_id": "req_xxx", "decision": "approved"}'
                # 由这个请求驱动 approve()/reject(), 而不是代码自己决定结果。
                self.approve(req_id)
                print(f"[Approver] 已批准 {req_id}\n")
                seen.add(req_id)
                return  # 演示只处理一个请求就退出

            time.sleep(0.3)


def run_approver_process():
    """独立进程入口: 运行审批器"""
    ApproverProcess().execute()


def main():
    """用 multiprocessing 启动两个真实的独立进程"""
    print("两进程人机协同模型: HumanInTheLoopMiddleware 触发中断, Approver 独立处理\n")

    if not LLM_API_KEY or "your-key" in LLM_API_KEY.lower():
        print("需要在 .env 中配置有效的 LLM_API_KEY 才能运行真实 Agent")
        return

    # 清空之前遗留的状态文件
    PENDING_FILE.unlink(missing_ok=True)
    APPROVED_FILE.unlink(missing_ok=True)

    # Approver 先启动, 持续轮询等待请求(模拟一直运行的审批服务)
    approver_proc = Process(target=run_approver_process)
    agent_proc = Process(target=run_agent_process)

    approver_proc.start()
    time.sleep(0.5)  # 确保 approver 先进入轮询状态
    agent_proc.start()

    agent_proc.join()
    approver_proc.join()


if __name__ == "__main__":
    main()
