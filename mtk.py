#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""mtk.py -- mtkclient on rooted Termux (scaffold).

The argument layout mirrors upstream bkerler/mtkclient's ``mtk.py``: a base
parser carrying the shared option groups, one subparser per command, subcommands
for the ``da`` family, and argparse-driven dispatch.

What works today: parsing, ``--help``, device discovery and opening the usbfs
node. What does not: everything that speaks the BROM/Preloader protocol. Those
commands report what they would do and exit with status 2.
"""

from __future__ import annotations

import argparse
import logging
import sys

from mtktermux.transport import (
    RootUsbTransport,
    TransportError,
    find_mtk_devices,
    open_transport,
)

__version__ = "0.1.0"

STATUS_OK = 0
STATUS_ERROR = 1
STATUS_NOT_IMPLEMENTED = 2

log = logging.getLogger("mtk")


# ---------------------------------------------------------------------------
# Shared option groups
# ---------------------------------------------------------------------------


def add_connection_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Connection")
    group.add_argument(
        "--preloader",
        metavar="FILE",
        help="Use a preloader image instead of the bootROM handshake",
    )
    group.add_argument(
        "--loader",
        metavar="FILE",
        help="Download Agent (.bin) to run on the target (needed for V6 chipsets)",
    )
    group.add_argument(
        "--device",
        metavar="NODE",
        help="Explicit usbfs node, e.g. /dev/bus/usb/001/002",
    )
    group.add_argument(
        "--timeout",
        type=int,
        default=1000,
        metavar="MS",
        help="Bulk transfer timeout in milliseconds (default: 1000)",
    )


def add_partition_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("GPT & partition")
    group.add_argument(
        "--parttype",
        help="Partition type (user/boot1/rpmb/lu0 etc.)",
    )
    group.add_argument(
        "--sectorsize",
        default="0x200",
        help="Default sector size (default: 0x200)",
    )
    group.add_argument(
        "--skip",
        metavar="NAMES",
        help="Comma-separated partitions to skip",
    )


def add_debug_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Debug")
    group.add_argument(
        "--debugmode",
        action="store_true",
        help="Verbose logging and transport diagnostics",
    )
    group.add_argument(
        "--verbose",
        action="store_true",
        help="Alias for --debugmode",
    )


def create_base_parser() -> argparse.ArgumentParser:
    """The parent parser every subcommand inherits, as upstream does."""
    base = argparse.ArgumentParser(add_help=False)
    add_connection_group(base)
    add_partition_group(base)
    add_debug_group(base)
    return base


# ---------------------------------------------------------------------------
# Command parsers
# ---------------------------------------------------------------------------


def _add_da_commands(da_parser: argparse.ArgumentParser, base: argparse.ArgumentParser) -> None:
    """``da``/``daa`` subcommands, limited to what this port documents."""
    da_subs = da_parser.add_subparsers(dest="da_command", metavar="<da-command>")

    seccfg = da_subs.add_parser(
        "seccfg", parents=[base], help="Read or change the seccfg lock state"
    )
    seccfg.add_argument(
        "mode",
        nargs="?",
        choices=("print", "lock", "unlock"),
        default="print",
        help="lock / unlock / print (default: print)",
    )

    vbmeta = da_subs.add_parser(
        "vbmeta", parents=[base], help="Patch the vbmeta partition"
    )
    vbmeta.add_argument(
        "vbmode",
        choices=("0", "1", "2", "3"),
        help="0=locked, 1=disable verity, 2=disable verification, 3=both",
    )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtk.py",
        description=(
            "mtkclient on a rooted Android host. Scaffold build: no command can "
            "talk to a device yet."
        ),
    )
    parser.add_argument("--version", action="version", version=f"mtk.py {__version__}")
    base = create_base_parser()
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    subparsers.add_parser("printgpt", parents=[base], help="Print the GPT table")

    read = subparsers.add_parser("r", parents=[base], help="Read partitions to files")
    read.add_argument("partitionname", help="Partition names (comma separated)")
    read.add_argument("filename", help="Output files (comma separated)")

    read_all = subparsers.add_parser(
        "rl", parents=[base], help="Read all partitions into a directory"
    )
    read_all.add_argument("directory", help="Output directory")

    read_flash = subparsers.add_parser(
        "rf", parents=[base], help="Dump the whole flash to a raw image"
    )
    read_flash.add_argument("filename", help="Output image")

    read_offset = subparsers.add_parser(
        "ro", parents=[base], help="Read a raw flash range"
    )
    read_offset.add_argument("offset", help="Start offset (hex or decimal)")
    read_offset.add_argument("length", help="Length in bytes")
    read_offset.add_argument("filename", help="Output file")

    write = subparsers.add_parser("w", parents=[base], help="Write partitions from files")
    write.add_argument("partitionname", help="Partition names (comma separated)")
    write.add_argument("filename", help="Input files (comma separated)")

    write_all = subparsers.add_parser(
        "wl", parents=[base], help="Write every image in a directory"
    )
    write_all.add_argument("directory", help="Directory of images")

    write_flash = subparsers.add_parser(
        "wf", parents=[base], help="Write a raw flash image"
    )
    write_flash.add_argument("filename", help="Input image")

    erase = subparsers.add_parser("e", parents=[base], help="Erase partitions")
    erase.add_argument("partitionname", nargs="+", help="Partition names")

    erase_sectors = subparsers.add_parser(
        "es", parents=[base], help="Erase a partition with a sector count"
    )
    erase_sectors.add_argument("partitionname", help="Partition name")
    erase_sectors.add_argument("sectors", help="Number of sectors to erase")

    da = subparsers.add_parser("da", parents=[base], help="Run a DA command")
    _add_da_commands(da, base)
    daa = subparsers.add_parser("daa", parents=[base], help="Run a DA command (alias)")
    _add_da_commands(daa, base)

    oem = subparsers.add_parser("oem", parents=[base], help="Lock or unlock the bootloader")
    oem.add_argument("mode", choices=("lock", "unlock"), help="lock or unlock")

    subparsers.add_parser("reset", parents=[base], help="Reboot the target")

    multi = subparsers.add_parser(
        "multi", parents=[base], help="Run several commands in one session"
    )
    multi.add_argument(
        "commands",
        help='Semicolon separated commands, e.g. "printgpt;r boot boot.img;reset"',
    )

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def describe_command(args: argparse.Namespace) -> str:
    """Human readable summary of what was asked for."""
    if args.command == "multi":
        steps = [step.strip() for step in args.commands.split(";") if step.strip()]
        return f"{len(steps)} chained operation(s): {'; '.join(steps)}"
    if args.command in ("da", "daa"):
        parts = [args.command, args.da_command or "<missing>"]
        if getattr(args, "mode", None):
            parts.append(args.mode)
        if getattr(args, "vbmode", None):
            parts.append(str(args.vbmode))
        return " ".join(parts)
    keys = ("partitionname", "filename", "directory", "offset", "length", "sectors", "mode")
    extra = [str(getattr(args, key)) for key in keys if getattr(args, key, None) is not None]
    return " ".join([args.command, *extra])


def _resolve_device(args: argparse.Namespace) -> str | None:
    if args.device:
        return args.device
    found = find_mtk_devices()
    if not found:
        raise TransportError(
            "no MediaTek device found on the USB bus. Put the target in "
            "BROM/Preloader mode and connect it over OTG, or pass --device "
            "/dev/bus/usb/BBB/DDD."
        )
    if len(found) > 1:
        listed = ", ".join(str(device) for device in found)
        raise TransportError(f"multiple MediaTek devices found ({listed}); pass --device")
    return found[0].path


def execute(args: argparse.Namespace) -> int:
    """Open the transport, then stop at the first unimplemented step."""
    log.info("requested: %s", describe_command(args))
    try:
        transport: RootUsbTransport = open_transport(
            device=_resolve_device(args), timeout_ms=args.timeout
        )
    except TransportError as exc:
        log.error("%s", exc)
        return STATUS_ERROR
    except NotImplementedError as exc:
        log.error("%s", exc)
        return STATUS_NOT_IMPLEMENTED

    with transport:
        if args.debugmode or args.verbose:
            for key, value in transport.describe().items():
                log.debug("  %s: %s", key, value)
        try:
            transport.claim(interface=0)
        except NotImplementedError as exc:
            log.error("%s", exc)
            log.error(
                "'%s' needs the BROM/Preloader protocol layer, which is not written "
                "yet. See the roadmap in README.md.",
                args.command,
            )
            return STATUS_NOT_IMPLEMENTED
    return STATUS_OK


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return STATUS_OK
    if args.command in ("da", "daa") and not getattr(args, "da_command", None):
        name = args.command
        parser.error(f"{name} needs a subcommand, e.g. `{name} seccfg unlock`")

    debug = args.debugmode or args.verbose
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    try:
        return execute(args)
    except KeyboardInterrupt:
        log.error("interrupted")
        return STATUS_ERROR


if __name__ == "__main__":
    sys.exit(main() or 0)
