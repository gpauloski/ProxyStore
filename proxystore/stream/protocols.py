"""Protocols used by the stream interfaces.

## Publisher/Subscriber

The [`Publisher`][proxystore.stream.protocols.Publisher] and
[`Subscriber`][proxystore.stream.protocols.Subscriber] are
[`Protocols`][typing.Protocol] which define the publisher and subscriber
interfaces to a pub/sub-like messaging system.

In general, these protocols do not enforce any other implementation details
besides the interface. For example, implementations could choose to support
any producer-to-consumer configurations (e.g., 1:1, 1:N, N:N).
A set of shims implementing these protocols for third-party message brokers
are provided in [`proxystore.stream.shims`][proxystore.stream.shims].

## Plugins

Additional protocols, such as the
[`Filter`][proxystore.stream.protocols.Filter], are plugins used by the
[`StreamProducer`][proxystore.stream.interface.StreamProducer] and/or
[`StreamConsumer`][proxystore.stream.interface.StreamConsumer] that alter
their behavior.
"""

from __future__ import annotations

import sys
from typing import Any
from typing import Protocol
from typing import runtime_checkable
from typing import TypeVar
from typing import Union

if sys.version_info >= (3, 11):  # pragma: >=3.11 cover
    from typing import Self
else:  # pragma: <3.11 cover
    from typing_extensions import Self

from proxystream.stream.events import Event

T = TypeVar('T')

Publisher = Union['EventPublisher', 'MessagePublisher']
"""Publisher union type."""
Subscriber = Union['EventSubscriber', 'MessageSubscriber']
"""Subscriber union type."""


@runtime_checkable
class EventPublisher(Protocol):
    """Publisher interface to an event stream."""

    def close(self) -> None:
        """Close this publisher."""
        ...

    def send_event(self, topic: str, event: Event) -> None:
        """Publish event with optional data to the stream.

        Args:
            topic: Stream topic to publish message to.
            event: Event to publish.
        """
        ...


@runtime_checkable
class EventSubscriber(Protocol):
    """Subscriber interface to an event stream.

    The subscriber protocol is an iterable object which yields objects
    from the stream until the stream is closed.
    """

    def __iter__(self) -> Self: ...

    def __next__(self) -> bytes: ...

    def close(self) -> None:
        """Close this subscriber."""
        ...

    def next_event(self) -> tuple[Event]:
        """Get the next event."""
        ...


@runtime_checkable
class MessagePublisher(Protocol):
    """Publisher interface to message stream."""

    def close(self) -> None:
        """Close this publisher."""
        ...

    def send_message(self, topic: str, message: bytes) -> None:
        """Publish a message to the stream.

        Args:
            topic: Stream topic to publish message to.
            message: Message as bytes to publish to the stream.
        """
        ...


@runtime_checkable
class MessageSubscriber(Protocol):
    """Subscriber interface to message stream.

    The subscriber protocol is an iterable object which yields objects
    from the stream until the stream is closed.
    """

    def __iter__(self) -> Self: ...

    def __next__(self) -> bytes: ...

    def close(self) -> None:
        """Close this subscriber."""
        ...

    def next_message(self) -> bytes:
        """Get the next message."""
        ...


class Filter(Protocol):
    """Filter protocol.

    A filter takes as input the dictionary of metadata associated with a new
    object event and returns a boolean indicating if the event should be
    dropped. I.e., if the filter returns `True`, the event will be filtered
    out of the stream and lost.
    """

    def __call__(self, metadata: dict[str, Any]) -> bool:
        """Apply the filter to event metadata."""
        ...
