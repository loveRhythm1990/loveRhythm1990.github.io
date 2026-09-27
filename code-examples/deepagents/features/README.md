# Features（功能模块）

这个目录包含 7 个功能演示模块 + API 文档。

## 模块列表

| 序号 | 文件 | 说明 |
|------|------|------|
| 0 | `00_diagnostic_agent.py` | 生产级诊断系统（参考代码） |
| 1 | `01_basic_agent.py` | 基础：最小化 Agent |
| 2 | `02_tools_definition.py` | 工具：定义和使用工具 |
| 3 | `03_human_in_loop.py` | 人机协同：审批流程 ⭐ |
| 4 | `04_permissions.py` | 权限：文件系统隔离 |
| 5 | `05_context_management.py` | 内存：会话持久化 |
| 6 | `06_diagnostic_workflow.py` | 系统：多步工作流 |

## 文档

| 文件 | 说明 |
|------|------|
| `API_EXAMPLES.md` | 人机协同 API 请求示例（curl、Python、JS、Go 等） |
| `README.md` | 本文件 |

## 快速使用

### 通过菜单运行（推荐）
```bash
cd ..
python main.py
# 选择 0-6
```

### 直接运行单个模块
```bash
python -m features.03_human_in_loop
```

## common.py

所有模块共用的工具库：
- LLM 配置管理
- 打印工具函数
- API 检查函数

## 人机协同 API

查看 **API_EXAMPLES.md** 获取完整的 API 请求示例：

- ✅ Curl 命令行
- ✅ Python requests
- ✅ JavaScript fetch / axios
- ✅ Go
- ✅ 错误处理
- ✅ 生产最佳实践
- ✅ 集成示例（Web UI、CLI、Slack Bot）

---

**详细说明见父目录的 INDEX.md** 📖
