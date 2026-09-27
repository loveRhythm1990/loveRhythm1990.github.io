# DeepAgents 完整指南

模块化的 DeepAgents 学习项目, 支持任何 OpenAI 兼容的 LLM。所有代码都基于真实安装的
`deepagents` 包(通过 `create_deep_agent`)编写并实际运行验证过, 不是伪代码。

## 快速开始(4 步)

### 1. 创建虚拟环境

```bash
# 进入 deepagents 目录
cd code-examples/deepagents

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# macOS/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

# 验证激活(提示符前会显示 (.venv))
(.venv) $
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

如果连接 pypi.org 遇到 SSL/网络问题, 换成国内镜像:

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

### 3. 配置 LLM

在 deepagents 目录中, 编辑 `.env` 文件配置你使用的 LLM:

**DeepSeek(推荐)**
```env
LLM_API_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-your-key-here
```

**其他兼容服务**
```env
LLM_API_BASE_URL=https://your-endpoint.com/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-key
```

详细配置见: `.env.example`

### 4. 运行

```bash
python main.py
# 选择 0-6 学习不同功能
```

---

## 虚拟环境管理

### 退出虚拟环境
```bash
deactivate
```

### 重新激活
```bash
# macOS/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 删除虚拟环境
```bash
rm -rf .venv  # macOS/Linux
rmdir /s .venv  # Windows
```

---

## 7 个功能模块

| 序号 | 功能 | 学习内容 | 用到的真实 API |
|------|------|--------|------|
| 0 | 完整诊断系统 | 自定义工具 + subagents + permissions + interrupt_on + memory | `create_deep_agent` 全部进阶参数 |
| 1 | 基础示例 | 最小化 deep agent | `create_deep_agent(tools=...)` |
| 2 | 工具定义 | 如何定义和调用工具 | `langchain.tools.tool` |
| 3 | 人机协同 | 两个独立进程, 中间件触发中断等待审批 | `interrupt_on` + `HumanInTheLoopMiddleware` |
| 4 | 权限管理 | 文件系统读写规则, deny/interrupt 两种模式 | `FilesystemPermission` |
| 5 | 上下文管理 | 跨轮持久化 + 自动摘要 | `checkpointer` + `SummarizationMiddleware` |
| 6 | 诊断工作流 | 主 agent 自主委派给子 agent | `subagents` + 内置 `task` 工具 |

### 推荐学习路径

**1 小时**: `1 -> 2` 理解基础
**2 小时**: `1 -> 2 -> 3 -> 4` 理解审批和权限
**3 小时**: `0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6` 完整学习

---

## 代码架构

```
deepagents/
├── main.py                    # 交互菜单
├── .env.example               # 配置模板
├── INDEX.md                   # 本文件
├── SETUP.md                   # 环境搭建细节
│
└── features/
    ├── 00_diagnostic_agent.py # 基础诊断 agent + 进阶参数示例
    ├── 01_basic_agent.py      # 最小化 deep agent
    ├── 02_tools_definition.py # 工具定义模式
    ├── 03_human_in_loop.py    # 人机协同核心, 两进程模型
    ├── 04_permissions.py      # 文件系统权限规则
    ├── 05_context_management.py # 上下文管理
    ├── 06_diagnostic_workflow.py # 子 agent 自主委派
    ├── API_EXAMPLES.md        # API 请求示例
    └── README.md
```

---

## 核心概念速览

### Agent Harness

