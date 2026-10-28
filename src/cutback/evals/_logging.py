"""Suppress SDK payload logging only inside explicitly owned worker requests."""

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
_owned_request = ContextVar("cutback_owned_native_request_logging", default=False)
_namespaces = ("openai", "anthropic", "httpx", "httpx2", "httpcore", "httpcore2")


class _OwnedRequestFilter(logging.Filter):
    def filter(self, record):
        return not _owned_request.get()


_filter = _OwnedRequestFilter()


def _install():
    # The pinned clients log complete Request options in _base_client at DEBUG.
    # Source logger filters also work with application-installed root/capture handlers.
    names = {
        *_namespaces,
        "openai._base_client",
        "anthropic._base_client",
        *tuple(logging.root.manager.loggerDict),
    }
    for name in names:
        if any(name == prefix or name.startswith(prefix + ".") for prefix in _namespaces):
            logger = logging.getLogger(name)
            if _filter not in logger.filters:
                logger.addFilter(_filter)


def owned_sdk_request(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def execute(*args: P.args, **kwargs: P.kwargs) -> T:
        _install()
        token = _owned_request.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            _owned_request.reset(token)

    return execute


def owned_async_sdk_request(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def execute(*args: P.args, **kwargs: P.kwargs) -> T:
        _install()
        token = _owned_request.set(True)
        try:
            return await function(*args, **kwargs)
        finally:
            _owned_request.reset(token)

    return execute
