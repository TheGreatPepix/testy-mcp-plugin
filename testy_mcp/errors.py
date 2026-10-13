from typing import Any

FORBIDDEN_ORIGIN_ERROR = -31003
RATE_LIMIT_EXCEEDED_ERROR = -31029


class GatewayError(Exception):
    def __init__(self, message: str, *, status: int = 400, data: Any = None):
        super().__init__(message)
        self.status = status
        self.data = data

