---
layout: post
title: "AI Code Review：审查流程、覆盖与边界"
date: 2026-10-19 18:00:00 +0800
author: "weak old dog"
header-img-credit: false
tags:
    - Harness
    - Code Review
---

几千、几万行代码已经写完，测试也通过了，但对质量仍然没有把握。给 AI 发一句“帮我 review”，它会检查哪些文件？会沿调用链继续读吗？如果 diff 涉及上千个文件，会不会只看一部分就结束？

AI 找到的缺陷，可以交给另一个 agent 复核，也可以继续生成复现和测试；更难确认的是，那些没有被报告的问题，究竟不存在，还是根本没有被查到。

## 审查过程

### 范围与输入

审查改动，首先要确定比较对象：未提交的工作区、某次提交，或两个分支之间的变化。程序通过 Git 获取文件清单、变更状态和 diff；工作区模式还需要明确是否包含暂存区、未暂存改动和未跟踪文件。

审查整个现有项目则是另一种任务。已提交的旧文件可能不在本次 diff 中，即使 agent 为了理解改动打开了它们，也不代表旧代码都接受了审计。对一个已经由 AI 写完的系统，必须先明确要找本次改动引入的问题，还是检查整个实现。

接着，审查工具可能过滤生成文件、依赖目录和不支持的文件类型，按上下文预算拆分任务，再装入审查提示词和项目背景。模型最初拿到的可能只是局部 diff，并非所有文件的完整源码。

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-workflow-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-workflow.png" alt="审查执行过程：确定范围、准备输入、模型调查、处理发现、报告覆盖；模型通过工具反复搜索和读取代码" width="920" loading="lazy">
</picture>

### 调查循环

带工具调用的 review 通常按下面的方式继续：

1. LLM 阅读 diff 和审查要求，寻找新旧行为的差异，形成候选问题。
2. LLM 请求搜索符号、读取函数、查找调用方或测试；程序执行工具，把结果追加到上下文。
3. LLM 根据新证据继续调查、撤回假设，或输出发现。这个循环可能进行多次。
4. 达到完成条件或触发预算、超时等停止条件后，程序汇总评论及执行状态。

这里没有天然的“每个文件从头到尾读一遍”。文件可能被直接装入，也可能只返回片段；读取什么、是否继续追踪，取决于工具实现、任务指令和模型的选择。存在搜索工具，只能说明有调查能力，不能证明这次调查充分。

运行测试也不是所有 review 的默认步骤。模型可能有执行权限，也可能只有搜索和读取工具。报告中的代码推断、其他 agent 的复核、实际执行的复现，需要分别说明。

### 订单案例

用一个合成例子观察这个过程：不同用户的数据共用一张订单表，`tenant_id` 标记归属。重构把查询从“归属和订单 ID 同时匹配”改成了只匹配订单 ID：

```diff
-SELECT id, tenant_id, total FROM orders WHERE tenant_id = ? AND id = ?
+SELECT id, tenant_id, total FROM orders WHERE id = ?
```

这是一条线索。模型还需要找到调用查询的 handler，检查用户能否提交别人的订单 ID、返回前是否校验归属，以及数据层是否另有隔离。

若 handler 直接返回查询结果，数据库也没有额外隔离，A 请求 B 的有效订单 ID 就会得到 B 的数据。若上层已有可靠校验，则应撤回原来的越权假设。只看 SQL，无法区分这两种实现。

原有“读取自己订单”的测试，在正确和错误版本上都可能通过。继续构造跨用户请求，才有机会区分它们。这说明审查需要把局部变化连到实际行为；代码读取、推理和验证各有作用。

## 原生审查与 OCR

### 已有能力

