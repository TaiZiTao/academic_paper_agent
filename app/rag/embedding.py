"""
Embedding 向量化抽象层

定义文本向量化接口，支持不同的 Embedding 服务。
所有实现必须继承 BaseEmbedding，确保 VectorStore 不依赖具体实现。

设计原则：
- VectorStore 依赖 BaseEmbedding 接口（依赖倒置）
- 替换为本地模型/BGE 只需新增一个子类，无需改动其他代码
"""

from abc import ABC, abstractmethod


class BaseEmbedding(ABC):
    """Embedding 抽象接口"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        ...

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """将单段文本转换为向量"""
        ...

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """将多段文本批量转换为向量"""
        ...


class OpenAIEmbedding(BaseEmbedding):
    """
    OpenAI Embedding API 实现

    使用 AsyncOpenAI 客户端，支持 text-embedding-3-small/large 等模型。
    """

    # 已知模型的向量维度（未知模型默认 1536）
    _MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        "text-embedding-v4": 1024,
    }

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    @property
    def dimension(self) -> int:
        return self._MODEL_DIMENSIONS.get(self.model, 1536)

    async def embed_text(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from openai import AsyncOpenAI

        if not self.api_key:
            raise ValueError(
                "OpenAI API key is required. Set EMBEDDING_API_KEY in .env"
            )

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

        # 分批——阿里云/DashScope 限 20 条/次，OpenAI 限 2048
        batch_size = 10
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = await client.embeddings.create(
                model=self.model,
                input=batch,
            )
            all_embeddings.extend(item.embedding for item in response.data)

        return all_embeddings
