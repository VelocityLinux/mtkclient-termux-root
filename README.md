# mtkclient-termux-root

An unofficial, community port of [mtkclient](https://github.com/bkerler/mtkclient) that runs **on a rooted Android device inside Termux**, using that device's USB host (OTG) to talk to a MediaTek phone or tablet in BROM/Preloader mode.

In other words: root your daily driver, plug the target MediaTek device into it with an OTG cable, and read, write, erase and back up partitions without a PC.

> **Status:** early and experimental. This is a vibecoded port, not an official release. Expect rough edges, and treat every write as permanent.
>
> **Only the skeleton exists.** The repository now holds an entry point (`mtk.py`), a dependency list (`requirements.txt`) and a root USB transport stub (`mtktermux/transport.py`). Device discovery, the root check, and opening a `/dev/bus/usb/...` node are real; interface claiming and bulk transfers raise `NotImplementedError`, so **no command can talk to a device yet**. The install and usage sections below describe the intended interface.

---

## Roadmap

What has to exist before any of the commands below work:

1. **A working USB transport.** A Python module that opens `/dev/bus/usb/<bus>/<dev>` as root and speaks the BROM/Preloader protocol — the piece Termux normally cannot provide, and the reason this port exists at all. _Stubbed in `mtktermux/transport.py`: discovery, the root check and `open()` work, the transfers do not._
2. **The mtkclient core, imported rather than rewritten.** Upstream's DA handling and command layer should be reused where possible, with the desktop-only USB backend swapped for the Termux one.
3. **An entry point plus dependencies.** A `mtk.py` (or `mtk` wrapper) that runs inside Termux, and a pinned `requirements.txt` that installs cleanly against Termux's Python and `libusb`. _Scaffolded: `mtk.py` mirrors upstream's argparse layout (`create_base_parser`, per-command subparsers, `da`/`daa` subcommands) and `requirements.txt` pins the headless subset. Neither has been installed under Termux yet._
4. **A verified device path.** At least one real MediaTek device talking to a rooted Android host over OTG, with `printgpt` and a read-only partition dump confirmed before any write path is advertised.
5. **Write safety.** Read-back verification and a confirmation prompt for `w`, `wl`, `wf`, `e` and `es` before those are documented as usable.

Only after step 4 should the usage examples below be treated as tested rather than aspirational.

## Why root?

Upstream mtkclient assumes a desktop OS with a full libusb stack. Android's USB layer is not friendly to that: Termux cannot claim arbitrary USB interfaces without permission, and BROM/Preloader devices expose a vendor-specific interface that Android does not hand out.

Running as **root** changes that. With root, the tool can open the raw USB device nodes under `/dev/bus/usb/...` directly and drive the BROM protocol itself, without going through Android's USB permission model. That is the entire reason this port exists.

## What it can do

The command set below is the **intended** surface, following upstream mtkclient. No command in this table has been run on a rooted Termux host yet:

| Command | What it does |
| --- | --- |
| `printgpt` | Print the device's GPT partition table |
| `r <part> <file>` | Read one or more partitions to files |
| `rl <dir>` | Read **all** partitions to a directory |
| `rf <file>` | Dump the entire flash to a single raw image |
| `ro <offset> <length> <file>` | Read a flash range |
| `w <part> <file>` | Write one or more partitions from files |
| `wl <dir>` | Write every image in a directory to its matching partition |
| `wf <file>` | Write a full raw flash image |
| `e <part>` | Erase partitions |
| `es <part> <count>` | Erase a partition with a sector count |
| `da` / `daa` | Run DA (Download Agent) commands, e.g. `daa seccfg unlock` |
| `oem lock` / `oem unlock` | Lock or unlock the bootloader where supported |
| `reset` | Reboot the target device |

Most commands accept `--preloader`, `--loader`, `--parttype`, `--skip` and `--debugmode`. Run `--help` for the full list.

## Requirements

- A **rooted** Android device as the host, with Termux installed.
- An **OTG cable** (plus a powered hub for devices that draw more current than the host can supply).
- A MediaTek target device with an unlocked or exploitable bootROM.
- Python 3.8+ inside Termux.

Root is required for raw USB access, and the host's kernel must expose USB host mode. Some vendor kernels disable OTG or block `usbfs`; there is no software workaround for that.

## Installation — planned

> Planned: the dependency list is real and installs, but the tool cannot reach a device yet, so every command below stops at the same unimplemented transport step. This documents the intended setup.

```bash
pkg update && pkg upgrade
pkg install python git libusb clang
```

Clone the repo and install the Python dependencies:

```bash
git clone https://github.com/VelocityLinux/mtkclient-termux-root.git
cd mtkclient-termux-root
pip install -r requirements.txt
```

If the project has not been packaged yet, run it straight from the source tree:

```bash
python mtk.py --help
```

Grant the process raw USB access. Either run Termux as root (`su -c ...`) or launch the shell from a root session:

```bash
su
cd /data/data/com.termux/files/home/mtkclient-termux-root
python mtk.py printgpt
```

## Connecting the target — planned

1. Power the target device **off**, and unplug it from the OTG cable.
2. Start the command first, e.g. `python mtk.py printgpt` — it will wait for a device.
3. Hold **Volume Down + Volume Up** (or the BROM key combo for your model) and plug the OTG cable into the target.
4. Release the buttons when the terminal starts reporting the chipset and partition table.

If the target only exposes Preloader instead of BROM, pass a loader explicitly:

```bash
python mtk.py printgpt --preloader preloader.bin
python mtk.py printgpt --loader MTK_AllInOne_DA.bin
```

Some devices need `adb reboot edl` from the host to enter the right mode.

## Typical workflow — planned

> Planned, not yet verified on hardware. These examples describe how the tool is meant to be driven; see the roadmap for what still has to be built.

Back up first. Always.

```bash
# Confirm communication and dump the partition map
python mtk.py printgpt

# Back up the boot-critical partitions
python mtk.py r boot,vbmeta boot.img,vbmeta.img

# Full backup of every partition into ./backup
python mtk.py rl backup

# Read the active slot's boot image on an A/B device
python mtk.py r boot_a boot_a.img

# Patch with Magisk, then flash it back
python mtk.py w boot_a patched_boot.img

# Unlock the bootloader (device support varies)
python mtk.py e metadata,userdata,md_udc
python mtk.py daa seccfg unlock

# Erase userdata only (factory reset)
python mtk.py e userdata
```

Chain several operations in one session:

```bash
python mtk.py multi "printgpt;r boot boot.img;reset"
```

## Safety

- **Writing the wrong partition bricks the device.** Re-read `printgpt` output and confirm names before every write.
- Keep a full `rl` backup of a working device before changing anything.
- Do not interrupt a write or erase. Power loss mid-write can leave the device unbootable.
- Flashing a modified `vbmeta` usually requires disabling verification (`--skip vbmeta`, or flashing a patched image). A mismatched pair can bootloop.
- This tool is for devices **you own**. MediaTek BROM exploits are not authorization to touch hardware that is not yours.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `No device found` / hangs forever | Target not in BROM mode, bad cable, OTG not enabled, or the kernel blocks `usbfs` |
| `Permission denied` on `/dev/bus/usb/...` | Not running as root |
| Target disconnects immediately | Insufficient power — use a powered OTG hub |
| `DA error` / `SLA` / `DAA` failures | Device is fused or requires signed DA; no public bypass exists |
| Partition names differ from guides | Use the names from *your* `printgpt` output, not from another model |

## Credits

This port stands on the shoulders of:

- [bkerler/mtkclient](https://github.com/bkerler/mtkclient) — the original project and most of the protocol work
- xyzz (kamakiri exploit) and chimera (linecode exploit)
- Chaosmaster, Geert-Jan Kreileman (GUI) and all upstream contributors
- The various Termux/Android port authors whose notes made root-based USB access practical

## License

GPL-3.0-or-later, matching [upstream mtkclient](https://github.com/bkerler/mtkclient). This port is a derivative work of that project — it is meant to reuse its DA handling and command layer — so it cannot be licensed more permissively. Full text in [`LICENSE`](LICENSE).

## Disclaimer

Unofficial community project, provided as-is with no warranty. Flashing firmware can permanently damage a device. You are responsible for what you run against your hardware.
