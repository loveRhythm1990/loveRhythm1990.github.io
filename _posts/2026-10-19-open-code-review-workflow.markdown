---
layout: post
title: "AI Code Review：审查流程、覆盖度与边界"
date: 2026-10-19 18:00:00 +0800
author: "weak old dog"
header-img-credit: false
tags:
    - Harness
    - Code Review
---

几千、几万行代码已经写完，测试也通过了，但对质量仍然没有把握。给 AI 发一句“帮我 review”，它会检查哪些文件？会沿调用链继续读吗？如果 diff 涉及上千个文件，会不会只看一部分就结束？

AI 报告的风险点，可以交给另一个 agent 复核，也可以继续生成复现和测试；更难确认的是，那些没有被报告的问题，究竟不存在，还是根本没有被查到。

## 审查过程

### 范围与输入

审查改动，首先要确定比较对象：未提交的工作区、某次提交，或两个分支之间的变化。程序通过 Git 获取文件清单、变更状态和 diff；工作区模式还需要明确是否包含暂存区、未暂存改动和未跟踪文件。

审查整个现有项目则是另一种任务。已提交的旧文件可能不在本次 diff 中，即使 agent 为了理解改动打开了它们，也不代表旧代码都接受了审计。对一个已经由 AI 写完的系统，必须先明确要找本次改动引入的问题，还是检查整个实现。

接着，审查工具可能过滤生成文件、依赖目录和不支持的文件类型，按上下文预算拆分任务，再装入审查提示词和项目背景。模型最初拿到的可能只是局部 diff，并非所有文件的完整源码。

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

## 原生审查

