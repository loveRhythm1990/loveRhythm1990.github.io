# AI Code Review：代码图最小实验

`graph_demo.py` 创建一个临时的 Python/SQLite 订单项目，固定 Code Review
Graph 源码提交，执行建图、增量更新、调用查询和行为验证。它不会调用大模型、
安装 agent 集成或修改用户的 MCP、hooks、AGENTS.md。

在仓库根目录执行：

```bash
uv run --python 3.13 code-examples/ai-code-review/graph_demo.py --output /tmp/graph-demo-result.json
```

`uv` 需要网络下载固定的 Git 依赖及其 Python 依赖。脚本使用临时工作目录；
图数据、注册表与合成 Git 仓库在实验结束后一起清理。输出报告用 `<demo>`
替代临时绝对路径。脚本固定项目 SHA；传递依赖未全部锁定，报告记录解析器和
Python 版本，未来复跑时应核对这些版本。

实验过程：

1. 建立五个合成源码文件和一个正常查询，检查 A 不能读取 B 的订单。
2. 建图，使用限定名查询 `get_order` 的直接调用方。
3. 删除查询中的租户条件，更新图并查询变更影响范围。
4. 执行已有的“读取自己订单”测试，确认它仍通过。
5. 额外执行跨租户请求，确认缺陷版本泄露结果，恢复查询后拒绝访问。

`observed-results.json` 是 2026-10-24 的实际观测，源码固定为
`tirth8205/code-review-graph@343ab7cea29d47a3e805381e09f8243a43d43d88`。
原始结果包含工具返回的结构信息和合成源码，没有模型审查结果。

需要注意：

- 裸名称 `get_order` 还匹配 `get_order_handler`，工具返回 `ambiguous`；脚本改用限定名。
- 修改前后，直接调用方都是 `get_order_handler`。
- 两跳影响范围还包含 `jobs.py` 和 `test_orders.py`，不包含 `health.py`。
- `jobs.py` 的动态字典调用没有被列为 `run_job → get_order` 的直接调用边；
  文件仍通过导入和引用关系进入影响范围。
- 本次 `get_review_context` 只返回 `store.py` 的源码片段；其余受影响文件需要另读。

这验证的是小样本中的结构查询、增量更新和 SQL 行为。它没有测量完整审查的
token 消耗，也不能用于比较任何模型或审查产品的缺陷检出率。

## 文章配图

`figures/` 保留可编辑的 SVG 源文件，文章引用 `pics/` 中的两倍分辨率 PNG。
配图说明通用审查循环、OCR 的六步执行流程、实验中的调用链与行为变化，以及三层审查覆盖。桌面版使用
紧凑的横向布局；`-mobile` 版本用于不超过 600px 的窄屏，由文章的 `picture`
元素切换，避免直接缩小横向图导致文字难以辨认。

安装提供 `rsvg-convert` 的 librsvg 后，在仓库根目录重新生成：

```bash
python3 code-examples/ai-code-review/render_figures.py
```

可以直接编辑 SVG，或修改 `render_figures.py` 后批量生成。重新运行脚本会覆盖
对应的 SVG 与 PNG；编辑 SVG 后，可用 `rsvg-convert --zoom 2` 单独导出 PNG。

## OCR 提示词附录

`ocr-prompts/` 保存来源明确的任务提示词、Rust 规则与调用配置，文章末尾以折叠块
展示。原文归属和许可证见该目录的 `README.md` 与 `LICENSE`。

修改本地副本后，重新生成文章附录：

```bash
python3 code-examples/ai-code-review/render_ocr_appendix.py
```