原生审查已经提供了不少能力。Codex 的 `/review` 可以针对未提交改动、一次提交或基准分支开展审查；Claude Code 的 `/code-review` 则提供专用的审查任务，并支持不同目标和 effort。它们运行在能够访问代码和工具的 agent 环境中，不能把它们简化成只给模型贴一段 diff。[Codex 文档](https://learn.chatgpt.com/docs/codex/cli)、[Claude Code 文档](https://code.claude.com/docs/en/code-review-local)

原生入口、本地 skill 与托管 PR 服务也可能采用不同的任务拆分和复核流程。公开文档没有承诺逐文件穷尽检查的地方，就不能自行补上这项保证。是否需要第三方工具，应比较具体流程和实际记录。

### 任务组织

阿里开源的 [OpenCodeReview](https://github.com/alibaba/open-code-review)把审查拆成文件选择、规则匹配、分组、调度、工具调查和评论处理。它的价值要沿这些步骤判断，不能只归结为一份更长的提示词。

OCR 的常规模式使用自己配置的模型运行调查；委托模式则把文件选择和规则解析交给 OCR，由宿主 agent 负责审查。两种模式不能混作同一个评测对象。[执行模式](https://github.com/alibaba/open-code-review#how-to-use)

对 diff 之外的旧代码，OCR 模型可以通过搜索和文件读取补充上下文，但默认主任务要求评论针对分配给它的变更文件。顺着调用链发现了旧代码，不等于顺便完成了全库审计。没有合适 diff 时，项目也提供独立的 `ocr scan` 全文件扫描入口；扫描范围扩大后，仍要检查过滤和任务完成情况。[主任务提示词](https://github.com/alibaba/open-code-review/blob/main/internal/config/template/prompts/main_task_system.md)、[扫描入口](https://github.com/alibaba/open-code-review#quick-start)

### 提示词与规则

OCR 不要求用户先编写规则。它内置了按文件类型匹配的审查提示词，例如 Rust 源码使用 `rust.md`，关注错误传播、unsafe、锁、异步取消和资源生命周期。这是指导 LLM 调查的自然语言清单，不是逐项执行的静态检查器。[Rust 提示词](https://github.com/alibaba/open-code-review/blob/main/internal/config/rules/rule_docs/rust.md)

规则文档中的 `rule.json` 同时提供两类配置：`include`、`exclude` 调整文件筛选；`rules` 按路径选择额外的审查要求。前者由程序执行，后者装入模型提示词。不创建这个文件，也会使用默认配置。[规则机制](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/review-rules.md)

自定义要求可以覆盖整个仓库或某个目录，无需逐文件维护。例如，项目确实承诺“关闭接口返回后不再建立新连接”，才值得把它加入相关模块的要求。若没有这项契约，模型不能把所有后台重连都判成错误。

配置时要注意：自定义条目默认会替换对应的内置提示词，`merge_system_rule: true` 才保留两者；用户条目取第一个匹配项，不会自动叠加所有匹配内容。`include` 也不是白名单，而是绕过部分默认排除条件。[匹配与合并](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/review-rules.md)

几百万行代码不可能靠人先写出完整正确性规则，再让 AI 审查。内置能力应该先工作，少量自定义要求只补充已知的重要契约。它们影响调查重点，解决不了文件漏分、任务中断和跨组上下文丢失。

### 评论处理

OCR 会把发现定位到代码行，再对候选评论做过滤。过滤提示词主要依据 diff 排除局部可直接推翻的意见，并保护并发、内存安全、行为变化等类别；它并不会重新追踪完整调用链。过滤后保留一条评论，不代表问题已通过复现。[处理流程](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/architecture.md)、[过滤提示词](https://github.com/alibaba/open-code-review/blob/main/internal/config/template/prompts/review_filter_task_user.md)

因此，OCR 的工程流程有助于组织和追踪任务；其分组、调查与复核仍包含模型判断。“流程更明确”和“缺陷找得更多”需要分别验证。已有原生审查若能满足任务组织和记录要求，就不必为了规则可配置再换工具；缺少的若是关系检索，则应单独评估代码图的收益。

## 千文件改动

### 分组与调度

假设一次 diff 修改了 1,000 个文件。OCR 不会把它们的完整内容一次性交给一个模型会话，而是先筛选，再组织多个审查任务。

在分组阶段，模型看到的是文件路径、变更状态和增删行数等元数据，不是全部 diff。它据此组织相关文件，每组最多 10 个；组内 diff 超出预算时继续拆成单文件任务。分组返回没有包含的文件会补成单独任务，分组失败也会退回逐文件调度。[分组实现](https://github.com/alibaba/open-code-review/blob/main/internal/agent/grouping.go)

这个兜底能避免文件因为“分组模型没提到”就消失，却无法保证分组识别出真实调用关系。两个强相关文件可能分到不同会话；组内共享上下文，组间则需要主动搜索和补读。并行只改变执行方式，不会自动产生全局理解。

### 未审内容

文件数量不是唯一限制。一千个小改名和十个大重写，输入量与调查成本可能完全不同。OCR 至少有以下几种留下未审内容的路径：

- **筛选排除**：二进制、受保护路径、不支持的类型和默认排除项不会直接派发审查，其中也包含一些测试文件。
- **单文件过大**：原始 diff 超出单次提示词预算时，文件被标记为 `too_large`。
- **文件删除**：整文件删除保留在变更清单中，但不作为独立文件任务派发；这不等于删除不会破坏其他调用方。
- **任务或运行未完成**：提示词过大、工具循环受限、调用失败或超时，会影响本组；若设置的全程 token 预算不足，后续组可能停止派发。

这些行为可以从[文件选择](https://github.com/alibaba/open-code-review/blob/main/internal/agent/selection.go)和[调度实现](https://github.com/alibaba/open-code-review/blob/main/internal/agent/agent.go)核对。需要把预览和实际执行结果一起看：

```bash
ocr review --preview
ocr review --format json --output review-result.json
```

预览只能说明计划选中哪些文件。它无法预知后面的预算消耗、模型调用失败或调查中断。源码中的运行清单记录已选任务及完成、复用、失败等执行状态，但分母是筛选后的任务，不能拿它代替原始变更清单。部分完成也可能仍返回已有评论和成功退出码，不能仅凭命令退出成功认定审查完整。[执行状态](https://github.com/alibaba/open-code-review/blob/main/internal/agent/agent.go)

### 覆盖对账

一次大审查至少应该交付三份能互相核对的清单：原始变更、被排除的文件及理由、进入任务后的执行结果。以下数字只是说明口径的例子：

```text
变更：1,000 个文件
排除：150 个，逐项保留理由
进入任务：850 个
正常完成：790 个；失败：20 个；预算不足未完成：40 个
```

这只能支持“790 个文件所在的任务正常结束”。不能写成“全部审完”，也不能把排除的 150 个文件直接视为没有风险。对于必要内容，要调整筛选、补充预算或单独组织审查。

即使所有已选文件对应的任务都完成，也仍可能漏掉 bug。OCR 的主任务提示词要求每个文件单独检查后再调用 `task_done`，但指令和完成标记不能证明模型逐行分析了所有分支。订单查询被看过，模型仍可能没想到跨用户请求；查询和授权分别被看过，模型也可能没把它们联系起来。[逐文件要求](https://github.com/alibaba/open-code-review/blob/main/internal/config/template/prompts/main_task_system.md)

实际使用中，可以按相关模块分批检查，随后另设一次跨模块调查，关注接口、共享状态和失败恢复。第二轮应有明确目标和补充证据，反复发送同一句“再检查一下”无法提供独立覆盖的保证。

## 代码图的作用

[Code Review Graph](https://github.com/tirth8205/code-review-graph)主要改进上下文检索。它用 Tree-sitter 等机制解析代码，以 SQLite 保存函数、文件及调用、导入、引用等关系，再通过工具接口供 agent 查询。图构建时解析了文件，不代表 LLM 已经审查了这些文件。

普通 agent 可以搜索 `GetOrder` 再辨认调用方；有图时，可以先查询调用关系，再读取关键源码。图补充的是结构信息，订单归属、错误恢复等行为仍需模型判断。它可以配合原生 agent 使用，不能默认 OCR 已经集成了这个项目。

### 实验观察

配套的 Python/SQLite 小实验重现前面的订单问题，保留查询、handler、测试、动态任务分发和无关健康检查五个源码文件。它实际运行了代码图查询与 SQL 行为验证，没有调用 LLM。脚本和观测结果见[示例目录](https://github.com/loveRhythm1990/loveRhythm1990.github.io/tree/master/code-examples/ai-code-review)。

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-graph-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-graph.png" alt="订单实验：测试通过 handler 调用查询；调用方不变，删除归属过滤却改变了跨用户访问结果" width="920" loading="lazy">
</picture>

实验查到了 handler，影响范围还包含相关测试和动态任务文件，没有包含无关的健康检查。不过，`get_review_context` 只返回查询文件的源码，agent 仍需继续打开 handler。动态字典分发没有直接调用边，任务文件通过导入和引用关系进入结果，具体调用目标仍需读代码。

修改前后，识别到的直接调用方相同；删除归属过滤后，A 请求 B 的订单却从未找到变成返回数据。只读取自己订单的测试在三个版本上都通过。调用图能够提供线索，却不能代替 SQL、断言和行为验证。

### 检索成本

图可能减少反复搜索的消耗，但要比较同一个 agent 完成同一调查的总成本：图查询结果、补读源码、后续对话，以及索引构建和更新。用图返回大小与整库文本比较，无法代表相对原生 agent 的真实收益；原生 agent 通常也会先搜索、再按需读取。[项目评测口径](https://github.com/tirth8205/code-review-graph/blob/343ab7cea29d47a3e805381e09f8243a43d43d88/docs/REPRODUCING.md)

本次小实验没有测量完整审查 token；工具的 `context_savings` 是估算，`saved_tokens` 为零。仓库变大也不自动意味着更划算，还取决于任务是否反复追踪跨文件关系。动态调用、索引过期和查询截断则可能损失上下文，需要核对解析状态与返回范围。

## 有效性的证据

这些项目尝试改进的环节不同：提示词影响注意力，任务编排影响工作分配，检索影响可见证据，复核影响误报，预算管理影响成本。改进其中一项，不代表其他几项也提高了。

例如，PR-Agent 提供输入压缩策略；CodeRabbit 的公开架构组合静态分析、agent 探索与验证；Greptile 强调代码关系检索。这些是设计方法，不构成缺陷检出能力排名。[PR-Agent](https://github.com/The-PR-Agent/pr-agent)、[CodeRabbit](https://docs.coderabbit.ai/overview/architecture)、[Greptile](https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context)

OCR 官方报告在其评测中，相同底层模型下，已报告问题的成立比例更高、耗时与 token 更低，但已知缺陷的检出比例更低。这支持它在这组条件下的取舍，不能推出换成自己的仓库、原生审查入口和预算后仍然更优。尤其对担心漏检的人，召回率下降不能被“评论更准”掩盖。[官方评测](https://github.com/alibaba/open-code-review#benchmark)

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-evaluation-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-evaluation.png" alt="审查覆盖的三层：文件是否进入并完成任务，关键路径是否得到调查，真实缺陷是否被发现；每层需要不同证据" width="920" loading="lazy">
</picture>

评估时要把三层覆盖分开：文件覆盖可以通过清单对账；调查覆盖只能从读取、搜索和验证记录中检查关键路径；缺陷覆盖需要已知问题作为参照，真实系统中通常没有完整答案。

可以恢复历史缺陷进入时的代码，隐藏后续修复信息，再加入没有缺陷的改动和正确例外。在相同背景、模型与预算下比较原生审查、OCR，或者同一个 agent 有无图检索。记录已知缺陷的漏检、额外发现的有效性、未完成任务及总成本。对已报告问题交叉复核，能增加可信度；它无法证明第一轮没有漏掉其他问题。

本文没有做完整的模型或产品对照实验。现有实验只支持前面列出的结构查询和行为观察，不能据此宣布 OCR 或某个模型找到了越权缺陷。

## 审查边界

有些不确定性不能通过增加文件覆盖解决：

- **契约不明确**：代码能表明如何实现，未必能表明应该如何实现。同步持久化还是允许异步落盘，需要已有协议、设计或明确决策。
- **运行条件缺失**：真实数据分布、流量、外部服务和部署配置不完整，性能、故障恢复与资源竞争的结论就受限，需要压测、故障注入或运行观测。
- **实现共同遗漏**：代码、测试和文档都由 AI 按同一错误前提生成时，重新读一遍仍可能接受这个前提。还需要从用户场景、协议和失效条件独立提出验证任务。

这些补充工作仍可以让 AI 执行，但需要单独的目标、工具和验收依据。完整文件清单和多个 agent 一致同意，都不能替代这些证据。

实际发起审查时，可以从下面的任务开始，不必先为仓库写一套完备规则：

```text
请审查 <明确的改动范围或目录>，先报告结果。

先列出范围和文件清单；规模过大时分批执行，保留每批状态。
记录排除、读取失败、预算不足和未完成内容，不要把部分完成写成全部完成。
对候选问题按需检查调用方、相关实现、配置和测试，核对正确例外。
每条发现给出位置、触发路径、影响及依据；区分推断、交叉复核和实际验证。
结束时说明尚缺哪些证据，以及下一步应单独开展的检查。
```

这段要求能引导审查和报告，不能替代程序化的任务清单。对于大批 AI 生成代码，首先需要拿到可核对的执行范围，再补查关键行为；“没有发现问题”只是在这次输入、工具与预算下的结果。
