"""서비스 계층 오류 (웹 API에서는 HTTP 상태 코드로 변환)"""


class ServiceError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationFailed(ServiceError):
    status_code = 422
    code = "VALIDATION"


class AuthFailed(ServiceError):
    status_code = 401
    code = "LOGIN_FAILED"


class NotAllowed(ServiceError):
    status_code = 403
    code = "NOT_ALLOWED"


class NotFound(ServiceError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(ServiceError):
    status_code = 409
    code = "CONFLICT"


class RateLimited(ServiceError):
    status_code = 429
    code = "RATE_LIMITED"


class LimitExceeded(ServiceError):
    status_code = 429
    code = "LIMIT_EXCEEDED"
