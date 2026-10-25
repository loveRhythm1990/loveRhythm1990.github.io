# OCR 提示词原文

本目录保存 alibaba/open-code-review 的六组任务模板、Rust 语言规则和工具定义，
供文章附录展示。`prompts/` 中的正文、`task_template.json`、`tools.json`、
`rust.md` 与 `LICENSE` 保留来源文件原始内容；未在提示词中填入实际代码或配置。

来源：<https://github.com/alibaba/open-code-review>。
Copyright 2026 alibaba/open-code-review Contributors，Apache-2.0；许可证见 `LICENSE`。
准确来源与各文件 blob 标识记录在 `source.json`，所有文件取自同一源码修订。

`runtime_messages.json` 从 `internal/llmloop/loop.go` 的字符串常量提取，
将 Go 拼接字符串还原为实际补入会话的两条消息；这是展示形式的转换。
它们分别用于未返回工具调用时的提醒，以及预算耗尽后的最后一轮收尾。

这些文件是模板与调用配置，不是完整请求，也不是审查运行结果。实际消息由程序
填入 diff、规则、背景等内容，工具定义单独注册；并非每次运行都会调用全部模板。

在仓库根目录执行以下命令，从本地原文重新生成文章末尾的折叠附录：

```bash
python3 code-examples/ai-code-review/render_ocr_appendix.py
```

生成器对模板占位符做 Liquid 保护，并转义 HTML，仅改变展示方式。正文段落不由
该脚本生成。更新模板时，应同时核对 `task_template.json` 和对应调用实现。
