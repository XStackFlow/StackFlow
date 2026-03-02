"""Custom exceptions for the LangGraph Go test generator."""


class ConfigurationError(Exception):
    """Raised when there's a configuration error."""
    pass


class RepositoryError(Exception):
    """Raised when there's an error with the repository."""
    pass


class RetriableError(Exception):
    """Raised when an error occurs that can be retried."""
    pass
