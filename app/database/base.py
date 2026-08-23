"""
SQLAlchemy 2.0 Declarative Base

所有 ORM Model 均继承此类。
独立文件用于打破 Model ↔ Database 之间的循环导入。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 模型基类，提供统一的 metadata 注册入口"""
    pass
