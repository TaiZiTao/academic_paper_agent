"""
API 接口层

薄层，只做 HTTP 协议转换。
"""

from app.api.router import router

__all__ = ["router"]
