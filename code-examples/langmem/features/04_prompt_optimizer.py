"""功能 4：Procedural Memory —— create_prompt_optimizer 优化系统提示词。

把"对话轨迹 + 反馈"喂给优化器，让它改写 system prompt。演示两种策略：
  - metaprompt：每一步反思是 1 次 LLM 调用，1~5 次调用
  - gradient：把"找问题"（think/critique）和"改 prompt"（recommend）拆成两步，
    每轮反思 2 次调用，2~10 次调用，最彻底也最贵

运行：python -m features.04_prompt_optimizer
"""

from langmem import create_prompt_optimizer

from features.common import get_llm

llm = get_llm()

# 一批带反馈的对话轨迹：助手总是给理论讲解，用户想要的是可运行的示例
trajectories = [
    (
        [
            {"role": "user", "content": "Explain inheritance in Python"},
            {"role": "assistant", "content": "Inheritance is a fundamental OOP concept where a class derives attributes and methods from another class. The theoretical basis lies in..."},
            {"role": "user", "content": "Show me a practical example instead"},
        ],
        {"user_score": 0},
    ),
    (
        [
            {"role": "user", "content": "How do I read a file in Python?"},
            {"role": "assistant", "content": "File I/O in Python is handled through the built-in io module, which abstracts stream operations..."},
            {"role": "user", "content": "Just give me the code"},
        ],
        {"user_score": 0},
    ),
]

prompt = "You are a helpful assistant."

for kind in ["metaprompt", "gradient"]:
    optimizer = create_prompt_optimizer(
        llm,
        kind=kind,
        config={"max_reflection_steps": 2, "min_reflection_steps": 1},
    )
    optimized = optimizer.invoke({"trajectories": trajectories, "prompt": prompt})
    print(f"\n===== kind={kind} 优化后的 prompt =====")
    print(optimized)
