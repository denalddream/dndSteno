"""Exceptions shared across pipeline steps."""


class Cancelled(Exception):
    """Raised inside a long operation when its cancel event is set."""
