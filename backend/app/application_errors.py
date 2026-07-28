class ApplicationError(Exception):
    """A user-visible application failure independent of the HTTP framework."""


class InvalidRequestError(ApplicationError):
    pass


class ResourceNotFoundError(ApplicationError):
    pass
