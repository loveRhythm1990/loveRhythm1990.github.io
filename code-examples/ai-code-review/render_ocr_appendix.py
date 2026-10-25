"""Render the article's collapsible OCR prompt appendix from local source copies."""

from html import escape
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent / "ocr-prompts"
POST = ROOT / "_posts/2026-10-19-open-code-review-workflow.markdown"
BEGIN = "<!-- OCR_PROMPTS_BEGIN -->"
END = "<!-- OCR_PROMPTS_END -->"

TASKS = [
    ("GROUPING_TASK", "grouping", "文件分组", "输入文件元数据，输出标签和文件序号组成的 JSON 分组。"),
    ("PLAN_TASK", "plan", "审查计划", "可选阶段，只生成调查计划；提示词描述了工具，但该调用不执行工具。"),
    ("MAIN_TASK", "main", "工具调查", "输入本组 diff、规则等上下文，并通过工具调用继续调查。"),
    ("MEMORY_COMPRESSION_TASK", "memory", "记忆压缩", "长会话的辅助调用，压缩既有调查记录供主任务继续使用。"),
    ("RE_LOCATION_TASK", "relocation", "评论重定位", "片段匹配失败时，从 diff 重新摘出定位片段。"),
    ("REVIEW_FILTER_TASK", "filter", "评论过滤", "依据 diff 和候选评论，判断哪些意见应当删除。"),
]


def literal(content):
    # Liquid runs before Markdown. Escape HTML and protect template placeholders.
    return (
        "{% raw %}\n"
        '<pre style="white-space: pre-wrap; overflow-wrap: anywhere;"><code>'
        + escape(content)
        + "</code></pre>\n"
        "{% endraw %}\n"
    )


def main():
    meta = json.loads((SOURCE / "source.json").read_text())
    template = json.loads((SOURCE / "task_template.json").read_text())
    upstream = f"https://github.com/{meta['repository']}/blob/{meta['revision']}/"
    sources = {entry["local"]: entry["upstream"] for entry in meta["files"]}
    blocks = [
        BEGIN,
        "## 附录：OCR 提示词\n",
        "下面保留六组任务的 system/user 模板原文，以及 Rust 规则示例和循环补充指令。"
        "文件选择、规则匹配和调度没有独立提示词，执行方式见正文。点击展开各组即可阅读。\n",
        "占位符保留原样。实际请求还会填入 diff、规则、背景和工具定义，因此这里展示的是模板，"
        "不是一次真实审查的会话回放。原文件与调用配置保存在"
        "[配套目录](https://github.com/loveRhythm1990/loveRhythm1990.github.io/tree/master/code-examples/ai-code-review/ocr-prompts)。\n",
        "原文来自 alibaba/open-code-review，Copyright 2026 alibaba/open-code-review Contributors，"
        "按 Apache-2.0 保留；[许可证副本](https://github.com/loveRhythm1990/loveRhythm1990.github.io/blob/master/code-examples/ai-code-review/ocr-prompts/LICENSE)。"
        "附录只增加说明和展示格式，模板正文未改写。\n",
    ]
    for task, slug, title, note in TASKS:
        blocks += [
            f'<details id="ocr-prompt-{slug}">',
            f"<summary><strong>{title} · {task}</strong></summary>\n",
            f"<p>{note}</p>\n",
        ]
        for message in template[task]["messages"]:
            name = message["prompt_file"]
            local = "prompts/" + name
            url = upstream + sources[local]
            blocks += [
                f'<p><strong>{message["role"]}</strong> · <a href="{url}">{name}</a></p>\n',
                literal((SOURCE / local).read_text()),
            ]
        blocks.append("</details>\n")

    blocks += [
        '<details id="ocr-prompt-rust">',
        "<summary><strong>语言规则 · Rust</strong></summary>\n",
        '<p>这份规则通过主任务和计划任务的 <code>{{system_rule}}</code> 占位符装入。'
        "它是提示词的一部分，不是另一轮独立审查。</p>\n",
        f'<p><a href="{upstream}{sources["rust.md"]}">rust.md</a></p>\n',
        literal((SOURCE / "rust.md").read_text()),
        "</details>\n",
        '<details id="ocr-prompt-runtime">',
        "<summary><strong>循环控制 · 重试与预算收尾</strong></summary>\n",
        "<p>下面两条消息由调查循环中的程序代码补入，不在六组任务模板里。"
        "第一条在模型未返回工具调用时提醒继续；第二条在预算耗尽时限制为提交已有发现或结束任务。</p>\n",
        f'<p><a href="{upstream}internal/llmloop/loop.go">循环实现</a></p>\n',
    ]
    runtime = json.loads((SOURCE / "runtime_messages.json").read_text())
    for label, key in [("未返回工具调用", "no_tool_calls"), ("预算耗尽后的最后一轮", "budget_exhausted")]:
        blocks += [f"<p><strong>{label}</strong></p>\n", literal(runtime[key])]
    blocks += ["</details>\n", END]
    appendix = "\n".join(blocks) + "\n"
    content = POST.read_text()
    if BEGIN in content:
        before, rest = content.split(BEGIN, 1)
        _, after = rest.split(END, 1)
        content = before.rstrip() + "\n\n" + appendix + after.lstrip("\n")
    else:
        content = content.rstrip() + "\n\n" + appendix
    # The explanatory reference needs Liquid protection just like prompt bodies.
    content = content.replace(
        "<code>{{system_rule}}</code>",
        "<code>{% raw %}{{system_rule}}{% endraw %}</code>",
    )
    POST.write_text(content)
    print("Rendered 6 task pairs, Rust rules and 2 runtime messages")


if __name__ == "__main__":
    main()
