"""Cutback. Import is offline and does not install instrumentation."""

__version__ = "0.1.0.dev1"


def init(**kwargs):
    """Explicit, optional instrumentation bootstrap; see cutback.instrumentation.init."""
    from cutback.instrumentation import init as initialize

    return initialize(**kwargs)