DeepAgents 是一个 "Agent Harness", 预集成的 agent 框架。`create_deep_agent` 默认就带一批
内置工具(`ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`execute`、`task`),
自定义 `tools` 参数是在这些工具之上追加的, 不是替换。进阶能力(权限、审批、子 agent、记忆、
自动摘要)都是通过给 `create_deep_agent` 传参数开启的, 不需要自己实现。

### 人机协同(Human-in-Loop)

`create_deep_agent` 的 `interrupt_on` 参数拦截指定工具的调用。模型一旦决定调用被保护的
工具, 内部会自动挂载的 `HumanInTheLoopMiddleware` 会在工具真正执行前调用 LangGraph 的
`interrupt()`, 暂停整个图的执行:

```python
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver

agent = create_deep_agent(
    model=llm,
    tools=[delete_logs],
    interrupt_on={"delete_logs": {"allowed_decisions": ["approve", "reject"]}},
    checkpointer=InMemorySaver(),  # 暂停/恢复需要 checkpointer 保存图状态
)
```

工作流:
1. Agent 分析问题, 决定调用受保护的工具
2. 中间件在工具执行前触发 `interrupt()`, `agent.invoke()` 返回并带上 `__interrupt__` 字段
3. 调用方(通常是一个常驻进程)把这个中断请求转给审批方
4. 审批方给出决定后, 调用方用 `Command(resume={"decisions": [...]})` 恢复执行

注意: `Command(resume=...)` 是发给 LangGraph 图的, 不是发给 LLM 的。LLM 感知不到这套
中断机制, 它只会看到两种结果: 批准后工具真的执行, 收到正常的 `ToolMessage`; 拒绝后工具
没有执行, 收到一条中间件伪造的 `status="error"` 的 `ToolMessage`。两者在消息结构上完全
一样, 模型只是把它当作"这次工具调用的结果"处理。

**用户如何通过 API 发送审批决定?**

真实场景中, 驱动 `Command(resume=...)` 的应该是一个等待 HTTP 请求的 API 服务, 不是像
本示例这样自动批准:

```bash
# 批准操作
curl -X POST http://approver-service/approve \
  -d '{"approval_id": "req_12345", "decision": "approved"}'

# 拒绝操作
curl -X POST http://approver-service/approve \
  -d '{"approval_id": "req_12345", "decision": "rejected", "reason": "需要更多信息"}'
```

详见:
- **功能 3**: `features/03_human_in_loop.py`, 用 `multiprocessing` 启动两个真实的操作系统
  进程还原 "Agent 进程等待审批, 审批进程独立处理" 的场景
- **features/API_EXAMPLES.md**: 完整的多语言 API 请求示例

### 权限管理

`permissions` 参数接收一组 `FilesystemPermission` 规则, 作用于内置的文件系统工具。按声明
顺序匹配, 第一条命中的规则生效, 没匹配到则默认放行:

```python
from deepagents import create_deep_agent, FilesystemPermission

agent = create_deep_agent(
    model=llm,
    permissions=[
        FilesystemPermission(operations=["write"], paths=["/etc/*"], mode="deny"),
        FilesystemPermission(operations=["write"], paths=["/tmp/repairs/*"], mode="interrupt"),
    ],
)
```

`mode` 有三种: `"allow"`(默认放行)、`"deny"`(工具直接返回 permission denied)、
`"interrupt"`(暂停等待审批, 效果等同于对这次文件操作单独设置了 `interrupt_on`)。

实测踩过的坑: `paths` 是按路径段匹配的 glob, `"/etc/*"` 只匹配 `/etc` 的直接子路径(如
`/etc/config.yml`), 不会匹配更深的路径(如 `/etc/app/config.yml`)。写规则时要按实际目录
层级设置, 不能想当然地认为一条规则能挡住整棵子树。

详见: `features/04_permissions.py`

### 上下文管理

跨轮持久化靠 `checkpointer`, 不需要自己维护消息列表: 给 `create_deep_agent` 传一个
checkpointer, 之后每次 `invoke()` 时带上相同的 `thread_id`, 图状态(包括完整对话历史)会
在调用之间自动持久化。长对话的上下文控制靠 `SummarizationMiddleware`: 监控消息数量或
token 数, 达到 `trigger` 阈值后自动把旧消息压缩成摘要, 只保留最近 `keep` 指定的消息量。

```python
from deepagents import create_deep_agent
from langchain.agents.middleware import SummarizationMiddleware
from langgraph.checkpoint.memory import InMemorySaver

agent = create_deep_agent(
    model=llm,
    middleware=[SummarizationMiddleware(model=llm, trigger=("messages", 40), keep=("messages", 20))],
    checkpointer=InMemorySaver(),
)
```

`InMemorySaver` 只在进程存活期间有效, 进程重启就丢失; 真正跨进程/跨重启持久化需要换成
数据库支持的 checkpointer(比如 Postgres、Redis 一类, 取决于安装了哪个
`langgraph-checkpoint-*` 包), 用法和 `InMemorySaver` 一致, 只是构造参数不同。

详见: `features/05_context_management.py`

---

## 支持的 LLM

支持任何 OpenAI 兼容的 LLM 服务:

- **DeepSeek** 推荐
- **OpenAI**、**Azure**
- **Ollama**、**vLLM** 本地运行
- 其他兼容服务

只需修改 `.env` 文件, 代码无需改动。

---

## 常见问题

### Q: 虚拟环境激活后还是找不到依赖?
**A**:
```bash
# 确认已激活虚拟环境(提示符前有 (.venv))
(.venv) $ pip list  # 应该只显示虚拟环境中的包
# 重新安装
(.venv) $ pip install -r requirements.txt
```

### Q: 这些代码能直接用吗?
**A**: 每个示例都是实际可运行的代码, 但里面的业务工具(比如 `check_db_connectivity`、
`analyze_error_logs`)返回的是写死的模拟数据。要真正使用需要:
1. 把这些工具换成真实的实现(连真实数据库、读真实日志)
2. 把 Approver 进程换成真正等待人工输入的 API 服务, 而不是自动批准
3. 把 `InMemorySaver` 换成能跨进程/跨重启持久化的 checkpointer

### Q: 如何在后台运行 Agent, 让另一个程序发送审批决定?
**A**: 见功能 3。核心是: Agent 进程调用 `interrupt_on` 保护的工具时会暂停并返回
`__interrupt__`, 需要一个常驻的调用方把这个请求转发出去(写共享存储、发消息队列等), 再由
外部的审批服务通过 API 收到决定后, 让这个常驻调用方执行
`agent.invoke(Command(resume=...), config=同一个thread_id)` 恢复执行。

### Q: 想看子 agent 自主委派任务, 而不是自己写死委派逻辑, 看哪个例子?
**A**: 功能 6。`subagents` 里每个子 agent 只需要给出 `name` 和 `description`, 主 agent
会根据描述自己决定要不要通过内置的 `task` 工具委派任务, 委派几次、怎么措辞任务描述都是
模型自己决定的。

---

## 关键文件

| 文件 | 说明 |
|------|------|
| `main.py` | 交互菜单入口 |
| `.env.example` | 配置模板(复制为 .env) |
| `features/00_diagnostic_agent.py` | 基础诊断 agent + 进阶参数示例 |
| `features/03_human_in_loop.py` | 人机协同核心, 两进程模型 |
| `features/API_EXAMPLES.md` | API 请求示例 |

---

## 快速参考

### 基础 deep agent

```python
import os
from dotenv import load_dotenv
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langchain.tools import tool

load_dotenv()

@tool
def my_tool(param: str) -> str:
    """工具描述"""
    return "结果"

llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "gpt-4o"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1"),
)

