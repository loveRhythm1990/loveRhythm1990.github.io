---
layout: post
title: "AI Code Review：从缺陷取证到效果评估"
date: 2026-10-19 18:00:00 +0800
author: "weak old dog"
header-img-credit: false
tags:
    - Harness
    - Code Review
---

一句“可能存在越权”会把调查工作留给开发者。审查者还得查明请求从哪里进入、授权在哪一层执行、什么输入会导致数据泄露。AI 应该把这些证据找齐，再提交评论。

代码和测试都由 AI 编写时，两者可能沿用同一个错误前提：以为别处已经做了授权，只测试合法用户读取自己的数据。审查需要重新核对这个前提。

## 越权线索

下面是一个合成例子。订单服务用共享表保存多个租户的数据，租户身份来自登录上下文。一次查询重构删掉了租户条件：

```diff
 func (s *Store) GetOrder(ctx context.Context, tenantID, orderID string) (*Order, error) {
     var order Order
     err := s.db.GetContext(ctx, &order,
-        "SELECT id, tenant_id, total FROM orders WHERE tenant_id = ? AND id = ?",
-        tenantID, orderID,
+        "SELECT id, tenant_id, total FROM orders WHERE id = ?",
+        orderID,
     )
     return &order, err
 }
```

原来的查询要求租户和订单 ID 同时匹配，新查询只看订单 ID。现有测试只验证 A 读取 A 的订单，因此仍可能通过。

这段 diff 提供了越权线索。能否报告缺陷，还取决于其他层是否校验了订单归属。若数据库本身隔离租户，或者 handler 在返回前可靠地检查归属，SQL 缺少这个条件也可能是正确实现。

## 审查流程

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-workflow-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-workflow.png" alt="AI 审查流程：固定版本和约束、提出行为反例、沿调用链取证、给出缺陷评论、执行回归验证" width="920" loading="lazy">
</picture>

### 约束与反例

先明确本次改动允许改变什么，以及必须保留什么：

```text
范围：本次 PR 的变更。
意图：重构订单查询，不改变访问权限。
约束：租户只能读取自己的订单。
架构前提：共享订单表，数据库不提供额外的租户隔离。
```

约束来自需求，架构前提需要从代码或配置确认。模型缺少这些信息时，只能猜测什么算错误。例如，同一个字段改名，对内部变量可能没有影响，对已有客户端依赖的响应字段则可能构成破坏。

