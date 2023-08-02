"""ZeroMQ-based distributed in-memory connector implementation."""
from __future__ import annotations
from asyncio import streams
import types
import json
from typing import Type, Callable, Any, List, Mapping, Union, Optional
#from .typing import hg_addr_t, margo_instance_id, margo_request
#from .bulk import Bulk
#from .logging import Logger
import asyncio
import atexit
import logging
import multiprocessing
import signal
import socket
import sys
import time
import uuid
from types import TracebackType
from typing import Any
from typing import Sequence
from multiprocessing import Process

if sys.version_info >= (3, 11):  # pragma: >=3.11 cover
    from typing import Self
else:  # pragma: <3.11 cover
    from typing_extensions import Self

try:
    import zmq
    import zmq.asyncio

    zmq_import_error = None
except ImportError as e:  # pragma: no cover
    zmq_import_error = e

import proxystore.utils as utils
from proxystore.streaming import ProxyStream
from proxystore.connectors.dim.exceptions import ServerTimeoutError
from proxystore.connectors.dim.models import DIMKey
from proxystore.connectors.dim.models import RPC
from proxystore.connectors.dim.models import RPCResponse
from proxystore.connectors.dim.utils import get_ip_address
from proxystore.serialize import deserialize
from proxystore.serialize import serialize

MAX_CHUNK_LENGTH_DEFAULT = 64 * 1024

logger = logging.getLogger(__name__)


