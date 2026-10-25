"""Render editable SVG diagrams to PNG with Python 3 and rsvg-convert."""

from html import escape
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent / "figures"
OUTPUT = ROOT / "pics"
INK = "#172b46"
MUTED = "#52647b"
BLUE = "#2563b8"


def text(x, y, label, size=24, color=INK, weight="400", anchor="start"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{escape(label)}</text>'
    )


def rect(x, y, width, height, fill="#ffffff", stroke="#d7e1ee"):
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="16" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    )


def arrow(x1, y1, x2, y2):
    return (
        f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="{BLUE}" '
        'stroke-width="3" marker-end="url(#arrow)"/>'
    )


def canvas(title, body, width=1000, height=214):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<title>{escape(title)}</title>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{BLUE}"/></marker></defs>
<rect width="{width}" height="{height}" fill="#ffffff"/>
<g font-family="Heiti SC, Noto Sans CJK SC, sans-serif">
{body}
</g></svg>
'''


def workflow(mobile=False):
    steps = [
        ("确定范围", "基准与目标", "原始变更清单"),
        ("准备输入", "筛选与分组", "预算与提示词"),
        ("模型调查", "提出候选问题", "按需搜索和读文件"),
        ("处理发现", "定位与复核", "可选行为验证"),
        ("报告结果", "评论与执行状态", "未审内容及原因"),
    ]
    body = []
    for index, (title, detail1, detail2) in enumerate(steps):
        if mobile:
            x, y, width, height = 16, 12 + index * 76, 448, 60
            body += [rect(x, y, width, height)]
            body += [text(34, y + 25, str(index + 1), 20, BLUE, "600")]
            body += [text(68, y + 25, title, 23, weight="600")]
            body += [text(68, y + 49, f"{detail1} · {detail2}", 18, MUTED)]
            if index < 4:
                body += [arrow(240, y + 64, 240, y + 72)]
        else:
            x, y = 24 + index * 196, 16
            body += [rect(x, y, 168, 132, "#f8faff")]
            body += [text(x + 84, 43, str(index + 1), 19, BLUE, "600", "middle")]
            body += [text(x + 84, 74, title, 23, weight="600", anchor="middle")]
            body += [text(x + 84, 104, detail1, 18, MUTED, anchor="middle")]
            body += [text(x + 84, 131, detail2, 18, MUTED, anchor="middle")]
            if index < 4:
                body += [arrow(x + 176, 82, x + 188, 82)]
    if mobile:
        body += [text(240, 412, "调查可多次调用工具，再继续判断", 19, MUTED, anchor="middle")]
        return canvas("从 diff 到审查结果", "\n".join(body), 480, 428)
    body += [arrow(500, 154, 500, 178)]
    body += [rect(404, 186, 192, 64, "#f3f7fe")]
    body += [text(500, 212, "搜索 / 读文件", 22, BLUE, "600", "middle")]
    body += [text(500, 237, "工具结果返回模型", 18, MUTED, anchor="middle")]
    body += [f'<path d="M596 218 H600 V124 H590" fill="none" stroke="{BLUE}" stroke-width="3" marker-end="url(#arrow)"/>']
    body += [text(500, 282, "调查循环可能重复，也可能因预算、超时或工具失败而停止", 18, MUTED, anchor="middle")]
    return canvas("从 diff 到审查结果", "\n".join(body), height=300)


def ocr_pipeline(mobile=False):
    steps = [
        ("文件选择", "程序", "Git 差异与路径筛选", "清单和排除理由"),
        ("规则匹配", "程序", "按文件路径选要求", "装入审查提示词"),
        ("文件分组", "模型 + 程序", "模型只看文件元数据", "程序校验与补齐"),
        ("任务调度", "程序", "并发、预算和超时", "各组独立上下文"),
        ("工具调查", "模型 + 程序", "模型选择搜索和读取", "工具结果回到模型"),
        ("评论处理", "模型 + 程序", "定位、重定位与过滤", "评论和执行状态"),
    ]
    body = []
    for index, (title, owner, detail1, detail2) in enumerate(steps):
        if mobile:
            x, y, width, height = 16, 12 + index * 66, 448, 56
            body += [rect(x, y, width, height, "#f8faff")]
            body += [text(32, y + 24, f"{index + 1}  {title}", 22, weight="600")]
            body += [text(448, y + 23, owner, 17, BLUE, anchor="end")]
            body += [text(32, y + 47, f"{detail1} · {detail2}", 17, MUTED)]
        else:
            x, y = 24 + index * 196, 16
            body += [rect(x, y, 168, 152, "#f8faff")]
            body += [text(x + 84, y + 28, f"{index + 1}  {owner}", 18, BLUE, "600", "middle")]
            body += [text(x + 84, y + 66, title, 25, weight="600", anchor="middle")]
            body += [text(x + 84, y + 103, detail1, 18, MUTED, anchor="middle")]
            body += [text(x + 84, y + 132, detail2, 18, MUTED, anchor="middle")]
            if index < 5:
                body += [arrow(x + 176, 92, x + 188, 92)]
    if mobile:
        body += [text(240, 429, "辅助调用：计划、记忆压缩、评论重定位", 19, MUTED, anchor="middle")]
        return canvas("OCR 的六步执行过程", "\n".join(body), 480, 446)
    body += [text(600, 207, "每组内部：可选计划 → 工具调用循环 → 候选评论", 21, BLUE, anchor="middle")]
    body += [text(600, 245, "计划、记忆压缩和重定位按条件触发；文件筛选、规则匹配与调度没有独立提示词", 18, MUTED, anchor="middle")]
    return canvas("OCR 的六步执行过程", "\n".join(body), width=1200, height=268)


def graph(mobile=False):
    nodes = [
        ("test_own_order", "只验证自己的订单"),
        ("get_order_handler", "返回前无归属校验"),
        ("get_order", "租户过滤发生变化"),
    ]
    body = []
    for index, (title, detail) in enumerate(nodes):
        x, y, width, height = (16, 12 + index * 112, 448, 82) if mobile else (24 + index * 336, 20, 280, 96)
        body += [rect(x, y, width, height, "#f8faff")]
        body += [text(x + width / 2, y + 34, title, 23, weight="600", anchor="middle")]
        body += [text(x + width / 2, y + 65, detail, 20, MUTED, anchor="middle")]
        if index < 2:
            if mobile:
                body += [arrow(240, y + 89, 240, y + 105)]
            else:
                body += [arrow(x + 288, 68, x + 328, 68)]
                body += [text(x + 308, 48, "调用", 17, BLUE, anchor="middle")]
    if mobile:
        body += [text(24, 360, "直接调用方相同，SQL 行为不同", 22, weight="600")]
        body += [text(24, 402, "正常 / 修复：A 请求 B → 未找到", 20, "#116850")]
        body += [text(24, 440, "删除过滤：A 请求 B → 返回 B 的订单", 20, "#a33a32")]
        return canvas("代码图与行为验证", "\n".join(body), 480, 464)
    body += [text(500, 154, "直接调用方相同，SQL 行为不同", 22, weight="600", anchor="middle")]
    body += [rect(24, 178, 460, 96, "#f3faf7", "#c6dfd4")]
    body += [text(48, 214, "正常 / 修复", 22, "#116850", "600")]
    body += [text(48, 250, "A 请求 B → 未找到", 21, "#116850")]
    body += [rect(516, 178, 460, 96, "#fff7f6", "#ebceca")]
    body += [text(540, 214, "删除租户过滤", 22, "#a33a32", "600")]
    body += [text(540, 250, "A 请求 B → 返回 B 的订单", 21, "#a33a32")]
    body += [text(500, 320, "调用关系不代表授权正确，测试关联不代表测试已执行", 19, MUTED, anchor="middle")]
    return canvas("代码图与行为验证", "\n".join(body), height=340)


def evaluation(mobile=False):
    layers = [
        ("文件覆盖", "文件有没有进入并完成任务", "证据：清单与执行状态", "可以对账"),
        ("调查覆盖", "关键路径有没有继续追查", "证据：读取、搜索与验证记录", "只能核对已记录的调查"),
        ("缺陷覆盖", "真实问题到底发现了多少", "证据：已知缺陷与独立样本", "真实系统通常没有完整答案"),
    ]
    body = []
    for index, (title, question, evidence, limit) in enumerate(layers):
        if mobile:
            x, y, width, height = 16, 12 + index * 112, 448, 98
            body += [rect(x, y, width, height, "#f8faff")]
            body += [text(34, y + 29, title, 23, weight="600")]
            body += [text(34, y + 57, question, 20, MUTED)]
            body += [text(34, y + 83, limit, 19, BLUE)]
        else:
            x, y, width, height = 20 + index * 332, 16, 296, 192
            body += [rect(x, y, width, height, "#f8faff")]
            body += [text(x + width / 2, y + 40, title, 25, weight="600", anchor="middle")]
            body += [text(x + width / 2, y + 81, question, 19, MUTED, anchor="middle")]
            body += [text(x + width / 2, y + 117, evidence, 18, MUTED, anchor="middle")]
            body += [text(x + width / 2, y + 161, limit, 19, BLUE, anchor="middle")]
    if mobile:
        body += [text(240, 376, "任务完成不代表关键路径和缺陷已查全", 20, MUTED, anchor="middle")]
        return canvas("审查覆盖的三层", "\n".join(body), 480, 396)
    body += [text(500, 247, "文件任务全部完成，仍然可能漏掉关键路径和缺陷", 20, MUTED, anchor="middle")]
    return canvas("审查覆盖的三层", "\n".join(body), height=270)


def main():
    renderer = shutil.which("rsvg-convert")
    if renderer is None:
        raise SystemExit("Install librsvg to provide rsvg-convert, then run this script again.")
    SOURCE.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, draw in {
        "ai-code-review-workflow": workflow,
        "ai-code-review-ocr-pipeline": ocr_pipeline,
        "ai-code-review-graph": graph,
        "ai-code-review-evaluation": evaluation,
    }.items():
        for mobile in (False, True):
            suffix = "-mobile" if mobile else ""
            source = SOURCE / f"{name}{suffix}.svg"
            target = OUTPUT / f"{name}{suffix}.png"
            source.write_text(draw(mobile), encoding="utf-8")
            subprocess.run([renderer, "--zoom", "2", "--output", str(target), str(source)], check=True)
            print(f"Rendered {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