agent = create_deep_agent(model=llm, tools=[my_tool])

result = agent.invoke({"messages": [{"role": "user", "content": "任务描述"}]})
print(result["messages"][-1].content)
```

### 添加人机协同

```python
from langgraph.checkpoint.memory import InMemorySaver

agent = create_deep_agent(
    model=llm,
    tools=[my_tool],
    interrupt_on={"my_tool": {"allowed_decisions": ["approve", "reject"]}},  # <- 添加这行
    checkpointer=InMemorySaver(),  # <- interrupt_on 需要 checkpointer
)
```

### 添加权限控制

```python
from deepagents import FilesystemPermission

agent = create_deep_agent(
    model=llm,
    tools=[my_tool],
    permissions=[
        FilesystemPermission(operations=["write"], paths=["/etc/*"], mode="deny"),
    ],
)
```

### 添加子 agent

```python
agent = create_deep_agent(
    model=llm,
    subagents=[
        {"name": "specialist", "description": "何时应该委派给这个子 agent", "tools": [my_tool]},
    ],
)
```

### 添加自动摘要

```python
from langchain.agents.middleware import SummarizationMiddleware

agent = create_deep_agent(
    model=llm,
    middleware=[SummarizationMiddleware(model=llm, trigger=("messages", 40), keep=("messages", 20))],
    checkpointer=InMemorySaver(),
)
```

---

## 下一步

1. 查看 **SETUP.md** 了解虚拟环境设置
2. 创建虚拟环境并激活
3. 安装依赖: `pip install -r requirements.txt`
4. 配置 `.env` 文件(复制 `.env.example`)
5. 运行 `python main.py`
6. 按顺序学习功能 0-6

---

## 资源链接

- **官方 DeepAgents 文档** https://docs.langchain.com/oss/python/deepagents/overview
- **Ollama** https://ollama.ai
- **vLLM** https://github.com/lm-sys/vllm
- **OpenAI API** https://platform.openai.com/docs

---

**准备好了?** 运行 `python main.py` 开始学习!
