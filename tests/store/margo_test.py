"""MargoStore Unit Tests."""
from __future__ import annotations

import pytest

from proxystore.serialize import deserialize
from proxystore.serialize import serialize
from proxystore.store.dim.margo import MargoServer
from proxystore.store.dim.margo import MargoStore
from proxystore.store.dim.margo import when_finalize
from testing.mocker_modules.pymargo_mocker import Bulk
from testing.mocker_modules.pymargo_mocker import Engine
from testing.mocker_modules.pymargo_mocker import Handle
from testing.utils import open_port

ENCODING = 'UTF-8'


@pytest.fixture
def margo_server():
    """Margo server fixture."""
    e = Engine(f'tcp://127.0.0.1:{open_port()}')
    yield MargoServer(e)


def test_margo_store(margo_store) -> None:
    """Test MargoStore.

    All MargoStore functionality should be covered in
    tests/store/store_*_test.py.
    """
    store = MargoStore(margo_store.name, **margo_store.kwargs)
    store._start_server()
    store.close()
    store.close()  # check that nothing happens


def test_margo_server(margo_server) -> None:
    key = 'hello'
    val = serialize('world')
    size = len(val)

    bulk_str = Bulk(val)
    h = Handle()

    margo_server.set(h, bulk_str, size, key)
    assert margo_server.data[key] == bytearray(val)

    with pytest.raises(ValueError):
        margo_server.set(h, bulk_str, -1, key)

    local_buff = bytearray(size)
    bulk_str = Bulk(local_buff)
    margo_server.get(h, bulk_str, size, key)
    assert bulk_str.data == val

    local_buff = bytearray(size)
    bulk_str = Bulk(local_buff)
    assert deserialize(h.response).success
    margo_server.get(h, bulk_str, size, 'test')
    assert not deserialize(h.response).success

    local_buff = bytearray(1)
    bulk_str = Bulk(local_buff)
    margo_server.exists(h, bulk_str, size, key)
    assert deserialize(bytes(bulk_str.data))

    margo_server.exists(h, bulk_str, size, 'test')
    assert not deserialize(bytes(bulk_str.data))

    local_buff = bytearray(1)
    bulk_str = Bulk(local_buff)
    margo_server.evict(h, bulk_str, size, key)
    assert deserialize(h.response).success
    assert key not in margo_server.data

    local_buff = bytearray(1)
    bulk_str = Bulk(local_buff)
    margo_server.evict(h, bulk_str, size, 'test')
    assert deserialize(h.response).success


def test_finalize() -> None:
    when_finalize()