原生审查已经提供了不少能力。Codex 的 `/review` 可以针对未提交改动、一次提交或基准分支开展审查；Claude Code 的 `/code-review` 则提供专用的审查任务，并支持不同目标和 effort。它们运行在能够访问代码和工具的 agent 环境中，不能把它们简化成只给模型贴一段 diff。[Codex 文档](https://learn.chatgpt.com/docs/codex/cli)、[Claude Code 文档](https://code.claude.com/docs/en/code-review-local)

原生入口、本地 skill 与托管 PR 服务也可能采用不同的任务拆分和复核流程。公开文档没有承诺逐文件穷尽检查的地方，就不能自行补上这项保证。是否需要第三方工具，应比较具体流程和实际记录。

## OCR 的执行过程

[OpenCodeReview](https://github.com/alibaba/open-code-review)是专用的代码审查 agent 系统，常规模式由自己的框架组织模型调用和工具反馈。下面沿 `ocr review` 展开；委托模式只由 OCR 提供文件选择和规则解析，实际调查交给宿主 agent，不能套用整条执行流程。

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-ocr-pipeline-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-ocr-pipeline.png" alt="OCR 执行流程：文件选择、规则准备、分组与调度后，各组装入规则并进入模型和工具的反馈循环；候选评论经定位过滤，满足条件时再开展下一轮，最终汇总报告" width="920" loading="lazy">
</picture>

### 1 文件选择

程序先用 Git 获取改动。工作区模式合并暂存、未暂存和未跟踪文件；提交与分支范围模式则读取相应差异。输出包含路径、变更状态和 diff 的文件记录，未跟踪文件按新增文件处理。

随后执行路径、类型和大小筛选：二进制、受保护路径、用户排除项、不支持类型、默认排除项，以及过大的 diff 都可能不进入任务。整文件删除保留在变更清单中，但不作为独立任务派发。输出是待审清单和排除理由。**这一步由程序执行，没有筛选文件的 LLM 提示词。**[选择实现](https://github.com/alibaba/open-code-review/blob/main/internal/agent/selection.go)

### 2 规则准备与匹配

程序先加载规则配置；真正逐文件解析并装入提示词，发生在后面每组任务的组装阶段。图中的第二步表示规则准备。

OCR 的“规则”包含两件事：**文件筛选配置**决定哪些文件进入任务；**审查要求**是按路径选取、装入模型请求的自然语言。程序执行筛选和匹配，LLM 判断代码是否违反要求。内置 Rust 清单也是提示词，不是逐项执行的静态检查器；[附录保留了原文](#ocr-prompt-rust)。

用户不需要先编写规则。程序依次尝试命令行规则、项目规则、用户全局规则，最后使用内置规则；不存在自定义文件时直接使用默认配置。[匹配实现](https://github.com/alibaba/open-code-review/blob/main/internal/config/rules/system_rules.go)

官方文档提供了下面这个例子，可以保存为项目根目录的 `.opencodereview/rule.json`：

```json
{
  "rules": [
    {
      "path": "**/*",
      "rule": "Security review: flag hardcoded secrets, unvalidated redirects, and missing authz checks.",
      "merge_system_rule": true
    }
  ]
}
```

`rule` 是给 LLM 的自然语言审查要求，`rules[].path` 指定它适用的文件；这里的 `**/*` 覆盖所有通过筛选的文件。它与 `include` 不同：后者只调整文件筛选，不决定某条 `rule` 用在哪些文件上。

**开启 `merge_system_rule: true` 后，可以把 `rule` 理解为内置提示词之外的“额外注意事项”。** 例子要求关注硬编码凭据、未校验重定向和缺失授权，同时保留相应语言的内置要求。Rust 文件仍有 Rust 清单，Java 文件仍有 Java 清单，再叠加这段安全要求，不需要逐文件或逐语言重复配置。[官方例子](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/review-rules.md)

这份配置会沿工作流产生以下影响：

1. **选择文件时**，可选的 `include`、`exclude` 改变待审范围。这个例子没有这两个字段，因此不改变默认筛选。`exclude` 排除匹配项；`include` 绕过部分默认排除，不是白名单，也不能绕过所有保护条件。
2. **组装任务时**，程序逐文件解析适用要求，再按组去重合并；不同要求会标明对应文件，填入 `{% raw %}{{system_rule}}{% endraw %}`，供 `PLAN_TASK` 和 `MAIN_TASK` 使用。分组模型仍主要看文件元数据，这条安全要求不会让它自动重做安全分组。[任务装配](https://github.com/alibaba/open-code-review/blob/main/internal/agent/agent.go)
3. **模型调查时**，要求可能促使模型追查授权或输入校验，但不会增加工具权限、单独启动一个安全扫描器，也不能证明每项要求都已检查。图中的工具反馈和停止条件照常生效。

可以先预览覆盖范围，再用项目中的实际路径核对最终生效文本：

```bash
ocr review --preview
ocr rules check src/storage.rs
ocr review
```

去掉 `merge_system_rule` 时，自定义条目默认替换内置要求；多个用户条目取第一个匹配项，不会全部叠加。只想对本次任务使用另一份配置，可以通过 `ocr review --rule ./custom.json` 指定。[配置与使用](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/review-rules.md)

几百万行代码不需要先配一套完备规则才能审查。先使用默认能力，再补充已确认的重要项目契约；规则无法代替文件清单对账，也无法修复预算不足或调查中断。

### 3 文件分组

对于需要模型分组的改动集，程序把文件序号、路径、变更状态、增删行数填入 `GROUPING_TASK`，让 LLM 返回各组的标签与文件序号。**这次调用没有整批源码或 diff**，模型主要根据元数据判断哪些文件相关。[分组提示词](#ocr-prompt-grouping)

程序检查返回结果，去重、补齐漏分文件，并限制组大小；分组失败就退回单文件任务。小改动集也可能直接由程序合并或逐文件处理，跳过模型分组。输出是一组组待调度的 diff。分组只决定哪些文件共用上下文，尚未开始查找 bug。[分组实现](https://github.com/alibaba/open-code-review/blob/main/internal/agent/grouping.go)

### 4 任务调度

程序按配置的并发数派发各组，每组有独立消息历史。它检查预算、超时与取消状态，记录完成或失败；组间不会自动共享完整调查记录。**调度本身没有提示词。**

较大改动还可能先执行 `PLAN_TASK`：输入本组 diff、规则、可选背景和其他变更文件清单，输出风险点及拟调用的工具。这里实际只调用一次模型生成计划，没有执行工具；程序把计划装入后续主任务。小改动可以跳过计划，计划调用失败也可以继续主审查。[计划提示词](#ocr-prompt-plan)、[调度实现](https://github.com/alibaba/open-code-review/blob/main/internal/agent/agent.go)

### 5 工具调查

`MAIN_TASK` 的初始消息包含本组 diff、规则、背景、可选计划与此前确认的发现；其他组最初只提供变更文件清单。程序还向模型注册工具定义。LLM 据此决定继续查什么，而不是由程序预先枚举所有调用链。[主任务提示词](#ocr-prompt-main)

默认工具分成三类：`code_search`、`file_find` 查找代码与文件；`file_read`、`file_read_diff` 补读源码和其他变更；`code_comment` 提交候选评论，`task_done` 报告任务结束。**默认这组工具没有执行测试的命令。** 搜索和文件读取也有返回数量或行数限制，读到一批结果不代表查询已经穷尽。[工具定义](https://github.com/alibaba/open-code-review/blob/main/internal/config/toolsconfig/tools.json)

例如模型看到订单查询删掉归属条件，可以搜索调用方、打开 handler，检查返回前是否另有授权；程序只负责执行请求并回传结果。读到可靠授权，模型就应撤回假设；证据足够时，通过 `code_comment` 提交触发条件、影响和定位片段。

工具结果会追加到当前会话，模型继续调查，直到显式结束或触发停止条件。上下文变长时，程序可能调用 `MEMORY_COMPRESSION_TASK`，摘要保留已发现问题、工具结论、完成与待办事项，再继续原任务；摘要本身也可能丢掉细节。[压缩提示词](#ocr-prompt-memory)

图中有两层循环：实线表示同一会话内的“模型请求工具 → 程序执行 → 结果返回模型”；虚线表示本轮结束、评论过滤后，再开始一轮审查。后续轮次带入已确认的发现，并去掉原计划；无新增发现、预算不足或达到轮次等限制时，不再继续。工具调用可以重复很多次，但这两层循环都不能证明已穷尽所有路径。[调查循环](https://github.com/alibaba/open-code-review/blob/main/internal/llmloop/loop.go)、[多轮调度](https://github.com/alibaba/open-code-review/blob/main/internal/agent/agent.go)

### 6 评论处理

模型提供的 `existing_code` 片段先由程序在 diff 中匹配，计算评论行号。匹配失败时，可调用 `RE_LOCATION_TASK`，让 LLM 从 diff 重新摘出对应片段，再尝试定位。**这一步修正位置，不验证 bug 是否成立。**[重定位提示词](#ocr-prompt-relocation)

程序收集候选评论后，调用 `REVIEW_FILTER_TASK`。模型主要看到本组 diff 和评论，按提示词删除局部可直接推翻的意见，并优先保留并发、内存安全、行为变化等类别。它看不到主审查的完整调查过程，也不会重新执行测试。[过滤提示词](#ocr-prompt-filter)

最后程序再次处理行号，输出评论和运行状态。定位成功不证明 bug 成立，过滤保留也不等于复现通过；最终报告仍需区分代码推断、其他 agent 复核和实际执行的验证。

### 与 Codex 的区别

**OCR 和 Codex 都使用 agent 循环。** Codex 本身已有会话状态、工具执行、上下文压缩和权限管理；原生 review 也是专用审查入口。OCR 的特点主要在围绕审查组织任务的方式，不能把差异说成“一个只读 diff，另一个会追调用链”。[Codex harness](https://developers.openai.com/blog/codex-as-a-platform)、[原生审查入口](https://learn.chatgpt.com/docs/codex/cli)

- **任务如何组织**：OCR 明确按文件筛选、分组和派发，组内独立调查，组间按需补读。比较原生审查时，要核对具体入口是否已有类似拆分和完成记录，不能预设它一定只有一次模型调用。
- **调查能做什么**：OCR 的默认主工具集聚焦搜索、读取和评论；本地 Codex 可以在权限允许时使用终端、测试和其他已配置工具。能执行不等于本次 review 实际执行，比较时需要核对工具权限和记录。
- **结果怎样交付**：OCR 专门处理 diff 行号、候选评论过滤和文件任务状态。原生入口也能报告发现、遵循项目指导；是否还需要 OCR，应看额外流程是否降低遗漏、误报或成本。

更换提示词、工具集和任务组织方式，可能改变同一模型的调查结果；这不等于 OCR 换上了更强的语义理解引擎，也不保证它比原生审查发现更多问题。

## 千文件改动

### 分组与调度

假设一次 diff 修改了 1,000 个文件。OCR 不会把它们的完整内容一次性交给一个模型会话，而是先筛选，再组织多个审查任务。

每组最多 10 个文件，组内 diff 超出预算时再拆成单文件任务。即使筛选后仍有 1,000 个文件，也需要至少 100 组，实际可能更多；并发上限只控制同时执行多少组。模型漏分的文件有单独任务兜底，但后续仍可能受预算或执行失败影响。[分组限制](https://github.com/alibaba/open-code-review/blob/main/internal/agent/grouping.go)

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

### 文件对账

一次大审查至少应该交付三份能互相核对的清单：原始变更、被排除的文件及理由、进入任务后的执行结果。以下数字只是说明口径的例子：

```text
变更：1,000 个文件
排除：150 个，逐项保留理由
进入任务：850 个
正常完成：790 个；失败：20 个；预算不足未完成：40 个
```

这只能支持“790 个文件所在的任务正常结束”。不能写成“全部审完”，也不能把排除的 150 个文件直接视为没有风险。对于必要内容，要调整筛选、补充预算或单独组织审查。

即使所有已选文件对应的任务都完成，也仍可能漏掉 bug。OCR 的主任务提示词要求每个文件单独检查后再调用 `task_done`，但指令和完成标记不能证明模型逐行分析了所有分支。订单查询被看过，模型仍可能没想到跨用户请求；查询和授权分别被看过，模型也可能没把它们联系起来。[逐文件要求](https://github.com/alibaba/open-code-review/blob/main/internal/config/template/prompts/main_task_system.md)

实际使用中，可以按相关模块分批检查，随后另设一次跨模块调查，关注接口、共享状态和失败恢复。第二轮应有明确目标和补充证据，反复发送同一句“再检查一下”不能保证查出此前遗漏的 bug。

## Code Review Graph

[Code Review Graph](https://github.com/tirth8205/code-review-graph)提供代码关系索引、查询工具和指导宿主使用工具的 skill。它可以接入 Codex、Claude Code 等 agent；最终由宿主调查并判断问题是否成立。不能默认 OCR 已经集成了这个项目。

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-graph-pipeline-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-graph-pipeline.png" alt="Code Review Graph 分为程序建图和宿主审查：源码解析为 SQLite 关系索引；宿主识别改动、查询关系、补读源码并判断行为，证据不足时继续查询或读取" width="920" loading="lazy">
</picture>

### 建图与维护

1. **收集源码**：程序根据仓库文件、支持的语言和忽略配置确定解析范围。依赖目录、生成物或不支持的文件可能不在索引内，建图范围也需要核对。
2. **提取关系**：Tree-sitter 等解析器识别函数、类、导入和调用语法；语言相关的解析逻辑再尝试把名字、别名和跨文件引用关联到具体定义。结果以节点和边保存在 SQLite 中。调用目标无法可靠确定时，不能把一条近似关系当成运行时保证。
3. **增量维护**：文件变化后，程序依据变更、内容哈希和依赖关系更新索引。解析失败可能留下旧记录，返回 `partial` 等状态时需要处理失败项，不能把索引存在当成已经与当前源码一致。

基础建图和关系查询由程序完成，不需要审查 LLM 逐文件阅读。也因此，“整个仓库已建图”不能写成“整个仓库已 review”。[建图实现](https://github.com/tirth8205/code-review-graph/blob/staging/code_review_graph/incremental.py)、[解析实现](https://github.com/tirth8205/code-review-graph/blob/staging/code_review_graph/parser.py)

### 关系表存储

SQLite 用关系表表达这张图，核心是 `nodes` 和 `edges`：

- **节点表 `nodes`**：每行代表文件、函数、类或测试，保存类型、完整符号名、文件位置和行号等信息。
- **边表 `edges`**：每行代表一条有向关系，以 `source_qualified`、`target_qualified` 保存两端符号名，`kind` 表示调用、导入、继承等关系类型。

例如，handler 调用查询函数，就写入一条从 handler 指向查询函数的 `CALLS` 边。查“谁调用这个函数”，可以用下面的 SQL 示意，参数传入目标函数的完整符号名：

```sql
SELECT source_qualified
FROM edges
WHERE kind = 'CALLS'
  AND target_qualified = ?;
```

边的起点、终点及其与关系类型的组合都有数据库索引，支持定向查找。多跳影响范围由程序配合 SQL 遍历计算，源码也保留 NetworkX 遍历方式。因此，“图索引”包含两层：表中的节点关系，以及加速查询的数据库索引。[表结构](https://github.com/tirth8205/code-review-graph/blob/staging/docs/schema.md)、[存储与遍历](https://github.com/tirth8205/code-review-graph/blob/staging/code_review_graph/graph.py)

可选 embedding 另存在 `embeddings` 表，向量以 BLOB 保存，用于相似度检索。向量相近与存在调用关系是两种信息，不能混用。[向量存储](https://github.com/tirth8205/code-review-graph/blob/staging/code_review_graph/embeddings.py)

### 查询与调查

以项目提供的 `review-delta` skill 为例，宿主先查询最小上下文，索引未就绪时再建图或更新；然后调用 `detect_changes_tool`，把 diff 映射到变更函数、受影响路径和测试线索。需要进一步调查时，再调用 `get_review_context_tool` 或 `query_graph_tool`。[审查 skill](https://github.com/tirth8205/code-review-graph/blob/staging/skills/review-delta/SKILL.md)

图工具回答的是“哪些函数调用它”“哪些文件可能受影响”“有哪些关联测试”，返回关系、文件位置以及预算内的源码片段。风险分数帮助安排阅读顺序，不能当成 bug 已经成立的概率；测试关联也不是实际执行得到的覆盖率。[查询接口](https://github.com/tirth8205/code-review-graph/blob/staging/docs/COMMANDS.md)

宿主拿到结果后，继续打开关键文件，检查调用条件、数据变化和测试断言。若证据仍不足，就再查询、扩大范围或改用普通搜索。查询深度、数量与源码片段都有限制，`truncated`、省略计数或名字歧义需要显式处理。宿主决定何时结束，图工具不会自动把返回的每个文件全部审完。

### 结构与语义

“语义理解”需要分成三个层次看：

- **代码结构及符号关系**：解析导入、别名、继承与调用目标，确实涉及部分静态语义，超出了纯文本匹配。但关系解析并不等同于编译器完整的类型和运行语义分析，动态分发等路径仍可能缺失。
- **按意思查找代码**：项目可选用 embedding，把节点名称、签名、文档摘要等文本转成向量，再按查询的相似度找节点；没有可用向量时可以退回关键词检索。这是语义检索，帮助找到调查入口，不能证明检索结果的行为正确。[向量检索实现](https://github.com/tirth8205/code-review-graph/blob/staging/code_review_graph/embeddings.py)
- **判断设计与行为**：是否越权、是否违反持久化约定、取消后会不会留下错误状态，需要结合需求、源码和运行证据继续判断。找到一个“相关函数”，甚至找到所有已知调用方，也没有自动完成这一步。

因此，可以说代码图辅助语义分析，包含部分结构语义和可选的语义检索；不能把它概括成已经理解整个系统业务逻辑的审查模型。

### 实验观察

配套的 Python/SQLite 小实验重现前面的订单问题，保留查询、handler、测试、动态任务分发和无关健康检查五个源码文件。它实际运行了代码图查询与 SQL 行为验证，没有调用 LLM。脚本和观测结果见[示例目录](https://github.com/loveRhythm1990/loveRhythm1990.github.io/tree/master/code-examples/ai-code-review)。

实验查到了 handler，影响范围还包含相关测试和动态任务文件，没有包含无关的健康检查。不过，`get_review_context` 只返回查询文件的源码，agent 仍需继续打开 handler。动态字典分发没有直接调用边，任务文件通过导入和引用关系进入结果，具体调用目标仍需读代码。

修改前后，识别到的直接调用方相同；删除归属过滤后，A 请求 B 的订单却从未找到变成返回数据。只读取自己订单的测试在三个版本上都通过。调用图能够提供线索，却不能代替 SQL、断言和行为验证。

### 检索成本

图可能减少反复搜索的消耗，但要比较同一个 agent 完成同一调查的总成本：图查询结果、补读源码、后续对话，以及索引构建和更新。用图返回大小与整库文本比较，无法代表相对原生 agent 的真实收益；原生 agent 通常也会先搜索、再按需读取。[项目评测口径](https://github.com/tirth8205/code-review-graph/blob/343ab7cea29d47a3e805381e09f8243a43d43d88/docs/REPRODUCING.md)

本次小实验没有测量完整审查 token；工具的 `context_savings` 是估算，`saved_tokens` 为零。仓库变大也不自动意味着更划算，还取决于任务是否反复追踪跨文件关系。动态调用、索引过期和查询截断则可能损失上下文，需要核对解析状态与返回范围。

## 有效性的证据

这些项目尝试改进的环节不同：提示词影响注意力，任务编排影响工作分配，检索影响可见证据，复核影响误报，预算管理影响成本。改进其中一项，不代表其他几项也提高了。

例如，PR-Agent 提供输入压缩策略；CodeRabbit 的公开架构组合静态分析、agent 探索与验证；Greptile 强调代码关系检索。这些是设计方法，不构成 bug 检出能力排名。[PR-Agent](https://github.com/The-PR-Agent/pr-agent)、[CodeRabbit](https://docs.coderabbit.ai/overview/architecture)、[Greptile](https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context)

OCR 官方报告在其评测中，相同底层模型下，已报告问题的成立比例更高、耗时与 token 更低，但已知 bug 的检出比例更低。这支持它在这组条件下的取舍，不能推出换成自己的仓库、原生审查入口和预算后仍然更优。尤其对担心漏检的人，召回率下降不能被“评论更准”掩盖。[官方评测](https://github.com/alibaba/open-code-review#benchmark)

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-evaluation-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-evaluation.png" alt="文件覆盖度、调查覆盖度与 bug 检出率：分别检查文件任务状态、关键路径调查和已知 bug 的发现情况，需要不同证据" width="920" loading="lazy">
</picture>

评估时要区分三件事：文件覆盖度可以通过清单对账；调查覆盖度只能从读取、搜索和验证记录中检查关键路径；bug 检出率需要已知问题作为参照，真实系统中通常没有完整答案。

可以取出引入历史 bug 的版本，隐藏后续修复信息，再加入没有 bug 的改动和正确例外。在相同背景、模型与预算下比较原生审查、OCR，或者同一个 agent 有无图检索。记录已知 bug 的漏检、额外发现的有效性、未完成任务及总成本。对已报告问题交叉复核，能增加可信度；它无法证明第一轮没有漏掉其他问题。

本文没有做完整的模型或产品对照实验。现有实验只支持前面列出的结构查询和行为观察，不能据此宣布 OCR 或某个模型找到了越权 bug。

## 审查边界

有些不确定性不能通过扩大文件审查范围解决：

- **契约不明确**：代码能表明如何实现，未必能表明应该如何实现。同步持久化还是允许异步落盘，需要已有协议、设计或明确决策。
- **运行条件缺失**：真实数据分布、流量、外部服务和部署配置不完整，性能、故障恢复与资源竞争的结论就受限，需要压测、故障注入或运行观测。
- **实现共同遗漏**：代码、测试和文档都由 AI 按同一错误前提生成时，重新读一遍仍可能接受这个前提。还需要从用户场景、协议和失效条件独立提出验证任务。

这些补充工作仍可以让 AI 执行，但需要单独的目标、工具和验收依据。完整文件清单和多个 agent 一致同意，都不能替代这些证据。

## 开发者的下一步

开发者需要把剩余的不确定性拆成后续任务，检查结果，并决定是否具备交付条件。搜索代码、构造复现和执行验证，都可以继续交给 AI。

1. **补齐未完成内容。** 对照原始文件清单，把失败、超预算和必要的排除项重新派发。生成文件可以检查生成源和产物一致性，不能无说明地从风险清单中消失。补审后更新状态，保留仍未检查的内容及原因。
2. **分开安排复核与查漏。** 对已报告的问题，让另一个 agent 主动寻找反证和正确例外；对尚未发现的问题，另设并发、取消、跨模块契约等调查目标，从入口和失败路径重新检查。把已有评论全部确认一遍，并没有完成查漏。
3. **验证关键行为。** 先运行编译、lint 和已有测试，再让 AI 为高风险行为补充有区分力的验证。订单例子要覆盖跨用户访问；重试逻辑要覆盖“执行成功但响应丢失”；恢复逻辑需要检查中途失败或重启后的状态。能复现的 bug，应让验证在错误版本上失败、在修复后通过，而不只是增加通过的测试。
4. **按证据决定交付。** 关键路径未审完、重要契约未确认，或相关验证失败时，继续补查和修复。依赖真实负载才能判断的问题，单独安排压测、故障注入和观测；没有执行条件，就明确保留为未验证项。具备交付条件后，再按项目既有的分批发布、监控和回滚流程控制上线风险。

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

<!-- OCR_PROMPTS_BEGIN -->
## 附录：OCR 提示词

下面保留六组任务的 system/user 模板原文，以及 Rust 规则示例和循环补充指令。文件选择、规则匹配和调度没有独立提示词，执行方式见正文。点击展开各组即可阅读。

占位符保留原样。实际请求还会填入 diff、规则、背景和工具定义，因此这里展示的是模板，不是一次真实审查的会话回放。原文件与调用配置保存在[配套目录](https://github.com/loveRhythm1990/loveRhythm1990.github.io/tree/master/code-examples/ai-code-review/ocr-prompts)。

原文来自 alibaba/open-code-review，Copyright 2026 alibaba/open-code-review Contributors，按 Apache-2.0 保留；[许可证副本](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/ai-code-review/ocr-prompts/LICENSE)。附录只增加说明和展示格式，模板正文未改写。

<details id="ocr-prompt-grouping">
<summary><strong>文件分组 · GROUPING_TASK</strong></summary>

<p>输入文件元数据，输出标签和文件序号组成的 JSON 分组。</p>

<p><strong>system</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/grouping_task_system.md">grouping_task_system.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>You are a file grouping assistant for code review. Group changed files into semantically related clusters that should be reviewed together.

Files in the same group typically:
- Belong to the same module/feature
- Have producer/consumer relationships (e.g. interface and implementation)
- Are i18n/config variants of the same resource (e.g. message_en.properties and message_zh.properties)
- Share the same directory and work together on a single concern

Each file in the list is prefixed with a zero-based index in brackets, e.g. `[0] MODIFIED   path/to/file (+12/-3)`. Refer to files by that integer index, never by path.

Rules:
- Every file index must appear in exactly one group.
- A group may contain 1 file if it is unrelated to others.
- Maximum 10 files per group.
- The &quot;files&quot; field of each group is an array of the integer indices shown in brackets.
- Output ONLY a JSON array, no other text.</code></pre>
{% endraw %}

<p><strong>user</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/grouping_task_user.md">grouping_task_user.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>Group the following changed files:

{{file_list}}

Respond with a JSON array, where &quot;files&quot; holds the integer indices shown in brackets beside each file:
[{&quot;label&quot;: &quot;short theme description&quot;, &quot;files&quot;: [0, 1]}]</code></pre>
{% endraw %}

</details>

<details id="ocr-prompt-plan">
<summary><strong>审查计划 · PLAN_TASK</strong></summary>

<p>可选阶段，只生成调查计划；提示词描述了工具，但该调用不执行工具。</p>

<p><strong>system</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/plan_task_system.md">plan_task_system.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>You are an expert in code review task planning. You have access to a set of tools for retrieving relevant context about code changes, and your responsibility is to analyze those changes and produce a structured review plan.

## Core Responsibilities
Analyze code change content, identify potential risk points, and plan appropriate tool-calling strategies for each risk point.

## Tool Descriptions
{{plan_tools}}

## Output Format
Strictly follow the plain-text structure below. Output nothing else — no preamble, no closing remarks, no Markdown headings (lines starting with `#`), and no code fences (triple backticks):

Summary: (a brief description of the purpose and scope of this code change)

Issues

1. [high|medium|low] (a clear description of the specific problem and its potential impact for this risk point)
   → (tool name) (invocation arguments) — (the purpose of calling this tool and its relevance to the current issue)
   → (one line per additional tool call planned for the same issue)
2. [high|medium|low] (...)

Each part carries exactly one piece of information:
- the `Summary:` line — the overall change summary
- the `[...]` tag — the severity of that issue
- the text after the severity tag — the issue description
- each `→` line — one piece of tool guidance: the tool name, then its invocation arguments, then the reason after the em dash (e.g. `→ file_read internal/agent/agent.go — confirm whether the key passed to AwaitKey matches the one used at submission`)

## Analysis Rules
1. **Scope**: Only analyze newly added and modified code; ignore deleted code
2. **Ordering**: Issues must be numbered continuously and sorted by severity in descending order (high → medium → low)
3. **Severity Definitions**:
   - `high`: May cause security vulnerabilities, data loss, system crashes, or critical functional failures
   - `medium`: May affect performance, maintainability, or involve potential edge-case problems
   - `low`: Code style, readability, or non-critical best practice suggestions
4. **Tool Usage**: Tools are for reference purposes only and must not be actually invoked; describe the calling intent on the `→` lines
5. **Description Requirements**: Each issue description must cover three dimensions — problem location, nature of the problem, and potential impact
6. **Empty Result**: If an issue needs no tool verification, omit its `→` lines. If the changes carry no identifiable risk at all, output the `Summary:` line, then `Issues`, then `(none)`. Do not invent issues to fill the list.
</code></pre>
{% endraw %}

<p><strong>user</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/plan_task_user.md">plan_task_user.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>Other files changed in this update (not in this review group):
&lt;other_changed_files&gt;
{{change_files}}
&lt;/other_changed_files&gt;

{{diffs}}

Current time in the real world: {{current_system_date_time}}

### Requirement Background (Optional)
{{requirement_background}}

### Review Checklist
{{system_rule}}

### Task
Please analyze the code changes above and output a structured review plan.
</code></pre>
{% endraw %}

</details>

<details id="ocr-prompt-main">
<summary><strong>工具调查 · MAIN_TASK</strong></summary>

<p>输入本组 diff、规则等上下文，并通过工具调用继续调查。</p>

<p><strong>system</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/main_task_system.md">main_task_system.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>## Role
You are a code review assistant. You are responsible for producing professional review feedback on pull requests before they are merged. The diffs show what changed; use context tools to read or search related code when needed.
Please keep your responses concise and objective.

## Capabilities
- Think step by step progressively.
- First understand the code changes to be reviewed. Code changes are provided in Unified Diff format, where lines starting with `-` indicate deleted code, lines starting with `+` indicate added code, consecutive `-` and `+` lines represent modified code, and other lines represent unchanged code.
- Be objective and neutral, make judgments based on facts and logic, avoid subjective assumptions. When the context is unclear, use tools to obtain contextual information rather than judging based on assumptions.
- For the current code changes, provide feedback opinions, pointing out areas for improvement or potential issues. Focus on issues in newly added code.
- Avoid commenting on correct code or unchanged code.
- Avoid commenting on deleted code; deleted code serves only as reference context.
- Focus on clarity, practicality, and comprehensiveness.
- Use developer-friendly terminology and analogies in explanations.
- Focus primarily on the actual code logic and functionality. Avoid commenting on or providing feedback about non-functional elements such as code comments, tool-generated indicators (like @Generated annotations), or other metadata, unless the user explicitly requests you to review these elements.

## Strict Focus Rules
- Review every file listed in &lt;review_files&gt; individually.
- Cross-file observations within &lt;review_files&gt; are encouraged — look for inconsistencies, missing updates, and broken contracts across related files.
- Context tools are for gathering background information only. Your comments must address code within &lt;review_files&gt; — never produce comments targeting files outside it.

## Reply limit
- Before calling `task_done`, confirm you have given every `&lt;file&gt;` in &lt;review_files&gt; its own pass. Reviewing an implementation file does not cover its header, interface, or configuration counterpart — a file being the smaller or secondary member of the group is not a reason to skip it.
- If the current code review task is complete, call `task_done` to end the task.
- If a code issue has been identified and confirmed, call the `code_comment` tool to provide feedback.
- If additional context is needed to confirm the issue, call the appropriate context tool.
</code></pre>
{% endraw %}

<p><strong>user</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/main_task_user.md">main_task_user.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>Other files changed in this update (not in this review group):
&lt;other_changed_files&gt;
{{change_files}}
&lt;/other_changed_files&gt;

&lt;review_files&gt;
{{diffs}}
&lt;/review_files&gt;

Current time in the real world: {{current_system_date_time}}

&lt;user_task&gt;
### Requirement Background (Optional)
{{requirement_background}}

### Review Checklist
{{system_rule}}

### Review Plan
{{plan_guidance}}

### Previously Confirmed Findings
{{confirmed_comments}}

Now please review the code changes in &lt;review_files&gt; above.
&lt;/user_task&gt;
</code></pre>
{% endraw %}

</details>

<details id="ocr-prompt-memory">
<summary><strong>记忆压缩 · MEMORY_COMPRESSION_TASK</strong></summary>

<p>长会话的辅助调用，压缩既有调查记录供主任务继续使用。</p>

<p><strong>system</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/memory_compression_task_system.md">memory_compression_task_system.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>## Goal
You are a professional code review conversation summarization assistant. You will receive a conversation history between a code review assistant and an LLM model (including tool calls and their results). Compress this conversation into a structured summary so that the code review assistant can continue from the current state without restarting.

## Output Format Requirements
Organize the summary using the following five dimensions, separated by explicit headings:

### Identified Code Issues
List all confirmed issues sorted by severity (HIGH / MEDIUM / LOW). Each entry should include: file path, issue type, severity, brief description. Example:
- [HIGH] `UserService.go:45` — map concurrent read-write access without lock, suggest adding sync.RWMutex
- [MEDIUM] `config_loader.go:12` — error handling is incomplete, may swallow critical information

### Tool Call Conclusions
Summarize key findings and conclusions from each tool invocation. Example:
- get_function_info(UserService): confirmed concurrent write-to-map logic within this function
- search_file(&quot;database&quot;): no other related configuration issues found

### Completed Tasks
List items that have been completed and require no further follow-up.

### Pending Tasks
List items that have been started but not yet completed and still need attention.

### Current Focus
Summarize in one sentence the core matter currently being investigated or handled.

## Rules
1. Do not include specific code details; only reference file paths and issue types
2. Avoid repetitive or redundant information
3. Omit any dimension that has no relevant content
4. Completed/pending task list items should be described as complete sentences
5. current_focus should be concise, no more than one sentence
</code></pre>
{% endraw %}

<p><strong>user</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/memory_compression_task_user.md">memory_compression_task_user.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>{{context}}
</code></pre>
{% endraw %}

</details>

<details id="ocr-prompt-relocation">
<summary><strong>评论重定位 · RE_LOCATION_TASK</strong></summary>

<p>片段匹配失败时，从 diff 重新摘出定位片段。</p>

<p><strong>system</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/re_location_task_system.md">re_location_task_system.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>You are a code location assistant. Given a unified diff and a review comment, your sole task is to extract the exact code snippet from the diff that the comment refers to. /no_think
</code></pre>
{% endraw %}

<p><strong>user</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/re_location_task_user.md">re_location_task_user.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>Below is a unified diff and a review comment. Identify the minimal contiguous code range in the diff that the comment targets.

Rules:
1. Copy the relevant lines VERBATIM from the diff — do not rewrite, reformat, or add anything.
2. Strip leading diff markers (`+`, `-`, ` `) from each line before outputting.
3. Include only the lines directly related to the issue — no surrounding context.
4. If multiple disjoint locations apply, pick the single most relevant one.
5. Output ONLY a fenced code block. No explanation, no commentary.

**Diff:**
```diff
{diff}```

**Original code snippet (failed to match):**
```
{existing_code}
```

**Review comment:**
{suggestion_content}
</code></pre>
{% endraw %}

</details>

<details id="ocr-prompt-filter">
<summary><strong>评论过滤 · REVIEW_FILTER_TASK</strong></summary>

<p>依据 diff 和候选评论，判断哪些意见应当删除。</p>

<p><strong>system</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/review_filter_task_system.md">review_filter_task_system.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>You are a fact-checker for code review comments.

These review comments come from an Agent that could invoke tools to read the full codebase. You can see only the diffs of the files it reviewed together. Anything you cannot see, the Agent may well have seen.

Your task is narrow: remove only the comments that this diff **proves** to be factually wrong. You are not judging whether a comment is useful, well-prioritized, or worth a reviewer&#x27;s time.

The two mistakes available to you are not equally bad:

- Keeping an incorrect comment costs a reviewer a few seconds of attention.
- Removing a correct comment silently destroys a real finding. It never reaches anyone, and nobody learns that it was dropped.

So when your evidence falls short of proof, approve. &quot;Suspicious&quot;, &quot;I cannot verify this&quot;, &quot;low value&quot;, &quot;the flagged code looks fine to me&quot;, and &quot;I would not have raised this&quot; all mean approve.
</code></pre>
{% endraw %}

<p><strong>user</strong> · <a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/template/prompts/review_filter_task_user.md">review_filter_task_user.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>### Task

Below are the diffs of one or more related files, and a set of review comments about them. Identify only the comments that these diffs **prove** to be wrong.

Every comment carries a `path`. The `&lt;file&gt;` element with that same path is the comment&#x27;s subject; the other files are context. They can supply the evidence a cross-file comment rests on, but they never stand in for the subject file — code present somewhere in the group is not present in the file the comment was filed against.

Your default answer is to approve everything. On most reviews that is the correct answer.

### The only two grounds for removal

**Ground A — the comment targets code that is not in its subject file&#x27;s diff.**

The symbol, statement, or construct the comment describes appears nowhere in the `&lt;file&gt;` whose path the comment names. This ground is judged against that file alone — the same construct appearing in a sibling file does not rescue the comment. Typical shapes:

- it discusses the body of a function, on a file that only declares or references it
- it discusses host-language logic on a file that holds none — a query, build, markup, or configuration file
- it claims code was removed, or an error is handled, and its subject file&#x27;s diff contains no such change

**Ground B — a specific diff line literally contradicts the comment&#x27;s central claim.**

The comment asserts a concrete fact and the diffs show the opposite in plain text. Unlike Ground A, the contradicting line may sit in any `&lt;file&gt;` in the group: a comment calling an identifier unused is wrong once any of these files uses it. The contradiction must be readable straight off the diff, not derived through a chain of reasoning. Typical shapes:

- it says an identifier is unused, and the diff shows it in use
- it says a check, assertion, or branch is missing, and the diff contains it
- it says a value is hardcoded, and the diff shows it read from a variable
- it says something is declared twice and shadows an outer name, and the diff holds exactly one declaration
- it states a condition or type relationship that the diff&#x27;s own text refutes

If you cannot point to the specific diff line that establishes Ground A or Ground B, approve the comment.

### Protected subjects — never remove

These are vetoes, applied before you judge correctness at all. Whatever you conclude about the comment, approve it if its subject is:

- **Memory safety** — allocation size, buffer length, index bounds, off-by-one, use-after-free, null dereference
- **Concurrency** — locks and lock modes, atomics, data races, synchronization arguments that are not honored
- **Linkage and declaration consistency** — `static` versus non-`static`, a declaration that disagrees with its definition, missing `extern`
- **Behavioral or compatibility change** — a message, field, status, or default that the old code produced and the new code no longer does; an altered error path; a counter whose update moved to a different point in the lifecycle
- **A parameter the function accepts and never uses**

These are the categories where a wrongly removed comment is most expensive, and where your own confidence is least trustworthy — including confidence that the language, compiler, or runtime does not behave the way the comment claims. On a protected subject you do not get to be confident. Approve.

### Not grounds for removal

- The comment is about style, formatting, naming, blank lines, the wording of a code comment, or readability — **provided what it states is true**. Low value is not incorrectness, and filtering by value is not your job.
- The comment reasons about runtime behavior, business semantics, or code in files you cannot see. The Agent had access you do not.
- You disagree with its recommendation, or you consider the flagged code acceptable as written.
- You cannot confirm it. Unverifiable is not incorrect.
- It identifies a real problem but quotes a slightly wrong line or snippet. Judge the claim, not the citation.
- It is imprecise in passing while its central claim holds.

### Method

Run these steps in order for every comment. Stop at the first step that applies — do not revisit a decision a later step would have made differently.

**Step 1 — protected-subject veto.** Is the comment&#x27;s subject one of the protected categories above (memory safety, concurrency, linkage and declaration consistency, behavioral or compatibility change, an unused parameter)? → **approve and stop.** Do not assess whether it is correct. This veto outranks Ground A and Ground B: a comment on a protected subject stays even when you are confident it is wrong.

**Step 2 — value veto.** Is the comment about style, formatting, naming, blank lines, the wording of a code comment, or readability, and is what it states true of this diff? → **approve and stop.** Its low value is not your concern.

**Step 3 — Ground A.** Is the code it describes absent from its subject file&#x27;s diff? → **remove it.**

**Step 4 — Ground B.** Is there one diff line, in any file of the group, that literally contradicts its central claim, requiring no chain of reasoning to see? → **remove it.** Steps 3 and 4 are not optional: once a comment reaches them and qualifies, report it.

Before concluding a contradiction in Step 4, search every `&lt;file&gt;` for what the comment describes — not only the snippet it quoted. A comment that cites the wrong line while describing something the diffs do contain is correct, and stays.

**Step 5 —** approve.

Reaching Step 4 and needing more than a single inferential step to reach the contradiction means there is none. Approve.

### Code Diff

&lt;review_files&gt;
{{diff}}
&lt;/review_files&gt;

### Review Comments

{{comments}}

### Output

You must call exactly one tool:

- `report_incorrect_comments` — only for comments meeting Ground A or Ground B, and only if you could name the diff line that disproves each one.
- `approve_all_comments` — in every other case, including when comments look doubtful, unverifiable, or minor.
</code></pre>
{% endraw %}

</details>

<details id="ocr-prompt-rust">
<summary><strong>语言规则 · Rust</strong></summary>

<p>这份规则通过主任务和计划任务的 <code>{% raw %}{{system_rule}}{% endraw %}</code> 占位符装入。它是提示词的一部分，不是另一轮独立审查。</p>

<p><a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/config/rules/rule_docs/rust.md">rust.md</a></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>#### Obvious Typos or Spelling Errors
- Spelling errors in type names, function names, variable names, enum variants, trait names, or module names at their declaration sites; do not report spelling errors at call sites
- Strings in log messages, panic messages, error messages, or public diagnostics containing spelling errors that affect readability

#### Ownership and Lifetime Correctness
- Incorrectly returned references, borrowed values escaping their valid scope, or lifetime relationships that make an API unsound or unusable
- Excessive or unnecessary `clone()` calls introduced to satisfy borrowing when a borrow, iterator, `Cow`, or ownership transfer would be clearer and cheaper
- Interior mutability (`RefCell`, `Cell`, `Mutex`) used to work around ownership without a real shared-mutability requirement
- Reference cycles with `Rc&lt;RefCell&lt;T&gt;&gt;` or `Arc&lt;Mutex&lt;T&gt;&gt;` where `Weak` should be used to break ownership cycles

#### Error Handling and Panics
- `unwrap()`, `expect()`, `panic!`, `todo!`, or `unimplemented!` in production/library paths where the failure is recoverable or can be propagated with `Result`
- Errors converted to strings too early or discarded without context; prefer preserving the original error and adding actionable context at boundaries
- `Result` or `Option` values ignored, swallowed, or mapped to misleading defaults
- Public APIs that panic on ordinary invalid input instead of returning a typed error, unless the panic documents a clear programming invariant

#### Unsafe Code Boundaries
- `unsafe` blocks that are broader than necessary or hide multiple unrelated invariants
- Missing or stale safety rationale for `unsafe` blocks, `unsafe fn`, `unsafe impl Send`, or `unsafe impl Sync`
- Raw pointer dereferences without clear validity, alignment, initialization, aliasing, and lifetime guarantees
- FFI boundaries that do not validate null pointers, buffer lengths, ownership transfer, string encoding, or allocator compatibility
- `static mut`, unchecked `transmute`, `MaybeUninit`, `mem::zeroed`, or manual drop logic used without a documented invariant that makes the operation sound

#### Concurrency and Shared State
- Holding `Mutex`, `RwLock`, or `RefCell` guards longer than necessary, especially across calls into user code or potentially blocking operations
- Holding synchronous locks across `.await`, or using blocking I/O, sleeps, or CPU-heavy work directly inside async tasks
- Check-then-act races around shared state, cache initialization, file creation, or atomics
- Atomic operations with ordering that is too weak for the data being protected, or overly strong orderings that hide the intended synchronization contract
- Unsafe `Send` or `Sync` implementations that do not prove all contained state is thread-safe under the documented invariants

#### Async and Cancellation Safety
- Spawned tasks whose `JoinHandle` is dropped when failures, cancellation, or shutdown still need to be observed
- Futures that are not cancellation-safe around partial writes, lock acquisition, transactions, or resource cleanup
- Async functions that use synchronous filesystem, network, or process APIs in request/worker paths where the runtime can be blocked
- Retry loops without backoff, timeout, cancellation propagation, or bounded attempts

#### Collections, Iterators, and Performance
- Avoid unnecessary allocations in hot paths, such as repeated `String` construction, `format!`, `collect()`, or `to_vec()` where borrowing or streaming is sufficient
- Prefer iterator adapters and standard library collection APIs when they make ownership and complexity clearer; avoid dense iterator chains that obscure error handling or side effects
- Ensure hash maps, vectors, and strings are preallocated when the expected size is known and growth cost is material
- Avoid O(n^2) lookups from nested loops when a `HashMap`, `HashSet`, sorting, or indexing strategy would clearly reduce complexity

#### Type and API Design
- Model domain states with enums, newtypes, and typed IDs instead of booleans, strings, or primitive integers when invalid states would otherwise be representable
- Prefer standard conversion and borrowing traits (`From`, `TryFrom`, `AsRef`, `Borrow`, `IntoIterator`) when designing reusable APIs
- Public structs, enums, traits, and errors should have useful names, visibility, trait derives, and documentation appropriate to the crate boundary
- Avoid exposing concrete collection or synchronization types in public APIs when a slice, iterator, trait, or narrower abstraction would preserve flexibility

#### Macros and Metaprogramming
Only flag when the diff actually defines a `macro_rules!` or procedural macro; do not report on ordinary macro invocations.
- An `$x:expr` fragment interpolated more than once in the expansion, so the caller&#x27;s expression — and any side effects — runs multiple times; bind it to a `let` once inside the expansion
- Exported or publicly used macros that reference items without `$crate::`, so name resolution breaks or binds the wrong item when the macro is invoked from another crate
- Token-tree (`$t:tt`) fragments re-emitted without parentheses, where operator precedence can silently change the intended meaning; this applies only to token-level (`tt`) interpolation, since an `:expr` fragment and a whole expansion are each parsed as one complete expression
- Procedural macros that `unwrap()`, `expect()`, or `panic!` on malformed input instead of emitting a `syn::Error` / `compile_error!` with a useful span
- Macro-hygiene assumptions that break: generated identifiers relying on names from the call-site scope, or items that collide when the macro is invoked more than once in the same module

#### Security-Sensitive Code
- Validate path, URL, command, SQL, and serialized input before use; do not build shell commands or SQL with unchecked string concatenation
- Do not log secrets, tokens, credentials, private keys, or personally identifiable information
- Check integer conversions, byte slicing, and length arithmetic for overflow, truncation, and UTF-8 boundary errors
- Cryptographic, random, authentication, and authorization code must use well-reviewed crates and explicit error handling; flag ad hoc implementations
</code></pre>
{% endraw %}

</details>

<details id="ocr-prompt-runtime">
<summary><strong>循环控制 · 重试与预算收尾</strong></summary>

<p>下面两条消息由调查循环中的程序代码补入，不在六组任务模板里。第一条在模型未返回工具调用时提醒继续；第二条在预算耗尽时限制为提交已有发现或结束任务。</p>

<p><a href="https://github.com/alibaba/open-code-review/blob/375f64cd6f67a48a135da83cfcf59c52669a09cb/internal/llmloop/loop.go">循环实现</a></p>

<p><strong>未返回工具调用</strong></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>You did not successfully call any tools. Please try again or use task_done if finished.</code></pre>
{% endraw %}

<p><strong>预算耗尽后的最后一轮</strong></p>

{% raw %}
<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>Your review budget is exhausted. This is your FINAL round. You may ONLY:
- Call code_comment to submit any findings you have identified but not yet reported.
- Call task_done if you have nothing more to report.
No other tools are available. Do not attempt further analysis.</code></pre>
{% endraw %}

</details>

<!-- OCR_PROMPTS_END -->
