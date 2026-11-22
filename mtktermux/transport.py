# SPDX-License-Identifier: GPL-3.0-or-later
"""Root-based USB transport for mtkclient on Termux.

Upstream mtkclient reaches MediaTek devices through libusb. Termux cannot claim
the vendor-specific interface of a BROM/Preloader device through Android's USB
permission model, which is the whole reason this port exists: with root we open
``/dev/bus/usb/<bus>/<dev>`` ourselves and drive the endpoint through USBFS
ioctls, bypassing both libusb and Android's permission prompts.

Status: **stub.** Device discovery, the root check, opening and closing the
usbfs node, and a device reset are implemented. Claiming an interface and the
bulk transfers that the BROM protocol is built on raise
``NotImplementedError`` — see ``docs`` in the roadmap section of the README.
"""

from __future__ import annotations

import dataclasses
import errno
import fcntl
import glob
import logging
import os
from typing import Iterator

log = logging.getLogger(__name__)

#: MediaTek USB vendor ID; bootROM and Preloader both use it.
MTK_VID = 0x0E8D

#: Where the kernel exposes USB device nodes on Android/Linux.
USBDEVFS_ROOT = "/dev/bus/usb"

#: sysfs view of the same devices, used to look up VID/PID.
SYSFS_USB_DEVICES = "/sys/bus/usb/devices"

#: Flipped to True once bulk transfers are implemented.
IMPLEMENTED = False

# ioctl encoding, asm-generic layout (arm64, x86_64).
_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2

# struct usbdevfs_bulktransfer: ep, len, timeout, data pointer (+ padding on LP64).
_BULKTRANSFER_SIZE = 24


def _ioc(direction: int, type_: str, nr: int, size: int) -> int:
    """Build an ioctl request number the way glibc's ``_IOC`` macro does."""
    return (direction << 30) | (size << 16) | (ord(type_) << 8) | nr


USBDEVFS_CLAIMINTERFACE = _ioc(_IOC_READ, "U", 15, 4)
USBDEVFS_RELEASEINTERFACE = _ioc(_IOC_READ, "U", 16, 4)
USBDEVFS_RESET = _ioc(_IOC_NONE, "U", 20, 0)
USBDEVFS_BULK = _ioc(_IOC_READ | _IOC_WRITE, "U", 2, _BULKTRANSFER_SIZE)


class TransportError(RuntimeError):
    """USB access failed in a way the caller can report and act on."""


@dataclasses.dataclass(frozen=True)
class UsbDevice:
    """One device node under ``/dev/bus/usb``."""

    path: str
    bus: int
    devnum: int
    vid: int | None = None
    pid: int | None = None

    def __str__(self) -> str:
        if self.vid is None:
            ids = "ids unknown"
        else:
            ids = f"{self.vid:04x}:{self.pid or 0:04x}"
        return f"{self.path} ({ids})"

    def describe(self) -> dict[str, object]:
        return {
            "path": self.path,
            "bus": self.bus,
            "devnum": self.devnum,
            "vid": None if self.vid is None else f"0x{self.vid:04x}",
            "pid": None if self.pid is None else f"0x{self.pid:04x}",
            "mediatek": self.vid == MTK_VID,
        }


def is_root() -> bool:
    """True when the current process can open raw USB device nodes."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def require_root() -> None:
    """Raise :class:`TransportError` unless running as root."""
    if not is_root():
        raise TransportError(
            "raw USB access needs root (euid is not 0). Start Termux as root "
            "or wrap the command in `su -c`."
        )


def _read_sysfs(path: str, name: str, base: int) -> int | None:
    try:
        with open(os.path.join(path, name), "r", encoding="ascii") as handle:
            return int(handle.read().strip(), base)
    except (OSError, ValueError):
        return None


def _sysfs_ids() -> dict[tuple[int, int], tuple[int | None, int | None]]:
    """Map ``(bus, devnum)`` to ``(vid, pid)`` from sysfs."""
    ids: dict[tuple[int, int], tuple[int | None, int | None]] = {}
    for entry in glob.glob(os.path.join(SYSFS_USB_DEVICES, "*")):
        bus = _read_sysfs(entry, "busnum", 10)
        devnum = _read_sysfs(entry, "devnum", 10)
        if bus is None or devnum is None:
            continue
        ids[(bus, devnum)] = (
            _read_sysfs(entry, "idVendor", 16),
            _read_sysfs(entry, "idProduct", 16),
        )
    return ids


def iter_usb_devices(root: str = USBDEVFS_ROOT) -> Iterator[UsbDevice]:
    """Yield every USB device node the kernel exposes, with ids where known."""
    ids = _sysfs_ids()
    for bus_dir in sorted(glob.glob(os.path.join(root, "[0-9]*"))):
        try:
            bus = int(os.path.basename(bus_dir))
        except ValueError:
            continue
        for node in sorted(glob.glob(os.path.join(bus_dir, "[0-9]*"))):
            name = os.path.basename(node)
            if not name.isdigit():
                continue
            devnum = int(name)
            vid, pid = ids.get((bus, devnum), (None, None))
            yield UsbDevice(path=node, bus=bus, devnum=devnum, vid=vid, pid=pid)


def find_mtk_devices(vid: int = MTK_VID, root: str = USBDEVFS_ROOT) -> list[UsbDevice]:
    """Return the devices whose vendor id is MediaTek's."""
    return [dev for dev in iter_usb_devices(root) if dev.vid == vid]