class ZeroMQConnector:
    """ZeroMQ-based distributed in-memory connector.

    Note:
        The first instance of this connector created on a process will
        spawn a [`ZeroMQServer`][proxystore.connectors.dim.zmq.ZeroMQServer]
        that will store data. Hence, this connector just acts as an interface
        to that server.

    Args:
        address: The network IP address to use. Takes precedence over
            `interface` if both are provided.
        interface: The network interface to use. `address` arg takes precedence
            if both are provided.
        port: The desired port for the spawned server.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
        timeout: Timeout in seconds to try connecting to local server before
            spawning one.

    Raises:
        ServerTimeoutError: If a local server cannot be connected to within
            `timeout` seconds, and a new local server does not response within
            `timeout` seconds after being started.
    """

    def __init__(
        self,
        port: int,
        address: str | None = None,
        interface: str | None = None,
        chunk_length: int | None = None,
        timeout: float = 1,
    ) -> None:
        # ZMQ is not a default dependency so we don't want to raise
        # an error unless the user actually tries to use this code
        if zmq_import_error is not None:  # pragma: no cover
            raise zmq_import_error

        self._address = address
        self._interface = interface
        self._client_id = str(uuid.uuid4())
        self.port = port
        self.chunk_length = (
            MAX_CHUNK_LENGTH_DEFAULT if chunk_length is None else chunk_length
        )
        self.timeout = timeout

        if self._address is not None:
            self.address = self._address
        elif self._interface is not None:  # pragma: darwin no cover
            self.address = get_ip_address(self._interface)
        else:
            host = socket.gethostname()
            self.address = socket.gethostbyname(host)

        self.url = f'tcp://{self.address}:{self.port}'
        print(self.url)

        self.server: multiprocessing.Process | None
        try:
            logger.info(
                f'Connecting to local server (url={self.url})...',
            )
            wait_for_server(self.address, self.port, self.timeout)
            logger.info(
                f'Connected to local server (url={self.url})',
            )
        except ServerTimeoutError:
            logger.info(
                'Failed to connect to local server '
                f'(address={self.url}, timeout={self.timeout})',
            )
            self.server = spawn_server(
                self.address,
                self.port,
                chunk_length=self.chunk_length,
                spawn_timeout=self.timeout,
            )
            logger.info(f'Spawned local server (url={self.url})')
        else:
            self.server = None

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _send_rpcs(self, rpcs: Sequence[RPC]) -> list[RPCResponse]:
        """Send an RPC request to the server.

        Args:
            rpcs: List of RPCs to invoke on local server.

        Returns:
            List of RPC responses.

        Raises:
            Exception: Any exception returned by the local server.
        """
        responses = []

        for rpc in rpcs:
            message = serialize(rpc)
            url = f'tcp://{rpc.key.peer_host}:{rpc.key.peer_port}'
            with self.socket.connect(url):
                self.socket.send_multipart(
                    list(utils.chunk_bytes(message, self.chunk_length)),
                )
                logger.debug(
                    f'Sent {rpc.operation.upper()} RPC (key={rpc.key})',
                )
                result = b''.join(self.socket.recv_multipart())

            response = deserialize(result)
            logger.debug(
                f'Received {rpc.operation.upper()} RPC response '
                f'(key={response.key}, '
                f'exception={response.exception is not None})',
            )

            if response.exception is not None:
                raise response.exception

            assert rpc.operation == response.operation
            assert rpc.key == response.key

            responses.append(response)

        return responses


    def close(self, kill_server: bool = True) -> None:
        """Close the connector.

        Args:
            kill_server: Whether to kill the server process. If this instance
                did not spawn the local node's server process, this is a
                no-op.
        """
        if kill_server and self.server is not None:
            self.server.terminate()
            self.server.join()
            logger.info(
                'Terminated local server on connector close '
                f'(pid={self.server.pid})',
            )

        self.socket.close()
        self.context.term()
        logger.info('Closed ZMQ connector')

    def config(self) -> dict[str, Any]:
        """Get the connector configuration.

        The configuration contains all the information needed to reconstruct
        the connector object.
        """
        return {
            'address': self._address,
            'interface': self._interface,
            'port': self.port,
            'chunk_length': self.chunk_length,
            'timeout': self.timeout,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ZeroMQConnector:
        """Create a new connector instance from a configuration.

        Args:
            config: Configuration returned by `#!python .config()`.
        """
        return cls(**config)

    def evict(self, key: DIMKey) -> None:
        """Evict the object associated with the key.

        Args:
            key: Key associated with object to evict.
        """
        rpc = RPC(operation='evict', key=key, client_id=self._client_id)
        self._send_rpcs([rpc])

    def exists(self, key: DIMKey) -> bool:
        """Check if an object associated with the key exists.

        Args:
            key: Key potentially associated with stored object.

        Returns:
            If an object associated with the key exists.
        """
        rpc = RPC(operation='exists', key=key)
        (response,) = self._send_rpcs([rpc])
        assert response.exists is not None
        return response.exists

    def get(self, key: DIMKey | None) -> bytes | None:
        """Get the serialized object associated with the key.

        Args:
            key: Key associated with the object to retrieve.

        Returns:
            Serialized object or `None` if the object does not exist.
        """
        rpc = RPC(operation='get', key=key, client_id=self._client_id)
        (result,) = self._send_rpcs([rpc])
        return result.data

    def get_batch(self, keys: Sequence[DIMKey]) -> list[bytes | None]:
        """Get a batch of serialized objects associated with the keys.

        Args:
            keys: Sequence of keys associated with objects to retrieve.

        Returns:
            List with same order as `keys` with the serialized objects or 
            `None` if the corresponding key does not have an associated object.
        """
        rpcs = [RPC(operation='get', key=key, client_id=self._client_id) for key in keys]
        responses = self._send_rpcs(rpcs)
        return [r.data for r in responses]
 

    def put(self, obj: bytes, key: DIMKey = None) -> DIMKey:
        """Put a serialized object in the store.

        Args:
            obj: Serialized object to put in the store.

        Returns:
            Key which can be used to retrieve the object.
        """
        if key is None:        
            data_key = DIMKey(
                dim_type='zmq',
                obj_id=str(uuid.uuid4()),
                size=len(obj),
                peer_host=self.address,
                peer_port=self.port,
                stream_id=None
            )
        else:
            data_key = DIMKey(
                dim_type='zmq',
                obj_id=str(uuid.uuid4()),
                size=len(obj),
                peer_host=self.address,
                peer_port=self.port,
                stream_id=key.stream_id
            )

        rpc = RPC(operation='put', key=data_key, data=obj)
        self._send_rpcs([rpc])
        return key

    def put_batch(self, objs: Sequence[bytes], key: DIMKey | None) -> list[DIMKey]:
        """Put a batch of serialized objects in the store.

        Args:
            objs: Sequence of serialized objects to put in the store.

        Returns:
            List of keys with the same order as `objs` which can be used to 
            retrieve the objects.
        """
        keys = [
            DIMKey(
                dim_type='zmq',
                obj_id=str(uuid.uuid4()),
                size=len(obj),
                peer_host=self.address,
                peer_port=self.port,
                stream_id=key.stream_id if key.stream_id is not None else None
            )
            for obj in objs
        ]
        rpcs = [
            RPC(operation='put', key=key, data=obj) for key in keys
            for key, obj in zip(keys, objs)
        ]
        self._send_rpcs(rpcs)
        return keys

    def create_stream(self) -> DIMKey:
        stream_id = str(uuid.uuid4())
        key = DIMKey(
            dim_type='zmq',
            obj_id= stream_id,
            size=0,
            stream_id = stream_id,
            peer_host=self.address,
            peer_port=self.port,
        )
        return key
    
    def close_stream(self, key: DIMKey) -> None:
        self.evict(key)
        #self.end_stream(self)


class ZeroMQServer:
    """ZeroMQServer implementation."""

    def __init__(self) -> None:
        self.data: dict[str, bytes | ProxyStream] = {}

    def evict(self, key: DIMKey) -> None:
        """Evict the object associated with the key.

        Args:
            key: Key associated with object to evict.
        """
        # check if stream
        if key.stream_id is not None:
            stream = self.data[key.stream_id]
            stream.end_stream()

            # clean out packets from data base
            for id in stream.proxy_uuids:
                self.data.pop(id, None)
            
        self.data.pop(key.obj_id, None)

    def exists(self, key: DIMKey) -> bool:
        """Check if an object associated with the key exists.

        Args:
            key: Key potentially associated with stored object.

        Returns:
            If an object associated with the key exists.
        """
        return key.obj_id in self.data

    def put(self, key: DIMKey, data:bytes) -> None:
        '''the put function appends new data '''
        if key.stream_id is not None:
            # if stream does not yet exist
            if key.stream_id not in self.data:
                stream = ProxyStream() # create stream
                self.data[key.stream_id] = stream # add stream to self.data
            else:
                stream = self.data[key.stream_id] # fetch existing stream

            stream.append(key.obj_id) # append obj_id of next packet
        self.data[key.obj_id] = data # store packet data in our store


    def get(self, key: DIMKey, host_id) -> bytes | None:
        '''The get function gives us the next data'''
        if key.stream_id is not None: # is a stream
            stream  = self.data[key.stream_id]
            stream.connect(host=host_id)
            proxy_uuid = stream.next_data(host_id)
            return self.data.get(proxy_uuid, None)
        else: # is not a stream
            return self.data.get(key.obj_id, None)

    def handle_rpc(self, rpc: RPC) -> RPCResponse:
        """Process an RPC request.

        Args:
            rpc: Client RPC to process.

        Returns:
            Response containing result or an exception if the operation failed.
        """
        response: RPCResponse
        try:
            if rpc.operation == 'exists':
                exists = self.exists(rpc.key)
                response = RPCResponse('exists', key=rpc.key, exists=exists)
            elif rpc.operation == 'evict':
                self.evict(rpc.key)
                response = RPCResponse('evict', key=rpc.key)
            elif rpc.operation == 'get':
                data = self.get(rpc.key, rpc.client_id)
                response = RPCResponse('get', key=rpc.key, data=data)
            elif rpc.operation == 'put':
                assert rpc.data is not None
                self.put(rpc.key, rpc.data)
                response = RPCResponse('put', key=rpc.key)
            else:
                raise AssertionError('Unreachable.')
        except Exception as e:
            response = RPCResponse(rpc.operation, key=rpc.key, exception=e)
        return response


async def run_server(
    address: str,
    port: int,
    chunk_length: int | None = None,
) -> None:
    """Listen and reply to RPCs from clients.

    Warning:
        This function does not return until SIGINT or SIGTERM is received.

    Args:
        address: IP address the server should bind to.
        port: Port the server should listen on.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
    """
    loop = asyncio.get_running_loop()
    close_future = loop.create_future()

    loop.add_signal_handler(signal.SIGINT, close_future.set_result, None)
    loop.add_signal_handler(signal.SIGTERM, close_future.set_result, None)

    server = ZeroMQServer()
    chunk_length = (
        MAX_CHUNK_LENGTH_DEFAULT if chunk_length is None else chunk_length
    )

    context = zmq.asyncio.Context()
    socket = context.socket(zmq.REP)
    socket.setsockopt(zmq.RCVTIMEO, 100)

    with socket.bind(f'tcp://{address}:{port}'):
        while not close_future.done():
            try:
                rpc_parts = await socket.recv_multipart()
            except zmq.error.Again:
                continue

            rpc_bytes = b''.join(rpc_parts)

            if rpc_bytes == b'ping':
                await socket.send(b'pong')
                continue

            rpc: RPC = deserialize(rpc_bytes)
            response = server.handle_rpc(rpc)

            message = serialize(response)
            await socket.send_multipart(
                list(utils.chunk_bytes(message, chunk_length)),
            )

    loop.remove_signal_handler(signal.SIGINT)
    loop.remove_signal_handler(signal.SIGTERM)

    socket.close()
    context.term()


def start_server(
    address: str,
    port: int,
    chunk_length: int | None = None,
) -> None:
    """Run a local server.

    Note:
        This function creates an event loop and executes
        [`run_server()`][proxystore.connectors.dim.zmq.run_server] within
        that loop.

    Args:
        address: IP address the server should bind to.
        port: Port the server should listen on.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
    """
    asyncio.run(run_server(address, port, chunk_length))


def spawn_server(
    address: str,
    port: int,
    *,
    chunk_length: int | None = None,
    spawn_timeout: float = 5.0,
    kill_timeout: float | None = 1.0,
) -> multiprocessing.Process:
    """Spawn a local server running in a separate process.

    Note:
        An `atexit` callback is registered which will terminate the spawned
        server process when the calling process exits.

    Args:
        address: IP address the server should bind to.
        port: Port the server will listen on.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
        spawn_timeout: Max time in seconds to wait for the server to start.
        kill_timeout: Max time in seconds to wait for the server to shutdown
            on exit.

    Returns:
        The process that the server is running in.
    """
    server_process = multiprocessing.Process(
        target=start_server,
        args=(address, port, chunk_length),
    )
    server_process.start()

    def _kill_on_exit() -> None:  # pragma: no cover
        server_process.terminate()
        server_process.join(timeout=kill_timeout)
        if server_process.is_alive():
            server_process.kill()
            server_process.join()
        logger.debug(
            'Server terminated on parent process exit '
            f'(pid={server_process.pid})',
        )

    atexit.register(_kill_on_exit)
    logger.debug('Registered server cleanup atexit callback')

    wait_for_server(address, port, timeout=spawn_timeout)
    logger.debug(
        f'Server started (host={address}, port={port}, pid={server_process.pid})',
    )

    return server_process


def wait_for_server(address: str, port: int, timeout: float = 0.1) -> None:
    """Wait until the server responds.

    Args:
        address: Host of the server to ping.
        port: Port of the server to ping.
        timeout: Max time in seconds to wait for server response.

    Raises:
        ServerTimeoutError: If the server does not respond within the timeout.
    """
    start = time.time()
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.LINGER, 0)
    socket.connect(f'tcp://{address}:{port}')
    socket.send(b'ping')

    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    while time.time() - start < timeout:
        # Poll for 100ms
        event = poller.poll(100)
        if len(event) != 0:
            response = socket.recv()
            assert response == b'pong'
            socket.close()
            return

    socket.close()

    raise ServerTimeoutError(
        f'Failed to connect to server within timeout ({timeout} seconds).',
    )

