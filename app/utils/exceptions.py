"""
应用基础异常

定义项目异常继承树的根节点。
所有业务异常均继承自 AppException，便于统一捕获和处理。

Phase 2 仅建立基础结构，不做过度设计。
后续 Phase 按需扩展具体异常类型（如 DocumentParseError、RetrievalError 等）。
"""


class AppException(Exception):
    """
    应用基础异常。

    所有业务层异常应继承此类，以便在中间件或全局异常处理器中统一捕获。

    Parameters
    ----------
    message : str
        面向用户的错误描述
    detail : str | None
        面向开发者的调试信息，可选
    """

    def __init__(self, message: str, detail: str | None = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)
