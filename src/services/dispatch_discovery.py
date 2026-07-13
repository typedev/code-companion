"""LAN presence for local dispatch via zeroconf (mDNS/DNS-SD).

The desktop advertises ``_codecompanion._tcp`` while the broker runs; laptops
browse for it. The advertisement carries only *presence* — device id, friendly
name, broker port — never the session list (that is bearer-gated behind
``/sessions``).

``zeroconf`` ships compiled extensions, so it is a **distro dependency**
(``python3-zeroconf``), never vendored.
"""

from __future__ import annotations

import socket
from typing import Callable

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

SERVICE_TYPE = "_codecompanion._tcp.local."


def primary_ipv4() -> str:
    """Best-effort primary LAN IPv4 (the default-route source address)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just resolves the route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class DispatchAdvertiser:
    """Publish this machine's broker on the LAN while dispatch is enabled."""

    def __init__(self, port: int, device_id: str, device_name: str) -> None:
        self._port = port
        self._device_id = device_id
        self._device_name = device_name
        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None

    def start(self) -> None:
        if self._zc is not None:
            return
        ip = primary_ipv4()
        # device_id keeps the instance name unique regardless of friendly name.
        name = f"{self._device_id}.{SERVICE_TYPE}"
        self._info = ServiceInfo(
            SERVICE_TYPE,
            name,
            addresses=[socket.inet_aton(ip)],
            port=self._port,
            properties={
                "device_id": self._device_id,
                "name": self._device_name,
                "port": str(self._port),
            },
            server=f"{socket.gethostname()}.local.",
        )
        self._zc = Zeroconf()
        self._zc.register_service(self._info)

    def stop(self) -> None:
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        finally:
            self._zc.close()
            self._zc = None
            self._info = None


def _txt(info: ServiceInfo, key: str, default: str = "") -> str:
    raw = (info.properties or {}).get(key.encode())
    if raw is None:
        return default
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


class DispatchBrowser:
    """Watch the LAN for dispatch peers; call ``on_change`` on every update.

    ``on_change`` receives the full current peer list (list of dicts with
    ``device_id``/``name``/``host``/``port``). It runs on a zeroconf thread —
    callers marshal to the GTK main loop themselves.
    """

    def __init__(self, on_change: Callable[[list[dict]], None]) -> None:
        self._on_change = on_change
        self._peers: dict[str, dict] = {}
        self._zc: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None

    def start(self) -> None:
        if self._zc is not None:
            return
        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, _Listener(self))

    def stop(self) -> None:
        if self._browser is not None:
            self._browser.cancel()
            self._browser = None
        if self._zc is not None:
            self._zc.close()
            self._zc = None
        self._peers.clear()

    def peers(self) -> list[dict]:
        return list(self._peers.values())

    # called from the listener (zeroconf thread)
    def _upsert(self, name: str, info: ServiceInfo | None) -> None:
        if info is None:
            return
        addrs = info.parsed_addresses()
        device_id = _txt(info, "device_id") or name.split(".")[0]
        self._peers[name] = {
            "device_id": device_id,
            "name": _txt(info, "name") or device_id,
            "host": addrs[0] if addrs else None,
            "port": info.port,
        }
        self._on_change(self.peers())

    def _remove(self, name: str) -> None:
        if name in self._peers:
            del self._peers[name]
            self._on_change(self.peers())


class _Listener(ServiceListener):
    def __init__(self, browser: DispatchBrowser) -> None:
        self._browser = browser

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._browser._upsert(name, zc.get_service_info(type_, name))

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._browser._upsert(name, zc.get_service_info(type_, name))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._browser._remove(name)
