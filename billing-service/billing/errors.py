"""Errors the API turns into 4xx responses. Anything else is a 500 and is logged with its stack trace."""


class BillingError(Exception):
    """A request the billing rules refuse."""

    status = 422
    code = "invalid"


class NotFound(BillingError):
    status = 404
    code = "not_found"


class Conflict(BillingError):
    """The request is valid but the policy's state doesn't allow it."""

    status = 409
    code = "conflict"
