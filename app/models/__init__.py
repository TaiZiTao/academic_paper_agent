"""
数据模型

导入所有 ORM Model 以注册到 Base.metadata。
每个新 Model 在此添加 import。
"""

from app.models.conversation import ConversationMessage  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.knowledge_base import KnowledgeBase  # noqa: F401
from app.models.paper import (  # noqa: F401
    Paper,
    PaperArtifact,
    PaperChunk,
    PaperFigure,
    PaperMessage,
    PaperPage,
    PaperSection,
    PaperTask,
)
from app.models.research import PaperImport  # noqa: F401
