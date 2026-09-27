"""
DeepAgents 完整示例: 技术问题诊断系统
==================================

用真正的 deepagents 包(create_deep_agent)展示一个多步诊断工作流,
以及 subagents、permissions、interrupt_on、memory 几个进阶参数的
真实用法。所有参数都对照已安装的 deepagents 包签名和文档字符串核实
过, 不是凭印象编的。

场景: 用户报告数据库连接失败, 系统自动:
1. 分析日志
2. 检查数据库连接
3. 采集系统指标
4. 生成诊断报告
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent, FilesystemPermission

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")


# ============================================
# 第一部分: 诊断工具的业务逻辑(纯函数, 和 deepagents 无关)
# ============================================

def analyze_logs(log_path: str) -> dict:
    """分析日志文件, 提取错误信息"""
    return {
        "log_file": log_path,
        "errors_found": [
            "Connection timeout at 2026-09-26 10:23:45",
            "Database pool exhausted",
        ],
        "summary": "数据库连接池耗尽导致连接失败"
    }


def check_database_status(host: str, port: int) -> dict:
    """检查数据库连接状态"""
    return {
        "host": host,
        "port": port,
        "status": "unreachable",
        "recommendation": "检查网络连接和防火墙配置"
    }


def get_system_metrics() -> dict:
    """获取系统指标"""
    return {
        "cpu_usage": "65%",
        "memory_usage": "82%",
        "disk_usage": "45%",
        "timestamp": datetime.now().isoformat()
    }


# ============================================
# 第二部分: 基础诊断 Agent
# ============================================

@tool
def analyze_error_logs(log_path: str) -> dict:
    """Analyze an error log file and extract findings"""
    return analyze_logs(log_path)


@tool
def check_db_connectivity(host: str, port: int) -> dict:
    """Check database connectivity to the given host and port"""
    return check_database_status(host, port)


@tool
def fetch_system_health() -> dict:
    """Fetch current system health metrics"""
    return get_system_metrics()


def create_diagnostic_agent():
    """创建诊断 agent, 只带自定义诊断工具, 不涉及审批或子 agent"""
    llm = ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE_URL)
    return create_deep_agent(
        model=llm,
        tools=[analyze_error_logs, check_db_connectivity, fetch_system_health],
        system_prompt=(
            "You are a technical diagnostics expert. When given a problem, "
            "call the diagnostic tools to gather evidence before concluding. "
            "Do not use filesystem tools like ls/read_file for this task."
        ),
    )


def run_diagnostic_workflow(problem_description: str):
    """执行一次诊断工作流"""
    agent = create_diagnostic_agent()
    result = agent.invoke({
        "messages": [{"role": "user", "content": problem_description}]
    })
    return result["messages"][-1].content


# ============================================
# 第三部分: 进阶特性 - subagents、permissions、interrupt_on、memory
# ============================================

@tool
def apply_fix(action: str) -> str:
    """Apply a remediation action, e.g. restart a service"""
    return f"Applied fix: {action}"


def create_advanced_agent():
    """
    展示 deepagents 的几个进阶参数, 均为 create_deep_agent 的真实参数:

    - subagents: 声明式子 agent, 通过内置的 task 工具被主 agent 调用
    - permissions: 文件系统权限规则, mode="interrupt" 的规则会自动
      挂载 HumanInTheLoopMiddleware, 暂停等待审批
    - interrupt_on: 对自定义工具(而非文件系统操作)设置审批
    - memory: 启动时加载 AGENTS.md 一类的记忆文件, 拼进系统提示
    """
    llm = ChatOpenAI(model=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_API_BASE_URL)

    return create_deep_agent(
        model=llm,
        tools=[check_db_connectivity, apply_fix],
        system_prompt="You are a senior SRE assistant handling incidents.",

        # 声明式子 agent, 通过 task 工具委派任务
        subagents=[
            {
                "name": "db-specialist",
                "description": "Handles database connectivity diagnostics",
                "system_prompt": "You specialize in diagnosing database connectivity issues.",
                "tools": [check_db_connectivity],
            }
        ],

        # 文件系统权限规则: 禁止写 /etc, 读写 /tmp/repairs 前需要审批
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/etc/*"], mode="deny"),
            FilesystemPermission(operations=["write"], paths=["/tmp/repairs/*"], mode="interrupt"),
        ],

        # 自定义工具 apply_fix 需要人工审批才能执行
        interrupt_on={"apply_fix": {"allowed_decisions": ["approve", "reject"]}},

        # 启动时加载记忆文件, 通过 invoke(files={...}) 提供内容
        memory=["/memory/AGENTS.md"],
    )


# ============================================
# 第四部分: 主程序入口
# ============================================

def main():
    """Main function"""
    if not LLM_API_KEY or "your-key" in LLM_API_KEY.lower():
        print("需要在 .env 中配置有效的 LLM_API_KEY")
        return

    print("示例 1: 基础诊断工作流\n")
    problem = "Database connections have been failing since 10am with 'Connection refused' errors."
    print(run_diagnostic_workflow(problem))

    print("\n\n示例 2: 进阶 agent 结构(subagents + permissions + interrupt_on + memory)\n")
    advanced_agent = create_advanced_agent()
    print(f"Agent 已创建: {type(advanced_agent)}")
    print("包含: 1 个子 agent(db-specialist), 2 条文件系统权限规则,")
    print("      apply_fix 工具需审批, 启动时加载 /memory/AGENTS.md")


if __name__ == "__main__":
    main()
