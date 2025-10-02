class XmlRequestError(Exception):
    """Base class for XML request processing errors."""


class ValidationError(XmlRequestError):
    """Raised when the incoming XML request does not meet structural expectations."""


class HydrationError(XmlRequestError):
    """Raised when reference hydration fails."""


class ServiceError(XmlRequestError):
    """Raised when the downstream evaluation service fails."""
