"""Stream producer and consumer interfaces.

Note:
    The [StreamProducer][proxystore.stream.interface.StreamProducer]
    and [StreamConsumer][proxystore.stream.interface.StreamConsumer]
    are re-exported in [`proxystore.stream`][proxystore.stream] for
    convenience.
"""
from __future__ import annotations

import logging
import sys
from types import TracebackType
from typing import Any
from typing import Generic
from typing import TypeVar

if sys.version_info >= (3, 11):  # pragma: >=3.11 cover
    from typing import Self
else:  # pragma: <3.11 cover
    from typing_extensions import Self

from proxystore.proxy import Proxy
from proxystore.store.base import Store
from proxystore.stream.events import event_to_json
from proxystore.stream.events import json_to_event
from proxystore.stream.events import NewObjectEvent
from proxystore.stream.protocols import Publisher
from proxystore.stream.protocols import Subscriber

logger = logging.getLogger(__name__)

T = TypeVar('T')


class StreamProducer(Generic[T]):
    """Proxy stream producer interface.

    Note:
        The [`StreamProducer`][proxystore.stream.interface.StreamProducer] can
        be used as a context manager.

        ```python
        with StreamProducer(...) as stream:
            for item in ...:
                stream.send(item)
        ```

    Note:
        The producer is only thread safe if the underlying
        [`Publisher`][proxystore.stream.protocols.Publisher] instance
        is thread safe.

    Tip:
        This class is generic, so it is recommended that the type of objects
        in the stream be annotated appropriately. This is useful for enabling
        a static type checker to validate that the correct object types are
        published to the stream.
        ```python
        producer = StreamProducer[str](...)
        # mypy will raise an error that StreamProducer.send() expects a str
        # but got a list[int].
        producer.send('default', [1, 2, 3])
        ```

    Args:
        store: [`Store`][proxystore.store.base.Store] instance used to store
            and communicate serialized objects in the stream.
        publisher: Object which implements the
            [`Publisher`][proxystore.stream.protocols.Publisher] protocol.
            Used to publish event messages when new objects are added to
            the stream.
    """

    def __init__(
        self,
        store: Store[Any],
        publisher: Publisher,
    ) -> None:
        self._store = store
        self._publisher = publisher

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self, *, store: bool = True, publisher: bool = True) -> None:
        """Close the producer.

        Warning:
            By default, this will also call `close()` on the
            [`Store`][proxystore.store.base.Store] and
            [`Publisher`][proxystore.stream.protocols.Publisher] interfaces.

        Args:
            store: Close the [`Store`][proxystore.store.base.Store] interface.
            publisher: Close the
                [`Publisher`][proxystore.stream.protocols.Publisher] interface.
        """
        if store:
            self._store.close()
        if publisher:
            self._publisher.close()

    def send(
        self,
        topic: str,
        obj: T,
        *,
        evict: bool = True,
    ) -> None:
        """Send an item to the stream.

        This method (1) puts the object in the
        [`Store`][proxystore.store.base.Store] to get back an identifier key,
        (2) creates a new event using the key and additional metadata, and
        (3) publishes the event to the stream via the
        [`Publisher`][proxystore.stream.protocols.Publisher].

        Warning:
            Careful consideration should be given to the setting of the
            `evict` flag. When set to `True`, the corresponding proxy
            yielded by the consumer of the stream will only be resolvable
            once. If you encounter unexpected
            [`ProxyResolveMissingKeyError`][proxystore.store.exceptions.ProxyResolveMissingKeyError]
            errors, it may be due to proxies from the stream being resolved
            multiple times but the first resolve triggered an eviction
            of the underlying data.

        Args:
            topic: Stream topic to send the object to.
            obj: Object to send via the stream.
            evict: Evict the object from the
                [`Store`][proxystore.store.base.Store] once the object is
                consumed by a
                [`StreamConsumer`][proxystore.stream.interface.StreamConsumer].
                Set to `False` if a single object in the stream will be
                consumed by multiple consumers. Note that when set to `False`,
                data eviction must be handled manually.
        """
        key = self._store.put(obj)
        event = NewObjectEvent.from_key(key, evict=evict)
        message = event_to_json(event).encode()
        self._publisher.send(topic, message)


class StreamConsumer(Generic[T]):
    """Proxy stream consumer interface.

    This interface acts as an iterator that will yield items from the stream
    until the stream is closed.

    Note:
        The [`StreamConsumer`][proxystore.stream.interface.StreamConsumer] can
        be used as a context manager.

        ```python
        with StreamConsumer(...) as stream:
            for item in stream:
                ...
        ```

    Tip:
        This class is generic, so it is recommended that the type of objects
        in the stream be annotated appropriately.
        ```python
        consumer = StreamConsumer[str](...)
        reveal_type(consumer.next())
        # Proxy[str]
        ```
        If the stream is heterogeneous or objects types are not known ahead
        of time, it may be appropriate to annotate the stream with
        [`Any`][typing.Any].
        ```python
        consumer = StreamConsumer[Any](...)
        reveal_type(consumer.next())
        # Proxy[Any]
        ```

    Warning:
        If you encounter unexpected
        [`ProxyResolveMissingKeyError`][proxystore.store.exceptions.ProxyResolveMissingKeyError]
        errors, it may be due to proxies from the stream being resolved
        multiple times but the first resolve triggered an eviction
        of the underlying data. If this is the case, confirm that the
        setting of the `evict` flag on
        [`StreamProducer.send()`][proxystore.stream.interface.StreamProducer.send]
        is set correctly and the there is not code incidentally resolving
        proxies before you expect.

    Note:
        The consumer is only thread safe if the underlying
        [`Subscriber`][proxystore.stream.protocols.Subscriber] instance
        is thread safe.

    Args:
        store: [`Store`][proxystore.store.base.Store] instance used to
            retrieve serialized objects in the stream.
        subscriber: Object which implements the
            [`Subscriber`][proxystore.stream.protocols.Subscriber] protocol.
            Used to listen for new event messages indicating new objects
            in the stream.
    """

    def __init__(
        self,
        store: Store[Any],
        subscriber: Subscriber,
    ) -> None:
        self._store = store
        self._subscriber = subscriber

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Proxy[T]:
        return self.next()

    def close(self, *, store: bool = True, subscriber: bool = True) -> None:
        """Close the consumer.

        Warning:
            By default, this will also call `close()` on the
            [`Store`][proxystore.store.base.Store] and
            [`Publisher`][proxystore.stream.protocols.Publisher] interfaces.

        Args:
            store: Close the [`Store`][proxystore.store.base.Store] interface.
            subscriber: Close the
                [`Subscriber`][proxystore.stream.protocols.Subscriber]
                interface.
        """
        if store:
            self._store.close()
        if subscriber:
            self._subscriber.close()

    def next(self) -> Proxy[T]:
        """Return a proxy of the next object in the stream.

        Raises:
            StopIteration: when the producer closes the stream.
        """
        message = next(self._subscriber)
        event = json_to_event(message.decode())
        if isinstance(event, NewObjectEvent):
            proxy: Proxy[T] = self._store.proxy_from_key(
                event.get_key(),
                evict=event.evict,
            )
            return proxy
        else:
            raise AssertionError('Unreachable.')
