---
layout: post
title: "以 pi 为例：如何设计一个 Harness 系统"
subtitle: "循环、工具、状态、扩展与验收"
date: 2026-10-19 16:00:00 +0800
author: "weak old dog"
header-img-credit: false
tags:
    - Harness
---

模型只做一件事：根据当前上下文生成下一步。让它持续工作、调用工具、保存状态、从错误里恢复的，是模型之外的那层系统。这层系统通常叫 **harness**。

pi 的 README 第一句就是这么定义自己的：

> Pi is a minimal, extensible agent harness that you can make your own.

pi 默认以终端编码智能体的形态发行：npm 包叫 `pi-coding-agent`，内置工具是 `bash`、`read`、`write`、`edit`、`grep`、`find`、`ls`。但官方把它称为 harness，而不是“模型的外壳”。这个措辞值得注意：**工具域是代码，架构不是代码专用的**。

我用 Python 按同样的结构写了一个精简实现 [mini-pi](https://github.com/loveRhythm1990/mini-pi)，九章，从一百多行的主循环，到工具、会话、扩展、Skill、CLI 和评测。本文不按章节复述，而是按 harness 的职责回答两个问题：**它包含哪些部件，每个部件要做什么。**

先给一条主线：harness 同时维护两条路径——**信息路径**（模型看到什么、结果如何表示、历史如何保留）和**行动路径**（调用是否真的执行、失败或中断后环境处于什么状态）。后面每一节都会回到这两条路径。

```text
用户请求 ──→ 组织上下文 ──→ 调用模型 ──→ 解析工具调用
                  ↑                              │
                  │                              ↓
              写回结果 ←── 执行工具 / 处理错误 ←──┘
```

这个循环很短，但它连接的组件并不简单。

## 一、harness 与 prompt

**Prompt** 是任务和行为指令；**上下文**是一次模型调用实际收到的全部信息，包括指令、对话、代码、工具定义和工具结果；**harness** 是围绕模型工作的运行系统，负责构建上下文、分发工具、保存状态、处理中断和展示结果。

三者混为一谈，会带来一个常见误判：以为“提示词写得好”就能替代执行逻辑。实际上，提示词可以要求模型谨慎，但不能代替进程清理、访问控制或状态恢复。

举个最小的例子：模型回复“文件已删除”，但工具执行失败。信息路径上写的是成功，行动路径上文件还在；如果 harness 只相信文本，下一步就会基于错误状态继续。两条路径必须由执行结果对齐，而不是由模型的叙述对齐。

从部件上看，一个能用的 harness 至少有这些：

| 部件 | 职责 |
|---|---|
| 主循环 | 决定何时调用模型、何时执行工具、何时停止 |
| 模型适配 | 把不同厂商的消息与工具协议收敛成一种内部表示 |
| 工具层 | 定义能力、校验参数、执行副作用、截断输出 |
| 会话状态 | 持久化消息、恢复、分支、压缩 |
| 扩展机制 | 钩子、动态工具、命令，让宿主可被改造 |
| 知识装载 | Skill 一类按需加载的指令 |
| 入口 | 交互终端、一次性任务、管道、退出码 |
| 观测与验收 | 轨迹、日志、测试、受控实验 |

不是每个 harness 都实现全部这些。极简实现可以只有循环和 bash；但只要任务变长、环境变真，缺哪一块，就会在哪一块出问题。

## 二、主循环

最小循环可以写成下面这样。相比教科书版本，它多了两件事：错误也要变成结果，结果必须和调用配对。

```python
while True:
    response = model(messages, tools)
    messages.append(response)

    if not response.tool_calls:
        break

    for call in response.tool_calls:
        try:
            output = execute(call.name, call.args)
        except Exception as e:
            output = f"{type(e).__name__}: {e}"
        messages.append(tool_result(call.id, output))
```

一次模型响应可能带多个调用，消息序列因此是：

```text
assistant(tool_calls=[c1, c2])
tool(call_id=c1, "ok")
tool(call_id=c2, "error: ...")
assistant(...)
```

三个细节决定它是否可靠：

1. **工具结果必须回填**，包括失败和超时。只回填成功结果，模型会以为调用没发生，进而重复调用。
2. **工具调用可能被截断**。流式输出中断、模型输出超长，都会留下不完整的参数；直接执行会得到难以理解的错误。
3. **终止条件是“没有工具调用”，不是“任务完成”**。模型停止请求工具，只说明它不想再调用，不说明结果正确。这一点在第 9 节会展开。

pi 在最小循环之外还做了一层：内层处理工具调用，外层维护消息队列，用来接收运行中插入的 steering 与 follow-up 消息。这不是必需的，但它解决了一个真实问题——长任务里，用户往往在模型跑完之前就想补充约束。mini-pi 的第 1 章只实现单层循环，把这一层留作对照。

## 三、模型适配

同一件事，两家 API 的表示完全不同：

| | OpenAI | Anthropic |
|---|---|---|
| 工具调用 | `assistant.tool_calls` | `tool_use` 内容块 |
| 工具结果 | `role="tool"` 消息 | `tool_result` 内容块 |
| 流式 | 增量 `delta` | 按事件类型分发的流 |

如果核心循环直接读厂商字段，换模型就要改循环。Adapter 模式解决的就是这个问题：内部只认统一的 `Message` / `ToolCall`，由适配器负责编解码。

看起来只是格式转换，实际还有三件麻烦事：

- **流式增量要累积**：参数分片到达，必须拼成完整 JSON 才能执行；
- **消息要校验和修复**：空内容、被拒绝的请求、工具调用与结果不配对，都会让下一次请求失败；
- **中止要全链路传递**：用户按 Ctrl-C 时，HTTP 请求、流式读取、工具执行都要收到同一个取消信号。

适配器可以抽象成一个很小的接口：

```python
class Adapter(Protocol):
    def encode(self, messages, tools) -> dict: ...
    def decode(self, raw) -> Message: ...
    def decode_stream(self, chunk) -> Event: ...
```

核心循环只依赖这个接口，不读厂商字段。归一化时要守住几条不变量：每条 tool result 都能找到对应的 call；assistant 文本可以为空，但整条消息不能为空；厂商特有字段放进 metadata，不进入控制逻辑。流式累积则是一个小状态机：按 index 拼接参数分片，收到结束事件后再解析 JSON；解析失败就转成一次工具错误，而不是让进程崩溃。

mini-pi 的第 2 章用一个统一结构和两个适配器实现这些；pi 把这层放在 `packages/ai/`，并维护 Provider Registry、token 计数和消息修复。适配层的成本是持续的：厂商协议会变，这层代码就得跟着变。

## 四、工具层

一个常见问题是：既然有 bash，为什么还要 read / write / edit？

因为**专用工具把“意图”变成了可校验的结构**。`edit` 要求旧文本在文件中唯一匹配，模型改错位置时会被立刻拒绝；bash 里的 `sed` 不会给你这个保证。pi 的 edit 采用的就是 exact unique match，mini-pi 第 3 章复现了它，并对比了行号替换和正则替换两种失败模式。

工具层要处理四件事：

| 事项 | 做法 | 不做的后果 |
|---|---|---|
| 定义与执行配对 | 注册时校验 JSON Schema，执行时再校验参数 | 模型传错参数，错误发生在深层代码里 |
| 输出截断 | 超长输出截断并注明 | 上下文被一次 `cat` 撑爆 |
| 并发控制 | 有副作用的调用串行或加锁 | 两个 edit 互相覆盖 |
| 错误表示 | 失败也是工具结果，带可读原因 | 模型重试同样的错误 |

一个工具定义大概长这样：

```json
{
  "name": "edit",
  "description": "Replace one unique occurrence of old_string",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "old_string": {"type": "string"},
      "new_string": {"type": "string"}
    },
    "required": ["path", "old_string", "new_string"]
  }
}
```

执行时的核心检查只有一句：

```python
count = text.count(old)
if count != 1:
    raise ToolError(f"expected exactly 1 match, found {count}")
```

`count == 0` 说明模型记错了上下文，`count > 1` 说明意图不明确；两种都应该让模型重新读取，而不是猜一个位置改下去。

bash 是特殊的一个：能力最大，也最难约束。它可以执行测试、编译、Git，也可以删文件、连网络。**权限边界应该由执行环境实施**，而不是靠提示词要求模型“不要乱来”。mini-pi 没有实现沙箱和完整权限系统，这是教程的边界，不是设计上的推荐。

## 五、会话状态

会话不是“把消息存成一个 JSON”。真实的失败长这样：模型生成到一半被中断，留下一条不完整的 assistant 消息；工具执行到一半进程被杀，留下一个没有结果的调用；用户在两个分支之间切换，需要回到某个历史点继续。

mini-pi 第 4 章的做法是：

- **JSONL 只追加**：文件头记录版本和元信息，之后每行一个 entry；
- **树形结构**：entry 带 `id` 和 `parentId`，用 leaf 指针表示当前分支；
- **恢复即投影**：从 leaf 沿父链回溯，投影出 LLM 需要的消息序列；
- **事故恢复**：中断留下半截消息时，补一个结果而不是重放工具；API 错误留痕，下次请求前修复消息配对。

一条会话在磁盘上大概是这样：

```json
{"type":"header","version":1,"session_id":"s1"}
{"type":"message","id":"e1","parentId":null,"role":"user","content":"..."}
{"type":"message","id":"e2","parentId":"e1","role":"assistant","tool_calls":[...]}
{"type":"message","id":"e3","parentId":"e2","role":"tool","tool_call_id":"c1","content":"..."}
```

恢复时不需要重放工具，只要从 leaf 回溯、投影出消息；分支也只是把 leaf 指向另一个 entry。

上下文压缩是另一个独立问题。触发方式有三种：超过阈值、请求溢出、用户手动 `/compact`。难点不在“让模型总结”，而在切点：

- 切点不能拆开 tool call 和 tool result，否则投影出的消息非法；
- 一个 turn 可能太长，需要 split turn，把前缀压成摘要、后缀保留原文；
- 摘要要增量更新，不能每次都重写；
- token 估算不能直接用 `chars / 4`，中文会被明显低估。

切点可以写成一个很小的算法：

```python
def choose_cut(entries, keep_tokens):
    i, used = len(entries), 0
    while i > 0 and used < keep_tokens:
        i -= 1
        used += estimate_tokens(entries[i])
    # 不能把 tool call 和它的结果切开
    while i > 0 and splits_tool_pair(entries, i):
        i -= 1
    return i
```

如果切点落在 turn 中间，就把前缀压成摘要、后缀保留原文（split turn）；摘要按 Goal / Constraints / Progress 分段增量更新，下一轮只改发生变化的段落。

这些都是有损操作。**原始 entry 必须保留**，摘要只是给模型看的视图，不能当作唯一事实来源。

## 六、扩展机制

不同团队的工作流不可能都塞进内核。扩展机制要回答的是：外部代码能在哪些点介入，介入后协议是否仍然完整。

mini-pi 第 5 章把扩展拆成几类能力：

- **工具注册表**：定义和执行器必须成对注册，内置工具走同一条路径；
- **生命周期钩子**：在模型调用前后、工具执行前后介入；
- **拦截与改写**：可以阻止一次调用，也可以改写参数或结果；
- **斜杠命令**：命令属于用户，工具属于模型，两者不要混在一起；
- **按文件加载**：目录发现、显式 `--extension`、首次加载的信任确认。

真正容易出错的是边界情况：

- 调用被拦截了，也要把“被拦截”作为结果保存，否则历史里会缺一条；
- 输出被截断了，就不能再执行它，否则执行的是残缺内容；
- 中断之后不要自动重试工具，副作用可能已经发生；
- 多个扩展同时挂同一个钩子时，顺序和冲突要有明确规则；扩展抛异常也不能一律“忽略后继续”。

这些规则听起来琐碎，但它们决定了扩展是“可组合的能力”，还是“随机破坏协议的后门”。

## 七、Skill

扩展改变的是“能做什么”，Skill 提供的是“怎么做”的指令。两者经常被混为一谈。

把所有领域的操作手册都塞进系统提示，会带来两个问题：上下文被无关内容占满，模型在长提示里也更容易忽略关键约束。Skill 的做法是**渐进式披露**：

1. 启动时只扫描 `SKILL.md`，把“名字 + 描述”挂进系统提示；
2. 模型判断某个 Skill 相关时，用普通的 `read` 工具读取全文；
3. 宿主也可以提供 `/skill <名字>`，强制加载某个 Skill。

一个 `SKILL.md` 的最小形态：

```markdown
---
name: release-check
description: 发布前检查版本号、变更日志与测试状态
---

1. 读取 pyproject.toml 与 CHANGELOG.md
2. 运行 pytest -q
3. 汇总未完成项
```

启动时进入系统提示的只有清单行：

```text
- release-check: 发布前检查版本号、变更日志与测试状态
```

正文不会出现在上下文里，直到模型主动读取。

这套机制的关键不在加载，而在**描述**。描述是路由依据：写得含糊，模型就不会来读；写得过宽，模型会在无关任务上浪费上下文。

mini-pi 第 6 章实现了发现、frontmatter 校验、重名先到先得、清单注入和 `system_suffix` 注入点，并对照了 pi 的两条路径：模型自取，以及宿主侧强制展开。pi 还把 `disable-model-invocation` 做成了三态控制，决定某个 Skill 是否允许模型主动调用。

需要强调：**Skill 是提示和脚本，不是权限边界**。它可以让模型更懂流程，但不能限制模型能执行什么。

## 八、入口与 CLI

同一个 harness，通常有三种用法：交互终端、一次性任务、被其他程序调用。它们的展示方式不同，但核心应该只有一条执行路径。

mini-pi 第 7 章处理了几个容易被忽略的细节：

- **流式文本和完整消息是两回事**：流式用于实时展示，完整消息用于持久化和下一次请求；流结束后不要再打印一遍全文；
- **会话按项目目录隔离**：在哪个仓库启动，就恢复哪个项目的会话；
- **输入历史不是模型历史**：终端的上下键历史，和会话里发给模型的消息，不应该混用；
- **中断是一条完整控制路径**：既要停模型生成，也要停工具执行，还要保证之后能继续用；
- **退出码要能区分成功、失败和中断**，否则脚本无法判断结果。

pi 在这之上还提供 print / JSON 模式、RPC 和 TypeScript SDK。接入方式越多，越需要把“展示”和“执行”分开，否则每加一个入口就要复制一遍逻辑。

## 九、验收与评测

这是最容易被跳过的一节。一个 harness 能跑起来，不等于它能可靠地完成任务。

第 8 章把可靠性拆成几个不同对象：实现是否符合约定、任务是否完成、代码是否正确、操作是否安全。**离线测试只能证明第一项**。一个“钩子被调用了”的单元测试，不能证明钩子改善了交付质量。

要判断某个机制是否有效，需要受控实验：固定模型版本、工具集、预算和环境，选一组真实任务，配对比较“有该机制”和“没有该机制”的结果，同时记录成功率、成本、用时、人工修正量和失败类型。

基准也有边界。SWE-bench 是最常用的编码智能体评测，它的判卷方式是：给一个 issue 和代码快照，让工具生成补丁，然后运行 PR 中修改过的测试文件。ICSE 2026 的一篇论文系统检查了这个流程，结论值得每个做 harness 的人记住：

- 只跑 PR 修改过的测试文件，会漏掉仓库里其他测试；跑全部开发者测试后，平均 **7.8%** 的“已解决”补丁其实是错的，解决率绝对下降 **4.5 个百分点**；
- 用差分测试（以人类补丁为参照，自动生成能区分两个补丁行为的测试）检查，平均 **29.6%** 的补丁行为与标准答案不同；其中 **82.7%** 是全量开发者测试也发现不了的；
- 人工复核这些“可疑”补丁，**28.6%** 确定错误；把这一比例外推到全部可疑补丁，再加上 RQ1 中未被标为可疑的错误补丁，合计约 **11.0%** 的补丁是错的，报告解决率平均被高估 **6.4 个百分点**。

这篇论文的方法叫 PatchDiff，思路不复杂：如果生成补丁是错的，它必然在某个场景下与人类补丁行为不同；于是不必证明“正确”，只要构造能区分两者的输入。它的流程可以压缩成几行伪代码：

```python
targets = find_target_functions(generated, oracle)   # 调用链上的非测试函数
for fn in targets[:10]:
    tests = llm.generate(fn, generated, oracle, context(fn))
    tests = repair(tests)                 # 失败最多修 2 次
    tests = filter_by_target(tests)       # 直接调用必须是目标函数
    tests = filter_flaky(tests, generated, oracle, runs=20)
    if tests:
        mark_suspicious(generated, tests)
```

真正决定质量的不是生成，而是后面三层过滤：目标函数校验、flaky 过滤、失败修复。少一层，输出就会退化成一堆“运行时不同但说明不了问题”的测试。

它同样有边界——人类补丁不是绝对真理，“有差异”只说明可疑，不说明错误。

对自己的 harness 来说，结论是：**不要把“模型停止调用工具”当成验收**。生成结束、测试通过、任务完成，是三件不同的事。

## 十、职责清单

把上面的内容压缩成表：

| 部件 | 要做什么 | 缺了会怎样 |
|---|---|---|
| 主循环 | 组织上下文、执行工具、判断停止 | 任务跑不完，或提前结束 |
| 模型适配 | 统一消息与工具协议，处理流式和中止 | 换模型就要改核心 |
| 工具层 | 定义、校验、执行、截断、互斥 | 参数错误、上下文爆掉、并发覆盖 |
| 会话状态 | 追加持久化、树形恢复、分支 | 中断即丢失，无法复盘 |
| 压缩 | 切点、增量摘要、token 估算 | 长任务请求溢出 |
| 扩展 | 钩子、注册表、拦截、命令 | 工作流无法定制 |
| Skill | 描述路由、按需加载 | 提示膨胀，或知识缺失 |
| 入口 | 交互、一次性、管道、退出码 | 无法被脚本和他人使用 |
| 权限 | 执行环境隔离、访问控制 | 一次误操作造成真实损失 |
| 观测 | 轨迹、日志、成本 | 失败无法定位 |
| 验收 | 独立测试、受控实验、基准边界 | 分数虚高，结论错误 |

如果只能记一句话：**先建立可观察的闭环，再根据失败增加机制。**

每一个机制都应该对应一种具体的失败：压缩对应请求溢出，恢复对应中断，扩展对应工作流差异，Skill 对应知识装载，差分测试对应“过测试但改错”。说不出它减少哪种失败，就先不要加。

pi 的极简是一种取舍，不是规范；mini-pi 的实现是教学用的，不是生产级的。两者都只能说明“这些机制可以这样组合”，不能说明“你的系统也应该这样”。

## 参考资料

- [pi 仓库](https://github.com/earendil-works/pi)：官方定位为 minimal, extensible agent harness；本文引用的工具与文档路径以提交 [`8bb180b`](https://github.com/earendil-works/pi/tree/8bb180bec133199c913ca8ef9bb30bd9ab12a72b) 为准。
- [Mario Zechner：What I learned building an opinionated and minimal coding agent](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)
- [mini-pi](https://github.com/loveRhythm1990/mini-pi)：本文对应的 Python 实现，各章 README 有完整代码解析。
- [Agent Skills 规范](https://agentskills.io) 与 pi 的 [skills 文档](https://github.com/earendil-works/pi/blob/8bb180bec133199c913ca8ef9bb30bd9ab12a72b/packages/coding-agent/docs/skills.md)
- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling) 与 [Anthropic Tool Use](https://docs.anthropic.com/en/docs/tool-use)
- [SWE-bench](https://github.com/SWE-bench/SWE-bench)
- [Are “Solved Issues” in SWE-bench Really Solved Correctly?](https://software-lab.org/publications/icse2026_SWE-bench-correctness.pdf)，ICSE 2026，DOI [10.1145/3744916.3764576](https://doi.org/10.1145/3744916.3764576)；复现包 [PatchDiff](https://github.com/ZJU-CTAG/PatchDiff)。

资料查阅日期：2026-10-19。