# def server():
#     context = zmq.Context()
#     socket = context.socket(zmq.REP)
#     socket.bind("tcp://*:5555")

#     stream = ProxyStream()
#     print(stream.is_end_of_stream())
#     socket.recv()

#     if stream.index < 1000:
#             data = stream.generate_data()
#             key = stream.connector.put(data)
#             stream.index += 1
#             stream.produce_data()
#     else:
#             stream.connector.end_stream()
#             print("Stream complete")

#     '''while not stream.is_end_of_stream():
#         data = stream.next_data()
#         print(data)
#         socket.send_string(data)
#         socket.recv()
#     '''
#     socket.send_string("END")

#     socket.close()
#     context.term()


# def client():
#     context = zmq.Context()
#     socket = context.socket(zmq.REQ)
#     socket.connect("tcp://localhost:5555")

#     socket.send_string("send now")
#     socket.close()
#     context.term()


# def consumer(stream):
#     data_iterator = iter(stream)
    
#     for data in data_iterator:
#         if data is None:
#             break
#         process_data(data)

# def process_data(data):
#     print("Processing data:", data)

# if __name__ == "__main__":
#     server_connector = ZeroMQConnector(port=5555)
#     Process(target=server, args=(server_connector,)).start()

#     client()

#     server_connector.close()
