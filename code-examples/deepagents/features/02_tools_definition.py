"""
功能 2: 工具定义 - 如何创建和使用工具

展示如何定义工具供 Agent 使用。
"""

from langchain.tools import tool
from typing import List, Dict
import json


# 工具定义示例

@tool
def search_database(query: str) -> List[str]:
    """Search database for matching records"""
    data = {
        "user": ["User 001", "User 002", "User 003"],
        "product": ["Product A", "Product B", "Product C"],
    }
    return data.get(query.lower(), [])


@tool
def analyze_json(json_string: str) -> Dict:
    """Parse and analyze JSON string"""
    try:
        data = json.loads(json_string)
        return {"valid": True, "data": data, "keys": list(data.keys())}
    except json.JSONDecodeError as e:
        return {"valid": False, "error": str(e)}


@tool
def get_user_info(user_id: str) -> Dict:
    """Get user information by ID"""
    users = {
        "001": {"name": "Alice", "email": "alice@example.com"},
        "002": {"name": "Bob", "email": "bob@example.com"},
    }
    return users.get(user_id, {"error": f"User {user_id} not found"})


@tool
def calculate_statistics(numbers: List[float]) -> Dict:
    """Calculate statistics for a list of numbers"""
    if not numbers:
        return {"error": "Empty list"}
    return {
        "count": len(numbers),
        "sum": sum(numbers),
        "mean": sum(numbers) / len(numbers),
        "min": min(numbers),
        "max": max(numbers)
    }


def main():
    """Main function"""
    print("工具定义示例\n")

    # 演示工具调用
    print("1. 数据库搜索:")
    result = search_database.invoke({"query": "user"})
    print(f"   {result}\n")

    print("2. JSON 解析:")
    result = analyze_json.invoke({"json_string": '{"name":"Alice"}'})
    print(f"   {result}\n")

    print("3. 获取用户信息:")
    result = get_user_info.invoke({"user_id": "001"})
    print(f"   {result}\n")

    print("4. 统计计算:")
    result = calculate_statistics.invoke({"numbers": [10, 20, 30, 40, 50]})
    print(f"   {result}\n")


if __name__ == "__main__":
    main()
