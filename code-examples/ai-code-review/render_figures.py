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


def routed_arrow(path, dashed=False):
    dash = ' stroke-dasharray="9 6"' if dashed else ""
    return (
        f'<path d="{path}" fill="none" stroke="{BLUE}" stroke-width="3"'
        f'{dash} marker-end="url(#arrow)"/>'
    )


def ocr_pipeline(mobile=False):
    body = []
    if mobile:
        body += [rect(16, 12, 448, 72, "#f8faff")]
        body += [text(240, 40, "1 文件选择 → 2 规则准备", 21, weight="600", anchor="middle")]
        body += [text(240, 69, "→ 3 文件分组 → 4 并发调度", 21, weight="600", anchor="middle")]
        body += [arrow(240, 90, 240, 110)]
        body += [rect(16, 118, 448, 378, "#f9fbff")]
        body += [text(240, 146, "每组独立上下文", 21, weight="600", anchor="middle")]
        body += [text(240, 176, "可选计划 → 本轮调查", 19, MUTED, anchor="middle")]
        body += [rect(40, 206, 180, 80), rect(270, 206, 170, 80)]
        body += [text(130, 237, "5 LLM 判断", 22, weight="600", anchor="middle")]
        body += [text(130, 267, "选择下一步动作", 18, MUTED, anchor="middle")]
        body += [text(355, 237, "执行工具", 22, weight="600", anchor="middle")]
        body += [text(355, 267, "搜索 · 读取 · 评论", 17, MUTED, anchor="middle")]
        body += [arrow(226, 246, 264, 246)]
        body += [routed_arrow("M355 286 V326 H130 V292")]
        body += [text(240, 316, "结果追加到上下文", 18, BLUE, anchor="middle")]
        body += [routed_arrow("M40 246 H26 V397 H34")]
        body += [text(240, 353, "task_done / 受限停止", 18, MUTED, anchor="middle")]
        body += [rect(40, 364, 400, 66)]
        body += [text(240, 392, "6 定位与过滤评论", 22, weight="600", anchor="middle")]
        body += [text(240, 418, "只处理候选评论，不执行复现", 18, MUTED, anchor="middle")]
        body += [routed_arrow("M440 397 H454 V185 H130 V200", True)]
        body += [text(240, 462, "有新发现且未达限额：下一轮", 19, BLUE, anchor="middle")]
        body += [text(240, 488, "虚线返回模型，带入此前确认的发现", 17, MUTED, anchor="middle")]
        body += [routed_arrow("M440 397 H472 V556 H446")]
        body += [rect(40, 526, 400, 60)]
        body += [text(240, 551, "汇总各组报告", 22, weight="600", anchor="middle")]
        body += [text(240, 576, "评论、失败、未审内容与停止原因", 17, MUTED, anchor="middle")]
        return canvas("OCR 的任务编排与两层反馈循环", "\n".join(body), 480, 604)

    body += [rect(20, 160, 1176, 286, "#f9fbff")]
    prep = [
        ("1 文件选择", "Git diff 与程序筛选"),
        ("2 规则准备", "加载配置，组内再匹配"),
        ("3 文件分组", "LLM 分组，程序兜底"),
        ("4 任务调度", "并发、预算、独立上下文"),
    ]
    for i, (title, detail) in enumerate(prep):
        x = 20 + i * 222
        body += [rect(x, 20, 190, 86, "#f8faff")]
        body += [text(x + 95, 53, title, 23, weight="600", anchor="middle")]
        body += [text(x + 95, 87, detail, 17, MUTED, anchor="middle")]
        if i < 3:
            body += [arrow(x + 198, 63, x + 214, 63)]
    body += [rect(936, 20, 240, 86, "#f3faf7", "#c6dfd4")]
    body += [text(1056, 53, "汇总报告", 23, weight="600", anchor="middle")]
    body += [text(1056, 87, "评论、失败与未审内容", 18, MUTED, anchor="middle")]
    body += [text(610, 185, "每组独立任务，可并发运行", 20, weight="600", anchor="middle")]
    body += [routed_arrow("M781 106 V139 H120 V224")]
    body += [rect(35, 230, 170, 100)]
    body += [text(120, 268, "可选计划", 24, weight="600", anchor="middle")]
    body += [text(120, 306, "仅生成调查计划", 18, MUTED, anchor="middle")]
    body += [rect(320, 230, 220, 100)]
    body += [text(430, 263, "5 LLM 调查", 24, weight="600", anchor="middle")]
    body += [text(430, 291, "代码、规则与已有发现", 18, MUTED, anchor="middle")]
    body += [text(430, 316, "决定下一步动作", 18, MUTED, anchor="middle")]
    body += [rect(610, 230, 240, 100)]
    body += [text(730, 263, "程序执行工具", 24, weight="600", anchor="middle")]
    body += [text(730, 291, "搜索 / 读取 / 提交评论", 18, MUTED, anchor="middle")]
    body += [text(730, 316, "返回结果，收集候选评论", 18, MUTED, anchor="middle")]
    body += [rect(920, 230, 250, 100)]
    body += [text(1045, 263, "6 评论处理", 24, weight="600", anchor="middle")]
    body += [text(1045, 291, "定位 / 条件重定位", 18, MUTED, anchor="middle")]
    body += [text(1045, 316, "依据 diff 过滤评论", 18, MUTED, anchor="middle")]
    body += [arrow(213, 280, 312, 280)]
    body += [arrow(548, 280, 602, 280)]
    body += [text(575, 270, "请求", 17, BLUE, anchor="middle")]
    body += [routed_arrow("M730 330 V368 H430 V336")]
    body += [text(580, 360, "结果追加；必要时压缩", 18, BLUE, anchor="middle")]
    body += [routed_arrow("M430 230 V212 H1045 V224")]
    body += [text(740, 206, "task_done / 受限停止", 18, MUTED, anchor="middle")]
    body += [routed_arrow("M1045 330 V424 H284 V280 H314", True)]
    body += [text(690, 416, "有新发现且未达限额：开始下一轮", 20, BLUE, anchor="middle")]
    body += [routed_arrow("M1170 280 H1204 V63 H1182")]
    body += [text(610, 477, "实线：同一会话的工具反馈；虚线：本轮过滤后的下一轮审查", 19, MUTED, anchor="middle")]
    return canvas("OCR 的任务编排与两层反馈循环", "\n".join(body), 1220, 496)


