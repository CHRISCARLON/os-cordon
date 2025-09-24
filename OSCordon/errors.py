"""
Custom exceptions for the OSCordon package.
"""


class OSCordon(Exception):
    """Base exception for all errors."""

    pass


class APIError(OSCordon):
    """Raised when an API request fails."""

    pass


class APIKeyError(APIError):
    """Raised when API key is missing or invalid."""

    pass


class RateLimitError(APIError):
    """Raised when API rate limit is exceeded."""

    pass


class DataNotFoundError(OSCordon):
    """Raised when requested data is not found."""

    pass


class USRNNotFoundError(DataNotFoundError):
    """Raised when a USRN is not found in the API."""

    def __init__(self, usrn: str):
        self.usrn = usrn
        super().__init__(f"USRN '{usrn}' not found in the API")


class InvalidGeometryError(OSCordon):
    """Raised when geometry data is invalid or unexpected."""

    def __init__(self, expected: str, got: str):
        self.expected = expected
        self.got = got
        super().__init__(f"Expected {expected} geometry, got {got}")


class BoundaryCreationError(OSCordon):
    """Raised when boundary creation fails."""

    pass


class InvalidParameterError(OSCordon):
    """Raised when invalid parameters are provided."""

    pass


class BufferConfigError(InvalidParameterError):
    """Raised when buffer configuration is invalid."""

    def __init__(self, param: str, value, allowed_values=None):
        self.param = param
        self.value = value
        self.allowed_values = allowed_values
        msg = f"Invalid buffer parameter '{param}': {value}"
        if allowed_values:
            msg += f". Allowed values: {allowed_values}"
        super().__init__(msg)


class CRSError(OSCordon):
    """Raised when there's an issue with Coordinate Reference System."""

    pass


class ConversionError(OSCordon):
    """Raised when data conversion fails."""

    pass


class RouteCreationError(OSCordon):
    """Raised when route creation fails."""

    def __init__(self, usrn_list: list, failed_usrns: list = []):
        self.usrn_list = usrn_list
        self.failed_usrns = failed_usrns or []
        msg = f"Failed to create route with {len(usrn_list)} USRNs"
        if failed_usrns:
            msg += f". Failed USRNs: {', '.join(failed_usrns)}"
        super().__init__(msg)


class EmptyDataError(DataNotFoundError):
    """Raised when expected data is empty."""

    def __init__(self, data_type: str):
        self.data_type = data_type
        super().__init__(f"No {data_type} found in the response")
