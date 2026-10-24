"""Explicit bounded flush hooks; cloud runtime freeze behavior still needs staging validation."""

import functools
import inspect


def instrument_handler(observer, *, max_flush_seconds=0.1):
    if not 0 <= max_flush_seconds <= 0.3:
        raise ValueError("handler flush cap must be between 0 and 0.3 seconds")

    def decorate(handler):
        if inspect.iscoroutinefunction(handler):

            @functools.wraps(handler)
            async def async_handler(*args, **kwargs):
                import asyncio

                try:
                    return await handler(*args, **kwargs)
                finally:
                    # Worker export remains off-loop; bounded wait delegates to a thread.
                    try:
                        await asyncio.to_thread(observer.flush, max_flush_seconds)
                    except Exception:
                        pass

            return async_handler

        @functools.wraps(handler)
        def sync_handler(*args, **kwargs):
            try:
                return handler(*args, **kwargs)
            finally:
                try:
                    observer.flush(max_flush_seconds)
                except Exception:
                    pass

        return sync_handler

    return decorate
