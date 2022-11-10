"""UCX mocker implementation."""
from __future__ import annotations

from typing import Any

from proxystore.serialize import deserialize
from proxystore.serialize import serialize

data = {}


class MockEndpoint:
    """Mock Endpoint."""

    last_event: str
    key: str
    response: str
    req: Any
    server: Any
    is_closed: bool

    def __init__(self, server=False):
        """Initializes the MockEndpoint."""
        self.key = ''
        self.last_event = ''
        self.response = ''
        self.req = None
        self.server = server
        self.is_closed = False

    async def send_obj(self, req: Any) -> None:
        """Mocks the `ucp.send_obj` function.

        Args:
            req (Any): the object to communicate

        """
        self.req = None
        if self.server:
            self.req = req
            return self.req

        event = deserialize(req)

        if event['op'] == 'set':
            data[event['key']] = event['data']

        self.key = event['key']
        self.last_event = event['op']

    async def recv_obj(self) -> Any:
        """Mocks the `ucp.recv_obj` function."""
        from proxystore.store.dim.utils import Status

        if self.req is not None:
            return self.req

        if self.last_event == 'get':
            try:
                return data[self.key]
            except KeyError as e:
                return serialize(Status(success=False, error=e))
        elif self.last_event == 'exists':
            return serialize(self.key in data)
        elif self.last_event == 'evict':
            data.pop(self.key, None)
            return serialize(Status(success=True, error=None))
        return serialize(True)

    async def close(self) -> None:
        """Mock close implementation."""
        self.is_closed = True

    def closed(self) -> bool:
        """Mock closed implementation."""
        return self.is_closed


class Listener:
    """Mock listener implementation."""

    called: bool

    def __init__(self) -> None:
        """Mock listener init implementation."""
        self.called = False

    def closed(self) -> bool:
        """Mock closed."""
        if not self.called:
            self.called = True
            return False
        return True


def get_address(ifname: str) -> str:
    """Get address mock implementation."""
    return ifname


def create_listener(handler: Any, port: int) -> Any:
    """Create_listener mock implementation.

    Args:
        handler (Any): the communication handler
        port (int): the communication port

    """
    return Listener()


async def create_endpoint(host: str, port: int) -> MockEndpoint:
    """Create endpoint mock implementation."""
    return MockEndpoint()
