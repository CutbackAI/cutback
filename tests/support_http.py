"""Owned loopback HTTP transport for independent in-process collector fixtures."""

import socket
import threading
import time
from contextlib import contextmanager

import uvicorn


@contextmanager
def owned_collector(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not instance.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert instance.started, "OWNED_COLLECTOR_DID_NOT_START"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "OWNED_COLLECTOR_DID_NOT_STOP"
