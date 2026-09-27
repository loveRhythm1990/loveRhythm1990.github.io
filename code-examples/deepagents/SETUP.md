# 环境设置指南

## Python 虚拟环境

### 什么是虚拟环境?

虚拟环境是一个隔离的 Python 环境, 让你可以:
- 项目间不互相影响
- 管理项目依赖
- 便于部署和共享

### 创建虚拟环境

```bash
# 进入 deepagents 目录
cd code-examples/deepagents

# 创建虚拟环境 (在该目录中)
python -m venv .venv
```

**问题排查**:
```bash
# 如果提示 python 找不到
python3 -m venv .venv

# 确认 Python 版本 (需要 3.7+)
python --version
```

### 激活虚拟环境

**macOS / Linux**
```bash
source .venv/bin/activate
```

**Windows (Command Prompt)**
```bash
.venv\Scripts\activate.bat
```

**Windows (PowerShell)**
```bash
.venv\Scripts\Activate.ps1
```

### 验证激活

成功激活后, 提示符前会显示 `(.venv)`:

```bash
(.venv) $ python --version
(.venv) $ pip list
```

### 安装依赖

```bash
pip install -r requirements.txt
```

### 退出虚拟环境

```bash
deactivate
```

### 重新激活

```bash
# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

---

## LLM 配置

### 配置文件位置

**文件必须在 deepagents 目录中:**

```bash
code-examples/deepagents/
├── .venv/              # 虚拟环境
├── .env                # ← 配置文件在这里
├── .env.example        # 模板
└── ...
```

### 复制配置文件

```bash
# 在 deepagents 目录中执行
cp .env.example .env
```

### 配置 DeepSeek (推荐)

编辑 `.env` 文件:

```env
LLM_API_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-your-key-here
```

[获取 DeepSeek API Key](https://platform.deepseek.com)

### 其他 LLM

```env
# OpenAI
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-your-key-here

# Azure OpenAI
LLM_API_BASE_URL=https://your-resource.openai.azure.com/
LLM_MODEL=your-deployment-name
LLM_API_KEY=your-key-here
```

---

## 快速检查清单

- [ ] Python 3.7+ 已安装
- [ ] 在 deepagents 目录中
- [ ] 虚拟环境已创建 (`.venv` 目录存在)
- [ ] 虚拟环境已激活 (提示符前有 `(.venv)`)
- [ ] 依赖已安装 (`pip list` 显示 deepagents 等)
- [ ] `.env` 文件已创建并配置
- [ ] API Key 已填入

---

## 常见问题

### Q: 如何删除虚拟环境?

```bash
# macOS / Linux
rm -rf .venv

# Windows
rmdir /s .venv
```

### Q: 虚拟环境占用多少空间?

通常 200-500 MB, 依赖于已安装包的数量。

### Q: 可以共享虚拟环境吗?

不推荐。建议:
1. 提交 `requirements.txt` (不提交 `.venv`)
2. 其他人用 `pip install -r requirements.txt` 创建自己的虚拟环境

### Q: 每个项目都需要虚拟环境吗?

推荐这样做。好处:
- 避免依赖冲突
- 便于项目管理
- 更容易共享

### Q: 怎样更新依赖?

```bash
# 激活虚拟环境
source .venv/bin/activate  # macOS/Linux
# 或
.venv\Scripts\activate  # Windows

# 升级包
pip install --upgrade package-name

# 冻结当前依赖列表
pip freeze > requirements.txt
```

---

## 运行项目

```bash
# 1. 进入 deepagents 目录
cd code-examples/deepagents

# 2. 激活虚拟环境
source .venv/bin/activate  # macOS/Linux
# 或
.venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 LLM (编辑 .env)
# LLM_API_BASE_URL=https://api.deepseek.com/v1
# LLM_MODEL=deepseek-chat
# LLM_API_KEY=sk-your-key

# 5. 运行
python main.py
```

---

## 虚拟环境文件夹结构

```
.venv/
├── bin/                    # 可执行文件 (macOS/Linux)
│   ├── activate           # 激活脚本
│   ├── python
│   └── pip
├── Scripts/                # 可执行文件 (Windows)
│   └── activate.bat
├── lib/                    # Python 库
│   └── python3.x/
│       └── site-packages/  # 安装的包
├── pyvenv.cfg
└── ...
```

重点: 只需关心 `.venv/bin/activate` 或 `.venv\Scripts\activate`

---

## 更多帮助

- Python 官方文档: https://docs.python.org/3/tutorial/venv.html
- pip 文档: https://pip.pypa.io/
- DeepSeek API 文档: https://platform.deepseek.com/docs

---

**准备好了?** 运行 `python main.py` 开始! 🚀
