class BvcPriceCollectorError(Exception):
    """Base error for the BVC price collector."""


class BvcConfigError(BvcPriceCollectorError):
    """Raised when collector configuration is invalid."""


class BvcFetchError(BvcPriceCollectorError):
    """Raised when fetching a configured source URL fails."""

    def __init__(self, message: str, *, source_url: str, error_type: str = "fetch_error") -> None:
        super().__init__(message)
        self.source_url = source_url
        self.error_type = error_type

