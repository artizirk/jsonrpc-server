# Real Time JSON-RPC for Browser clients
(Over WebSockets With automatic service JSON schema generation)

* [x] Python 3.7+ and AsyncIO
* [x] Sans I/O [json-rpc](https://pypi.org/project/json-rpc/) and simple [WebSockets](https://pypi.org/project/websockets/)
* [ ] Ready for use?

# Inspiration
* [DBus interfaces](https://dbus.freedesktop.org/doc/dbus-specification.html#standard-interfaces)
* [dbus-next ServiceInterface](https://python-dbus-next.readthedocs.io/en/latest/high-level-service/index.html)
* [Brutusin-RPC server](https://github.com/brutusin/Brutusin-RPC)


# Example Service
```python
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
```
