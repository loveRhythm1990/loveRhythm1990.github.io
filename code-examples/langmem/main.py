"""LangMem 示例交互菜单。"""

import subprocess
import sys

FEATURES = {
    "1": ("features.01_hot_path", "Hot Path：agent 对话中主动记/查长期记忆"),
    "2": ("features.02_background_reflection", "Background：后台自动提取 + ReflectionExecutor 防抖"),
    "3": ("features.03_memory_manager", "Core API：create_memory_manager 三种记忆形态"),
    "4": ("features.04_prompt_optimizer", "Prompt 优化：metaprompt / gradient 两种策略"),
    "5": ("features.05_postgres_store", "外部存储：Postgres(pgvector) 持久化，需先起 docker 容器"),
}


def main():
    print("LangMem 示例（先配置 .env，详见 INDEX.md）\n")
    for k, (_, desc) in FEATURES.items():
        print(f"  {k}. {desc}")
    choice = input("\n选择编号: ").strip()
    if choice not in FEATURES:
        print("无效编号")
        sys.exit(1)
    module, _ = FEATURES[choice]
    args = [sys.executable, "-m", module]
    if choice == "5":
        mode = input("write 还是 read? [write]: ").strip() or "write"
        args.append(mode)
    subprocess.run(args)


if __name__ == "__main__":
    main()
