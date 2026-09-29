"""功能 3：Core API —— create_memory_manager 纯函数式记忆提取。

不依赖任何存储：输入 (对话, existing 记忆)，输出更新后的记忆列表，存不存、
怎么存完全由你决定。演示三种记忆形态：
  a) Collection（语义记忆集合，三元组 schema，演示跨对话的更新与删除）
  b) Profile（用户档案，单一文档原地更新：enable_inserts=False）
  c) Episode（情景记忆，记录成功经验的完整推理链）

运行：python -m features.03_memory_manager
"""

from pydantic import BaseModel, Field
from langmem import create_memory_manager

from features.common import get_llm

llm = get_llm()


def show(title, memories):
    print(f"\n--- {title} ---")
    for m in memories:
        kind = type(m.content).__name__
        print(f"  [{kind}] {m.content}")


# ---------- a) Collection：三元组集合，演示增/改/删 ----------
class Triple(BaseModel):
    """Store all new facts, preferences, and relationships as triples."""

    subject: str
    predicate: str
    object: str
    context: str | None = None


manager = create_memory_manager(
    llm,
    schemas=[Triple],
    instructions="Extract user preferences and any other useful information",
    enable_inserts=True,
    enable_deletes=True,  # 允许删除过时记忆（默认 False）
)

conv1 = [{"role": "user", "content": "Alice manages the ML team and mentors Bob, who is also on the team."}]
memories = manager.invoke({"messages": conv1})
show("第 1 轮对话后", memories)

conv2 = [{"role": "user", "content": "Bob now leads the ML team and the NLP project."}]
memories = manager.invoke({"messages": conv2, "existing": memories})
show("第 2 轮对话后（注意 RemoveDoc 与新三元组）", memories)

conv3 = [{"role": "user", "content": "Alice left the company."}]
memories = manager.invoke({"messages": conv3, "existing": memories})
show("第 3 轮对话后（Alice 相关记忆被删除）", memories)


# ---------- b) Profile：单文档原地更新 ----------
class UserProfile(BaseModel):
    """Save the user's preferences."""

    name: str | None = None
    preferred_name: str | None = None
    response_style_preference: str | None = None
    special_skills: list[str] = []


profile_manager = create_memory_manager(
    llm,
    schemas=[UserProfile],
    instructions="Extract user preferences and settings",
    enable_inserts=False,  # 只更新既有文档，不新建
)

c1 = [{"role": "user", "content": "Hi! I'm Alex but please call me Lex. I'm a wizard at Python."}]
profile = profile_manager.invoke({"messages": c1})
show("Profile 第 1 轮", profile)

c2 = [{"role": "user", "content": "Keep it casual and witty. Also, I do competitive speedcubing!"}]
profile = profile_manager.invoke({"messages": c2, "existing": profile})
show("Profile 第 2 轮（同一文档被更新，不是新增）", profile)


# ---------- c) Episode：情景记忆 ----------
class Episode(BaseModel):
    """An episode captures how to handle a specific situation, including the
    reasoning process and what made it successful."""

    observation: str = Field(..., description="The situation and relevant context")
    thoughts: str = Field(..., description="Key considerations and reasoning process")
    action: str = Field(..., description="What was done in response")
    result: str = Field(..., description="What happened and why it worked")


episode_manager = create_memory_manager(
    llm,
    schemas=[Episode],
    instructions="Extract examples of successful interactions. Include the context, "
    "thought process, and why the approach worked.",
    enable_inserts=True,
)

conv = [
    {"role": "user", "content": "What's a binary tree? I work with family trees if that helps"},
    {
        "role": "assistant",
        "content": "A binary tree is like a family tree, but each parent has at most 2 children. "
        "Here's a simple example:\n   Bob\n  /  \\\nAmy  Carl\n",
    },
    {"role": "user", "content": "Oh that makes sense! So in a binary search tree, would it be like organizing a family by age?"},
]
episodes = episode_manager.invoke({"messages": conv})
show("Episode 提取", episodes)
