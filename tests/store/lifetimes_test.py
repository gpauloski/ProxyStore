from __future__ import annotations

from typing import Any

import pytest

from proxystore.connectors.local import LocalConnector
from proxystore.proxy import Proxy
from proxystore.store.base import Store
from proxystore.store.exceptions import ProxyStoreFactoryError
from proxystore.store.lifetimes import ContextLifetime
from proxystore.store.lifetimes import Lifetime


def test_context_lifetime_protocol(store: Store[LocalConnector]) -> None:
    lifetime = ContextLifetime(store)
    assert isinstance(lifetime, Lifetime)
    lifetime.close()


def test_context_lifetime_cleanup(store: Store[LocalConnector]) -> None:
    key1 = store.put('value1')
    key2 = store.put('value2')
    key3 = store.put('value3')
    key4 = store.put('value4')
    proxy1: Proxy[str] = store.proxy_from_key(key3)
    proxy2: Proxy[str] = store.proxy_from_key(key4)

    with ContextLifetime(store) as lifetime:
        assert not lifetime.done()

        lifetime.add_key(key1, key2)
        lifetime.add_proxy(proxy1, proxy2)

    assert lifetime.done()

    assert not store.exists(key1)
    assert not store.exists(key2)
    assert not store.exists(key3)
    assert not store.exists(key4)


def test_context_lifetime_add_bad_proxy(store: Store[LocalConnector]) -> None:
    proxy: Proxy[list[Any]] = Proxy(list)

    with ContextLifetime(store) as lifetime:
        with pytest.raises(ProxyStoreFactoryError):
            lifetime.add_proxy(proxy)
