"""公共工具：LLM 与 Embeddings 配置。

LLM 走任意 OpenAI 兼容服务（在 .env 里配 LLM_API_BASE_URL / LLM_MODEL / LLM_API_KEY）。

Embeddings 说明：DeepSeek 等对话服务没有 embeddings 接口，为了让示例零依赖跑通，
这里实现了一个本地的 HashEmbeddings（词袋哈希 + L2 归一化），它对"词面重叠"的查询
能给出正确的相似度排序，足够演示语义检索的完整链路。
生产环境请换成真实的 embedding 模型，比如：

    from langchain_openai import OpenAIEmbeddings
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
"""

import hashlib
import math
import os
import re

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI

load_dotenv()

EMBEDDING_DIMS = 384


def get_llm(**kwargs) -> ChatOpenAI:
    """从 .env 读取配置，构造 ChatOpenAI（兼容任何 OpenAI 协议的服务）。"""
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_API_BASE_URL"),
        **kwargs,
    )


class HashEmbeddings(Embeddings):
    """本地词袋哈希 embedding，确定性强、无网络调用。

    原理：把文本分词后，每个词哈希到 dims 维向量中的一个桶并累加，最后 L2 归一化。
    相同词越多，余弦相似度越高。只能表达"词面重叠"，不理解真正的语义，
    仅用于演示 store 的向量检索机制。
    """

    def __init__(self, dims: int = EMBEDDING_DIMS):
        self.dims = dims

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()):
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            vec[h % self.dims] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def get_embeddings() -> HashEmbeddings:
    return HashEmbeddings()
