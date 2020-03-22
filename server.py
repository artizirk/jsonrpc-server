#!/usr/bin/env python3
import inspect
import os
import argparse
import asyncio
import datetime
import random
import signal as unix_signal
import jsonrpc
import websockets
from websockets.exceptions import ConnectionClosed
from http import HTTPStatus
from socket import AddressFamily
from functools import wraps, partial


MIME_TYPES = {
    "html": "text/html",
    "js": "text/javascript",
    "css": "text/css"
}

## https://gist.github.com/artizirk/04eb23d957d7916c01ca632bb27d5436
async def process_request(sever_root, path, request_headers):
    """Serves a file when doing a GET request with a valid path."""

    if "Upgrade" in request_headers:
        return  # Probably a WebSocket connection

    if path == '/':
        path = '/index.html'

    response_headers = [
        ('Server', 'asyncio websocket server'),
        ('Connection', 'close'),
    ]

    # Derive full system path
    full_path = os.path.realpath(os.path.join(sever_root, path[1:]))

    # Validate the path
    if os.path.commonpath((sever_root, full_path)) != sever_root or \
            not os.path.exists(full_path) or not os.path.isfile(full_path):
        print("HTTP GET {} 404 NOT FOUND".format(path))
        return HTTPStatus.NOT_FOUND, [], b'404 NOT FOUND'

    # Guess file content type
    extension = full_path.split(".")[-1]
    mime_type = MIME_TYPES.get(extension, "application/octet-stream")
    response_headers.append(('Content-Type', mime_type))

    # Read the whole file into memory and send it out
    body = open(full_path, 'rb').read()
    response_headers.append(('Content-Length', str(len(body))))
    print("HTTP GET {} 200 OK".format(path))
    return HTTPStatus.OK, response_headers, body


class _Method:
    def __init__(self, fn, name):
        self.name = name
        self.fn = fn


def method(name: str = None):
    if name is not None and type(name) is not str:
        raise TypeError('name must be a string')

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            return fn(*args, **kwargs)

        fn_name = name if name else fn.__name__
        wrapped.__dict__['__RPC_METHOD'] = _Method(fn, fn_name)

        return wrapped

    return decorator


class _Signal:
    def __init__(self, fn, name):
        self.name = name
        self.fn = fn


def signal(name: str = None):
    if name is not None and type(name) is not str:
        raise TypeError("name must be a string")

    def decorator(fn):
        fn_name = name if name else fn.__name__
        signal = _Signal(fn, fn_name)

        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            result = fn(self, *args, **kwargs)
            ServiceInterface._handle_signal(self, signal, result)
            return result

        wrapped.__dict__["__RPC_SIGNAL"] = signal

        return wrapped

    return decorator


class _Property(property):
    def __init__(self, fn, *args, **kwargs):
        if 'name' in kwargs:
            if kwargs['name'] is not None:
                self.name = kwargs['name']
            else:
                self.name = self.prop_getter.__name__
            del kwargs['name']
        self.__dict__['__RPC_PROPERTY'] = True
        super().__init__(fn, *args, **kwargs)

    def setter(self, fn, **kwargs):
        result = super().setter(fn, **kwargs)
        result.name = self.name
        return result


def rpc_property(name: str = None):
    if name is not None and type(name) is not str:
        raise TypeError('name must be a string')

    def decorator(fn):
        fn_name = name if name else fn.__name__
        return _Property(fn, name=fn_name)

    return decorator


class ServiceInterface:

    def __init__(self, name):
        self.name = name
        self.__methods = []
        self.__properties = []
        self.__properties_getters = set()  # used to removing duplicates, removed later
        self.__signals = []
        self.__bus = set()

        for name, member in inspect.getmembers(type(self)):
            member_dict = getattr(member, '__dict__', {})
            if '__RPC_METHOD' in member_dict:
                method = member_dict["__RPC_METHOD"]
                self.__methods.append(method)
            elif '__RPC_SIGNAL' in member_dict:
                method = member_dict["__RPC_SIGNAL"]
                self.__signals.append(method)
            elif '__RPC_PROPERTY' in member_dict:
                if member.fget in self.__properties_getters:
                    continue
                self.__properties_getters.add(member.fget)
                self.__properties.append(member)
        del self.__properties_getters

    def _get_methods(self):
        return self.__methods

    def _get_properties(self):
        return self.__properties

    def _add_bus(self, bus):
        self.__bus.add(bus)

    def _remove_bus(self, bus):
        self.__bus.remove(bus)

    def _handle_signal(self, signal, result):
        def resolve_task(bus, task: asyncio.Task):
            if task.exception():
                self.__bus.remove(bus)

        for bus in self.__bus:
            task = asyncio.create_task(
                bus.send('{"jsonrpc: "2.0", "method":"a.signal.bla", "params": ["hello"]}')
            )
            task.add_done_callback(partial(resolve_task, bus))