def graph_pipeline(mobile=False):
    body = []
    if mobile:
        body += [rect(16, 12, 448, 78, "#f8faff")]
        body += [text(240, 42, "程序建图：源码 → 解析 → SQLite", 21, weight="600", anchor="middle")]
        body += [text(240, 73, "文件变化后增量维护关系索引", 18, MUTED, anchor="middle")]
        steps = [
            ("1 识别改动", "宿主根据 diff 确定调查范围"),
            ("2 查询图索引", "关系、影响范围、相关测试与片段"),
            ("3 补读源码", "宿主按需打开关键文件"),
            ("4 判断行为", "宿主 LLM 结合契约与证据判断"),
        ]
        for i, (title, detail) in enumerate(steps):
            y = 124 + i * 78
            body += [rect(16, y, 416, 64)]
            body += [text(36, y + 27, title, 22, weight="600")]
            body += [text(36, y + 52, detail, 18, MUTED)]
            if i < 3:
                body += [arrow(224, y + 68, 224, y + 74)]
        body += [routed_arrow("M432 390 H456 V234 H438")]
        body += [text(240, 457, "证据不足：继续查询或补读", 20, BLUE, anchor="middle")]
        body += [text(240, 488, "图工具供宿主调用，最终判断由宿主完成", 18, MUTED, anchor="middle")]
        return canvas("代码图的索引维护与审查查询", "\n".join(body), 480, 508)

    body += [text(20, 25, "索引维护 · 程序执行", 21, weight="600")]
    index_nodes = [
        (20, 220, "源码文件", "支持语言与忽略配置"),
        (320, 260, "语法解析与关系解析", "定义、导入、引用与调用"),
        (660, 220, "SQLite 图索引", "保存节点和关系"),
        (960, 220, "文件变化", "按内容与依赖增量更新"),
    ]
    for x, width, title, detail in index_nodes:
        body += [rect(x, 42, width, 70, "#f8faff")]
        body += [text(x + width / 2, 71, title, 23, weight="600", anchor="middle")]
        body += [text(x + width / 2, 100, detail, 18, MUTED, anchor="middle")]
    body += [arrow(248, 77, 312, 77), arrow(588, 77, 652, 77)]
    body += [routed_arrow("M960 77 H920 V140 H450 V118")]
    body += [text(680, 134, "重算受影响关系", 18, BLUE, anchor="middle")]
    body += ['<path d="M770 112 V172 H455 V212" fill="none" stroke="#ffffff" stroke-width="9"/>']
    body += [routed_arrow("M770 112 V172 H455 V212")]
    body += [text(20, 195, "审查查询 · 宿主 agent 决定下一步", 21, weight="600")]
    nodes = [
        (20, 230, "1 识别改动", "diff 定位变更函数", "宿主确定调查范围"),
        (330, 250, "2 图检索", "调用方、依赖与相关测试", "可选向量搜索定位入口"),
        (660, 250, "3 补读源码", "宿主打开关键文件", "检查调用条件与断言"),
        (990, 190, "4 判断行为", "宿主 LLM 判断", "形成可核对的发现"),
    ]
    for x, width, title, detail1, detail2 in nodes:
        body += [rect(x, 218, width, 96)]
        body += [text(x + width / 2, 248, title, 24, weight="600", anchor="middle")]
        body += [text(x + width / 2, 279, detail1, 18, MUTED, anchor="middle")]
        body += [text(x + width / 2, 304, detail2, 18, MUTED, anchor="middle")]
    body += [arrow(258, 266, 322, 266), arrow(588, 266, 652, 266), arrow(918, 266, 982, 266)]
    body += [routed_arrow("M1085 314 V390 H455 V320")]
    body += [text(770, 381, "证据不足：再次查询 / 补读", 20, BLUE, anchor="middle")]
    body += [text(600, 436, "图关系帮助找代码，向量相似度帮助找入口；缺陷判断仍需源码和行为证据", 19, MUTED, anchor="middle")]
    return canvas("代码图的索引维护与审查查询", "\n".join(body), 1200, 458)


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
        "ai-code-review-graph-pipeline": graph_pipeline,
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