长期约束可以保存在项目指导文件中，一次改动的特殊意图随审查任务提供。[Codex 的项目规则说明](https://developers.openai.com/blog/custom-code-review-rules-for-codex)也采用这种方式传递兼容要求和数据边界。

对这次改动，最有用的问题是：

> A 使用自己的登录身份，请求 B 的一个有效订单 ID，会得到什么？

这个场景明确了请求者、输入和错误结果，能指引后续搜索。类似地，增加重试后可以检查“服务端已成功但响应丢失，再次执行是否重复写入”；缩小锁范围后可以检查“两个请求是否同时通过检查并重复创建资源”。

反例还必须可达。一个永远不会由调用方传入的值，或者需求允许的行为变化，都不足以支持缺陷评论。

### 调用链取证

沿着这个请求检查三处代码：

1. **入口**：handler 从登录上下文取得租户 ID，从请求参数取得订单 ID。A 可以提交 B 的订单 ID。
2. **返回路径**：handler 查询成功后直接返回，没有检查订单归属。
3. **数据层**：使用共享连接，没有额外注入租户过滤，也没有数据库隔离策略。

如果合成案例中这三个条件都成立，触发路径就完整了：A 的请求按 B 的订单 ID 命中记录，结果未经归属检查就被返回。

如果找到可靠的授权检查，应撤回原假设；如果关键代码无法读取，就列出缺失的证据。搜索没有命中，不能直接写成“系统没有校验”。

### 缺陷验证

调查结果应直接写进评论：

> 查询只匹配订单 ID，handler 查询后直接返回，数据库也没有额外隔离。租户 A 请求租户 B 的有效订单 ID 时，会收到 B 的订单。请恢复租户限制，并补充跨租户访问测试。

实际报告附上变更位置和相关调用路径。开发者可以据此核查前提、构造请求和修复问题，无需从“建议加强权限校验”重新开始调查。

| 请求 | 正确行为 | 缺陷版本的行为 |
|---|---|---|
| A 读取 A 的订单 | 返回 A 的订单 | 返回 A 的订单 |
| A 读取 B 的订单 | 不返回 B 的订单 | 返回 B 的订单 |
| A 读取不存在的订单 | 返回未找到 | 返回未找到 |

第二行才区分了错误实现和正确实现。拒绝访问或返回未找到都可以，具体按接口约定，关键是不泄露 B 的订单。

让同一份跨租户测试在缺陷版本上失败、在修复后通过，再运行相关回归测试。测试应经过应用授权和真实查询路径，直接 mock 查询结果可能绕过被审查的逻辑。

断言必须来自需求。如果模型按错误实现生成预期，再证明实现符合预期，测试也会通过。

### 复核任务

第二轮审查可以专门检查第一轮的前提，而不是继续要求增加问题数量：

```text
复核这条越权发现：
查找上层授权、数据库隔离和不可达路径。
找到反证就撤回发现；仍缺证据就说明缺什么。
```

换会话或换模型可以改变调查路径，但不同审查者仍可能共享盲点。最终依据是代码与行为证据。

大 PR 则应按调用链组织调查，让 handler、service、store 和测试能互相参照。拆分后还要检查跨组接口，否则授权逻辑和查询被分开，双方都可能以为对方已经检查。

### 任务模板

```text
请审查本次 PR，先给报告。
变更意图：<本次有意改变的行为>
项目约束：<必须保持的行为及其来源>

1. 确认范围，记录跳过或无法读取的文件。
2. 找出新旧行为不同的具体场景，检查调用链和正确例外。
3. 每条发现给出位置、触发条件、错误结果和代码依据。
4. 区分已确认发现与待确认项，不凑数量。
5. 列出实际执行的验证及结果，没执行的不要写成通过。
```

格式、类型和已有静态规则交给 CI。AI 的工作重点是调查跨文件行为、检查项目约束，并把候选缺陷推进到可验证的程度。


## 代码图

审查订单查询，往往要读没有修改过的 handler、数据库封装和测试。文本搜索会返回定义、导入、调用和同名符号，agent 还得逐项辨认。[Code Review Graph](https://github.com/tirth8205/code-review-graph)把一部分结构分析提前完成，供后续任务查询。

### 构建与查询

项目用 Tree-sitter 等机制解析源码，以 SQLite 保存节点与边。函数、类和文件是节点，调用、导入、引用等关系是边。例如：

```text
handlers.py ──导入──→ store.py
get_order_handler ──调用──→ get_order
```

提取调用语法之后，还要解析目标定义。别名、继承、类型和同名符号都会影响结果，项目通过语言相关的解析逻辑补充这些信息。[构建实现](https://github.com/tirth8205/code-review-graph/blob/343ab7cea29d47a3e805381e09f8243a43d43d88/code_review_graph/incremental.py)

审查时，从变更文件中的节点出发，沿关系查找调用方和测试。查询深度、返回数量与源码片段都有预算限制；`truncated` 和省略计数表示结果没有完整返回，需要继续查询。[查询接口](https://github.com/tirth8205/code-review-graph/blob/343ab7cea29d47a3e805381e09f8243a43d43d88/docs/COMMANDS.md)

索引通过文件哈希和依赖关系增量维护，减少重复解析。不过，内容核对和派生关系更新仍有成本。切换代码、构建中断或部分文件解析失败后，要检查索引状态，不能直接信任旧结果。[更新入口](https://github.com/tirth8205/code-review-graph/blob/343ab7cea29d47a3e805381e09f8243a43d43d88/code_review_graph/tools/build.py)

### 订单实验

用 Python 和 SQLite 重现上面的订单问题，观察代码图能提供哪些证据。示例包含五个源码文件：

```text
store.py        查询订单
handlers.py     取得登录租户，调用查询并返回结果
test_orders.py  只测试 A 读取 A
jobs.py         用字典注册、分发查询函数
health.py       无关的健康检查
```

正常查询同时过滤租户和订单 ID，缺陷查询只过滤订单 ID。实验先建图，再删除租户条件、增量更新，最后恢复查询。它只检查结构查询和 SQL 行为，没有调用大模型。

[示例目录](https://github.com/loveRhythm1990/loveRhythm1990.github.io/tree/master/code-examples/ai-code-review)保留脚本和观测结果。在安装 Git、uv 后运行，首次需要下载依赖：

```bash
uv run code-examples/ai-code-review/graph_demo.py \
  --output /tmp/graph-demo-result.json
```

查询 `get_order` 的直接调用方，得到了 `get_order_handler`。两跳影响范围还包含 `jobs.py` 和 `test_orders.py`，没有包含无关的 `health.py`。

裸名称 `get_order` 同时匹配了 `get_order_handler`，工具先返回 `ambiguous`。使用限定名重新查询后才得到上述调用方。歧义没有解决，就不能把结果当成空集。

`get_review_context` 在本例只返回了 `store.py` 的源码片段。虽然 handler 已列入影响范围，agent 仍需打开它，检查返回前的授权逻辑。

动态分发则暴露了另一种边界：

```python
JOBS = {"export": get_order}

def run_job(db, name, tenant_id, order_id):
    return JOBS[name](db, tenant_id, order_id)
```

图没有给出 `run_job → get_order` 的直接调用边，`jobs.py` 仍通过导入和引用关系被选中。引用能留下调查线索，具体调用目标还要读注册表和分发代码。

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-graph-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-graph.png" alt="订单实验：测试调用 handler，handler 调用查询函数；直接调用方相同，删除租户过滤后跨租户请求的结果发生变化" width="920" loading="lazy">
</picture>

删除租户过滤前后，工具识别到的直接调用方相同，返回行为却变了：A 请求 B 的订单，从未找到变为返回 B 的订单；恢复过滤后，又回到未找到。

三个版本的“读取自己订单”测试全部通过。图中的测试关联只能帮助找到这份测试，是否覆盖了跨租户场景，取决于断言。模型必须读 SQL、读断言，再设计能区分这两个版本的请求。

图在本例中找到了 handler 和相关测试，排除了一个无关文件；越权判断所需的 SQL 和断言仍需继续阅读。

### Token 成本

项目提供了几种不同的评测口径。整库文本与图查询的比较，可以得到很大的缩减倍率；但已有 agent 通常会搜索后再读取部分文件，不会每次都输入整个仓库。项目也提供模拟搜索基线，以及变更文件与审查上下文的比较。[评测说明](https://github.com/tirth8205/code-review-graph/blob/343ab7cea29d47a3e805381e09f8243a43d43d88/docs/REPRODUCING.md)

要知道实际节省多少，比较对象应是同一个 agent 完成同一项调查的全过程：

- 普通搜索：搜索结果、打开的源码和后续对话。
- 增加代码图：图查询结果、随后补读的源码和后续对话。

再分别统计输入输出、缓存费用和等待时间。图本身的构建、更新成本另计。一次紧凑查询返回，并不代表后续不需要读文件。

本次小实验的 `context_savings` 只给出估算，`saved_tokens` 为零。对几行就能读完的查询，关系元数据可能比源码还长；它不能用来支持“每次审查都会节省 token”。

评估时，让人工列出关键调用方，核对图有没有遗漏，再比较完整调查的成本。反复追踪跨文件关系时，索引可能减少重复探索；一次搜索就能读清的小改动，则可能不值得建图。


## 工具选择

同一个越权问题，可能在不同环节漏掉：相关测试被排除，删除的授权检查没有进入模型输入，或者模型没有读到上层 handler。选择工具，先检查它怎样处理这些环节。

### 原生审查

已经使用 Codex 或 Claude Code 的团队，可以先用原生专用审查入口。Codex 的 GitHub 审查读取适用的 `AGENTS.md`，Claude Code 的本地 `/code-review` 遵循 `CLAUDE.md`。[Codex 审查规则](https://learn.chatgpt.com/docs/third-party/github)、[Claude 本地审查](https://code.claude.com/docs/en/code-review-local)

比较时，双方应得到同一份代码和业务背景。把“共享表没有额外租户隔离”告诉 OCR，却不给原生 agent，测到的首先是信息差。普通对话、本地专用命令与托管审查也要分开，不能用一句泛泛的“帮我看看”代表原生工具的全部能力。

### OCR 任务编排

阿里开源的 [OpenCodeReview](https://github.com/alibaba/open-code-review)把文件选择、规则匹配、任务组织和评论处理做成独立流程。可以在调用模型前检查本次究竟会审哪些文件、装入哪些规则：

```bash
ocr review --preview
ocr rules check internal/store/orders.go
```

例如，订单模块希望把实现和测试一起审查，可以配置：

```json
{
  "include": ["internal/**/*_test.go"],
  "rules": [{
    "path": "internal/store/**/*.go",
    "rule": "租户只能读取自己的订单；沿调用链确认是否有等效授权，已有可靠隔离时不要误报。",
    "merge_system_rule": true
  }]
}
```

`include` 让这些测试文件绕过默认排除，不是只审这些文件的白名单。`merge_system_rule` 则保留内置规则，再加入业务要求。[规则说明](https://github.com/alibaba/open-code-review/blob/27def8684b3f5c5f235871fac2957be58262f43d/pages/src/content/docs/en/review-rules.md)

这些机制使输入可检查：测试为什么没进入任务，文件匹配到了什么要求，可以从程序结果核对。模型是否充分理解、能否发现越权，仍要检查实际输出。

正式审查时，模型拿到分组的 diff 和规则，按需要调用代码搜索、文件读取等工具，再提交评论。程序执行工具，把结果追加到上下文，让模型继续调查，最后定位评论。这种循环允许模型在读到 handler 后修正对 SQL 的判断。[工具接口](https://github.com/alibaba/open-code-review/blob/27def8684b3f5c5f235871fac2957be58262f43d/internal/config/toolsconfig/tools.json)

OCR 的评论过滤阶段主要看到 diff 和候选评论，删除部分能被 diff 直接推翻的意见，并优先保留行为变化、并发等主题。它可以清理局部矛盾，但这些输入不足以重新调查整个授权链。[过滤提示词](https://github.com/alibaba/open-code-review/blob/27def8684b3f5c5f235871fac2957be58262f43d/internal/config/template/prompts/review_filter_task_user.md)

OCR 的委托模式更窄：只提供文件选择和规则解析，调查由宿主 agent 完成。比较效果时，要注明使用哪种模式。[委托模式](https://github.com/alibaba/open-code-review/blob/27def8684b3f5c5f235871fac2957be58262f43d/pages/src/content/docs/en/integrations/delegate.md)

### PR-Agent 输入裁剪

[PR-Agent](https://github.com/The-PR-Agent/pr-agent/tree/ab90e6e8545ea4d983a35ad0bfdc295c49727e24)取得 PR 变更后，扩展局部上下文，并按预算压缩或分块。社区项目与 Qodo 商业产品已经分开，这里讨论公开仓库中的实现。

补入周边函数有助于理解 diff，但超出预算时，裁掉什么会直接影响漏检。它的一种压缩路径会保留含新增内容的 hunk，省略同一文件中的纯删除 hunk。[输入处理](https://github.com/The-PR-Agent/pr-agent/blob/ab90e6e8545ea4d983a35ad0bfdc295c49727e24/pr_agent/algo/pr_processing.py)、[删除处理](https://github.com/The-PR-Agent/pr-agent/blob/ab90e6e8545ea4d983a35ad0bfdc295c49727e24/pr_agent/algo/git_patch_processing.py)

设想一个文件有两处相距较远的改动：一处删除归属检查，另一处新增无关日志。进入上述压缩路径后，模型可能只看到新增日志，丢失判断越权的关键删除。这个问题应检查最终模型输入，光核对原始 Git diff 不够。

这个条件不能扩大成“删除都会消失”：若压缩结果为空，处理函数会保留原 patch；没有触发压缩的路径也应另看。前面的 SQL 替换同时有新增和删除行，不属于这个纯删除例子。这是源码分析得到的风险，本文没有运行模型漏检实验。

大 PR 还支持可选的 `enable_large_pr_chunking`，拆分后分别审查、合并结果。分块能增加装入的内容，却也要检查跨块的调用关系是否被遗漏。[审查实现](https://github.com/The-PR-Agent/pr-agent/blob/ab90e6e8545ea4d983a35ad0bfdc295c49727e24/pr_agent/tools/pr_reviewer.py)

### 商业工具

商业产品也提供了几种可研究的设计：

- [CodeRabbit](https://docs.coderabbit.ai/overview/architecture)组合静态分析、agent 探索和候选发现验证，可以检查这些步骤是否增加真实发现、减少误报。
- [Greptile](https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context)索引代码关系，适合检查它是否找到未修改的关键调用方和跨文件影响。
- [Cursor Bugbot](https://cursor.com/docs/bugbot)利用增量审查和已有 PR 评论，适合检查补审、去重和修复状态的处理。

这些描述来自官方文档，实际效果要放到相同样本中检验。

## 效果评估

用订单问题可以准备五类样本：

1. **真实缺陷**：删除租户过滤，其他层也没有隔离，应报告越权并给出触发路径。
2. **正确例外**：SQL 不带租户，但返回前有可靠授权，应避免误报。
3. **正常改动**：权限不变，只改命名和位置，用于检查噪声。
4. **复杂输入**：在大量无关修改中放入真实缺陷，并加入前述两个独立 hunk 的例子，检查筛选和裁剪是否遗漏证据。
5. **后续修复**：修复后再提交修改，检查是否补审，并正确更新旧发现。

还可以恢复历史缺陷进入时的 PR，向审查者提供当时可获得的需求和约束，隐藏后续修复信息。不要在任务里提前告诉它“这里有越权”。

<picture>
  <source media="(max-width: 600px)" srcset="{{ site.baseurl }}/pics/ai-code-review-evaluation-mobile.png">
  <img src="{{ site.baseurl }}/pics/ai-code-review-evaluation.png" alt="对照评测：固定代码、背景和验证条件，分别比较有无项目规则、有无代码图、开启或关闭复核，并记录发现质量和总成本" width="920" loading="lazy">
</picture>

规则、图检索和复核分别开关，其他条件尽量相同，才容易解释收益来源。如果连模型、预算和工具权限都一起换了，结果只能归属于整个产品组合。

每次运行记录三组结果：

- **发现质量**：已知缺陷找到了多少，已核对的独立发现有多少成立。工具额外找到的问题也要核对，不能因为不在预设清单中就算误报。
- **完成范围**：哪些内容被排除、跳过或执行失败。失败而没有评论，不能作为正常的“未发现问题”计入。
- **处理成本**：总 token、费用、等待时间，以及人工确认、复现和排除误报的时间。重复评论合并计算发现数量，处理它们的成本仍要保留。

同一配置运行多次，观察稳定性；调规则用的样本与验收样本分开，避免只对几个已知案例有效。

采用工具的依据是它稳定补充了原流程遗漏的真实问题，或者在保持发现质量的同时降低了处理成本。本文没有做完整的产品对照实验，因此不提供质量排名。
