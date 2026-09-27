#!/usr/bin/env python
"""
DeepAgents 示例代码选择器

这是一个交互式主程序,可以选择运行不同的功能演示
"""

import os
import sys
from pathlib import Path

# 添加 features 目录到路径
features_dir = Path(__file__).parent / "features"
sys.path.insert(0, str(features_dir))


def print_menu():
    """打印菜单"""
    print("\n" + "=" * 60)
    print("DeepAgents 功能演示")
    print("=" * 60)
    print("\n请选择要执行的功能: \n")

    features = [
        ("0", "完整诊断系统: 生产级参考代码", "00_diagnostic_agent"),
        ("1", "基础示例: 最小化 Agent", "01_basic_agent"),
        ("2", "工具定义: 如何创建和使用工具", "02_tools_definition"),
        ("3", "人机协同: 如何实现审批流程", "03_human_in_loop"),
        ("4", "权限管理: 文件系统隔离和权限控制", "04_permissions"),
        ("5", "上下文管理: 会话持久化和内存", "05_context_management"),
        ("6", "完整诊断系统: 多步工作流示例", "06_diagnostic_workflow"),
        ("q", "退出", None),
    ]

    for code, desc, _ in features:
        print(f"  {code}. {desc}")

    print("\n" + "=" * 60)
    return features


def run_feature(feature_module):
    """运行指定的功能模块"""
    try:
        # 动态导入模块
        module = __import__(feature_module)

        # 运行 main() 函数
        if hasattr(module, 'main'):
            module.main()
        else:
            print(f"❌ 错误: {feature_module} 中没有 main() 函数")

    except ImportError as e:
        print(f"❌ 错误: 无法导入 {feature_module}: {e}")
    except Exception as e:
        print(f"❌ 执行错误: {e}")
        import traceback
        traceback.print_exc()


def main():
    """主程序"""
    while True:
        features = print_menu()
        choice = input("请输入选择 (0-6, q 退出): ").strip().lower()

        # 查找对应的功能
        feature_module = None
        for code, desc, module in features:
            if code == choice:
                if module is None:
                    print("\n👋 退出程序")
                    return
                feature_module = module
                print(f"\n▶️  执行: {desc}\n")
                break

        if feature_module:
            run_feature(feature_module)
            input("\n\n按 Enter 返回菜单...")
        else:
            print("❌ 无效的选择,请重试")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 程序已中断")
        sys.exit(0)
