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
        ("版本与约束", "代码快照", "业务约束"),
        ("行为反例", "输入与预期", "可达场景"),
        ("调用链取证", "调用方与数据层", "检查正确例外"),
        ("缺陷评论", "位置与触发条件", "影响与证据"),
        ("回归验证", "缺陷版本失败", "修复版本通过"),
    ]
    body = []
    for index, (title, detail1, detail2) in enumerate(steps):
        if mobile:
            x, y, width, height = 16, 12 + index * 84, 448, 64
            body += [rect(x, y, width, height)]
            body += [text(34, y + 27, str(index + 1), 20, BLUE, "600")]
            body += [text(68, y + 27, title, 23, weight="600")]
            body += [text(68, y + 52, f"{detail1} · {detail2}", 18, MUTED)]
            if index < 4:
                body += [arrow(240, y + 69, 240, y + 79)]
        else:
            x, y = 24 + index * 196, 20
            body += [rect(x, y, 168, 140, "#f8faff")]
            body += [text(x + 84, 49, str(index + 1), 19, BLUE, "600", "middle")]
            body += [text(x + 84, 82, title, 23, weight="600", anchor="middle")]
            body += [text(x + 84, 112, detail1, 19, MUTED, anchor="middle")]
            body += [text(x + 84, 139, detail2, 19, MUTED, anchor="middle")]
            if index < 4:
                body += [arrow(x + 176, 90, x + 188, 90)]
    if mobile:
        return canvas("从变更到缺陷验证", "\n".join(body), 480, 424)
    body += [text(500, 197, "每一步保留证据，结论绑定具体版本", 19, MUTED, anchor="middle")]
    return canvas("从变更到缺陷验证", "\n".join(body))


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
    pairs = [
        ("规则对照", "不提供规则 / 提供项目规则"),
        ("图检索对照", "普通搜索 / 增加代码图查询"),
        ("复核对照", "关闭复核 / 开启复核"),
    ]
    if mobile:
        body = [text(24, 32, "固定代码、背景、模型、预算与验证条件", 21, weight="600")]
        for index, (title, detail) in enumerate(pairs):
            y = 56 + index * 94
            body += [rect(16, y, 448, 78, "#f8faff")]
            body += [text(36, y + 30, title, 23, weight="600")]
            body += [text(36, y + 59, detail, 20, MUTED)]
        body += [text(24, 368, "记录召回、误报、执行范围与总成本", 21, BLUE)]
        body += [text(24, 408, "每组单独运行，只改变一个因素", 20, MUTED)]
        return canvas("评测中的变量控制", "\n".join(body), 480, 432)
    body = [f'<path d="M244 149 H284 M284 63 V255 M724 63 V255 M724 149 H756" fill="none" stroke="{BLUE}" stroke-width="3"/>']
    for index, (title, detail) in enumerate(pairs):
        y = 24 + index * 96
        body += [arrow(284, y + 39, 316, y + 39)]
        body += [f'<path d="M684 {y + 39} H724" fill="none" stroke="{BLUE}" stroke-width="3"/>']
        body += [rect(324, y, 360, 78, "#f8faff")]
        body += [text(344, y + 31, title, 23, weight="600")]
        body += [text(344, y + 60, detail, 19, MUTED)]
    body += [arrow(724, 149, 756, 149)]
    body += [rect(24, 82, 220, 134)]
    body += [text(134, 122, "固定审查输入", 24, weight="600", anchor="middle")]
    body += [text(134, 159, "代码、背景、模型", 20, MUTED, anchor="middle")]
    body += [text(134, 191, "预算与验证条件", 20, MUTED, anchor="middle")]
    body += [rect(764, 82, 212, 134)]
    body += [text(870, 122, "记录结果", 24, weight="600", anchor="middle")]
    body += [text(870, 159, "召回与误报", 20, MUTED, anchor="middle")]
    body += [text(870, 191, "执行范围与总成本", 20, MUTED, anchor="middle")]
    body += [text(500, 332, "每组只改变一个因素，单独运行并核对结果", 19, MUTED, anchor="middle")]
    return canvas("评测中的变量控制", "\n".join(body), height=352)


def main():
    renderer = shutil.which("rsvg-convert")
    if renderer is None:
        raise SystemExit("Install librsvg to provide rsvg-convert, then run this script again.")
    SOURCE.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, draw in {
        "ai-code-review-workflow": workflow,
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
