class AppError(Exception):
    """An actionable, sanitized error safe to show in the local UI."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
