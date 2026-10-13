"""Typed application errors shared by the Core and the HTTP API."""


class CadyxError(Exception):
    code = "CADYX_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    def as_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": str(self)}


class ValidationError(CadyxError):
    code = "VALIDATION_ERROR"
    http_status = 400


class NotFoundError(CadyxError):
    code = "NOT_FOUND"
    http_status = 404


class ConflictError(CadyxError):
    code = "CONFLICT"
    http_status = 409


class LeaseRequiredError(ConflictError):
    code = "EDIT_LOCK_REQUIRED"