class RootUsbTransport:
    """Direct usbfs access to a MediaTek device, without libusb.

    ``open()`` is real. Everything past it needs the protocol layer.
    """

    def __init__(
        self,
        device: str | UsbDevice | None = None,
        timeout_ms: int = 1000,
        interface: int = 0,
    ) -> None:
        self.device = device if isinstance(device, UsbDevice) else None
        self.device_path = device if isinstance(device, str) else None
        self.timeout_ms = timeout_ms
        self.interface = interface
        self.endpoints: dict[str, int] = {}
        self._fd: int | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def fd(self) -> int | None:
        return self._fd

    def open(self) -> "RootUsbTransport":
        """Open the usbfs node. Requires root."""
        require_root()
        path = self.device_path or (self.device.path if self.device else None)
        if path is None:
            raise TransportError(
                "no device given; pass a /dev/bus/usb/BBB/DDD path or a UsbDevice"
            )
        if not os.path.exists(path):
            raise TransportError(f"{path} does not exist; is the target plugged in?")
        try:
            self._fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise TransportError(
                    f"permission denied opening {path}; raw USB access needs root"
                ) from exc
            raise TransportError(f"cannot open {path}: {exc.strerror}") from exc
        self.device_path = path
        log.debug("opened %s (fd %d)", path, self._fd)
        return self

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def __enter__(self) -> "RootUsbTransport":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def describe(self) -> dict[str, object]:
        """Diagnostics for ``--debugmode``."""
        return {
            "device": self.device_path,
            "fd": self._fd,
            "euid": os.geteuid() if hasattr(os, "geteuid") else None,
            "timeout_ms": self.timeout_ms,
            "interface": self.interface,
            "endpoints": dict(self.endpoints),
            "implemented": IMPLEMENTED,
        }

    # -- not implemented yet ----------------------------------------------

    def _require_fd(self) -> int:
        if self._fd is None:
            raise TransportError("transport is not open; call open() first")
        return self._fd

    def claim(self, interface: int | None = None) -> None:
        """Claim the vendor interface (USBDEVFS_CLAIMINTERFACE)."""
        self._require_fd()
        raise NotImplementedError(
            "claiming the BROM/Preloader interface is not implemented yet "
            "(USBDEVFS_CLAIMINTERFACE is defined but unused)"
        )

    def release(self, interface: int | None = None) -> None:
        """Release a previously claimed interface."""
        self._require_fd()
        raise NotImplementedError(
            "releasing the interface is not implemented yet "
            "(USBDEVFS_RELEASEINTERFACE is defined but unused)"
        )

    def bulk_write(self, data: bytes, endpoint: int = 0x01) -> int:
        """Send bytes to an OUT endpoint."""
        self._require_fd()
        raise NotImplementedError(
            "bulk transfers are not implemented yet; the BROM protocol needs "
            "USBDEVFS_BULK with endpoint discovery"
        )

    def bulk_read(self, size: int, endpoint: int = 0x81) -> bytes:
        """Receive bytes from an IN endpoint."""
        self._require_fd()
        raise NotImplementedError(
            "bulk transfers are not implemented yet; the BROM protocol needs "
            "USBDEVFS_BULK with endpoint discovery"
        )

    # -- implemented -------------------------------------------------------

    def reset(self) -> None:
        """Reset the device (USBDEVFS_RESET). Not exercised on hardware yet."""
        fd = self._require_fd()
        fcntl.ioctl(fd, USBDEVFS_RESET)


def open_transport(
    device: str | UsbDevice | None = None, timeout_ms: int = 1000
) -> RootUsbTransport:
    """Open a transport for ``device``, or for the only MediaTek device present."""
    if device is None:
        found = find_mtk_devices()
        if not found:
            raise TransportError(
                "no MediaTek device found on the USB bus. Put the target in "
                "BROM/Preloader mode and connect it over OTG, or pass --device."
            )
        if len(found) > 1:
            listed = ", ".join(str(dev) for dev in found)
            raise TransportError(
                f"multiple MediaTek devices found ({listed}); pass --device"
            )
        device = found[0]
    return RootUsbTransport(device=device, timeout_ms=timeout_ms).open()


__all__ = [
    "IMPLEMENTED",
    "MTK_VID",
    "RootUsbTransport",
    "TransportError",
    "UsbDevice",
    "find_mtk_devices",
    "is_root",
    "iter_usb_devices",
    "open_transport",
    "require_root",
]