class TimeService(ServiceInterface):

    def __init__(self):
        super().__init__("ee.arti.TimeService")
        self._alarm_delay = 1

    @method()
    def time(self) -> str:
        return datetime.datetime.utcnow().isoformat() + 'Z'

    @method()
    def set_alarm(self):
        asyncio.get_running_loop().call_later(5, self.alarm_clock)

    @signal()
    def alarm_clock(self):
        print("ALARM")
        return "ALARM"

    @rpc_property()
    def alarm_delay(self):
        return self._alarm_delay

    @alarm_delay.setter
    def alarm_delay(self, value):
        self._alarm_delay = value


class RPC(ServiceInterface):
    def __init__(self):
        super().__init__("jrpc")
        self.dispatcher = jsonrpc.Dispatcher()
        self.interfaces = dict()
        self.bus = set()

        self.register_interface(self)

    def register_interface(self, interface: ServiceInterface):
        self.interfaces[interface.name] = interface
        for method in interface._get_methods():
            bound_method = partial(method.fn, interface)
            method_name = interface.name + '.' + method.name
            self.dispatcher.add_method(bound_method, method_name)

    @method("Property.Get")
    def get_prop(self, interface_name, property_name):
        return getattr(self.interfaces[interface_name], property_name)

    @method("Property.Set")
    def set_prop(self, interface_name, property_name, value):
        setattr(self.interfaces[interface_name], property_name, value)
        return None

    @method("Property.GetAll")
    def get_all_props(self, interface_name):
        all_props = dict()
        interface = self.interfaces[interface_name]
        for key in interface._get_properties():
            all_props[key] = getattr(interface, key)
        return all_props

    @signal("Properties.PropertiesChanged")
    def props_changed(self, interface_name):
        return

    @method("Introspectable.Introspect")
    def introspect(self):
        data = {"interfaces":{}}
        for name, interface in self.interfaces.items():
            data["interfaces"][name] = {"methods": {}, "signals": {}, "properties": {}}
        return data

    def _add_bus(self, ws):
        self.bus.add(ws)
        for interface in self.interfaces.values():
            if interface == self:
                super()._add_bus(ws)
            else:
                interface._add_bus(ws)

    def _remove_bus(self, ws):
        self.bus.remove(ws)
        for interface in self.interfaces.values():
            if interface == self:
                super()._add_bus(ws)
            else:
                interface._remove_bus(ws)


async def ws_handler(rpc: RPC, websocket: websockets.WebSocketServerProtocol, path: str):
    print("New WebSocket connection from", websocket.remote_address)

    rpc._add_bus(websocket)

    try:
        async for req in websocket:
            resp = jsonrpc.JSONRPCResponseManager.handle(req, rpc.dispatcher)
            if resp:
                print(resp.data)
                await websocket.send(resp.json)
    except ConnectionClosed as e:
        print("WebSocket connection closed for", websocket.remote_address, e.code, e.reason)

    rpc._remove_bus(websocket)



async def main(args):
    rpc = RPC()
    rpc.register_interface(TimeService())

    from pprint import pprint
    pprint(rpc.dispatcher.method_map)

    # set first argument for the handler to current working directory
    bound_http_handler = partial(process_request, args.htdocs)
    bound_ws_handler = partial(ws_handler, rpc)
    server = await websockets.serve(bound_ws_handler, args.host, args.port,
                                    compression=None,
                                    process_request=bound_http_handler,
                                    reuse_address=True,
                                    reuse_port=True)
    for socket in server.sockets:
        if socket.family == AddressFamily.AF_INET:  # IPv4
            host, port = socket.getsockname()
            print(f"Server listening on http://{host}:{port}")
        if socket.family == AddressFamily.AF_INET6:  # IPv6
            host, port = socket.getsockname()[:2]
            print(f"Server listening on http://[{host}]:{port}")

    # The stop condition is set when receiving SIGTERM.
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    loop.add_signal_handler(unix_signal.SIGTERM, stop.set_result, None)
    loop.add_signal_handler(unix_signal.SIGINT, stop.set_result, None)

    # wait for signals
    await stop

    print("Closing the server")
    server.close()
    print("Waiting for server to close")
    await server.wait_closed()
    print("Bye")


def build_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WebSocket RPC Server")
    parser.add_argument("--host", type=str, default="localhost",
                        help="bind to this host (default: localhost)")
    parser.add_argument("--port", type=int, default=8765,
                        help="bind to this port (default: 8765)")
    default_server_root = os.getcwd()
    parser.add_argument("--htdocs", type=str, default=default_server_root,
                        help=f"http root (default: {default_server_root})")
    return parser


if __name__ == "__main__":
    parser = build_argparse()
    args = parser.parse_args()
    asyncio.run(main(args))
