# SPDX-License-Identifier: GPL-3.0-or-later
"""Termux/Android backend pieces for the mtkclient port.

Only the rooted USB transport lives here for now. Upstream mtkclient owns the
protocol layer (DA handling, partitions, commands) and is meant to be vendored
or installed alongside this package, with its libusb transport replaced by
:class:`mtktermux.transport.RootUsbTransport`.
"""

from mtktermux.transport import (
    MTK_VID,
    RootUsbTransport,
    TransportError,
    UsbDevice,
    find_mtk_devices,
    is_root,
    iter_usb_devices,
    open_transport,
    require_root,
)

__all__ = [
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

__version__ = "0.1.0"
