# SPDX-License-Identifier: GPL-3.0-or-later
"""The drive list and drive health, from udisks2 (the service the desktop already runs). No root, no Qt.

read() asks the service once through `busctl`; parse() turns the answer into Drive objects and is tested on
made-up answers. Nothing is mounted, unmounted or written here.
"""
import json
import os
import subprocess
from dataclasses import dataclass, field

_CALL = ["busctl", "--json=short", "call", "org.freedesktop.UDisks2", "/org/freedesktop/UDisks2",
         "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"]
_P = "org.freedesktop.UDisks2."
TEST_FOLDER = "Disky Business test files"
NTFS = {"ntfs", "ntfs3"}                     # file-system types udisks2 reports for Windows drives      # made in the home folder when the drive has no folder of its own


@dataclass
class Volume:
    device: str
    size: int
    fstype: str
    mounts: list
    read_only: bool = False

    @property
    def is_system(self):
        return "/" in self.mounts


@dataclass
class Health:
    state: str = "unknown"      # "ok", "failing", "unknown"
    note: str = "Health not available"
    temp_c: int | None = None
    hours: int | None = None

    def lines(self):
        out = [self.note]
        if self.temp_c is not None or self.hours is not None:
            bits = ([f"{self.temp_c} °C"] if self.temp_c is not None else []) + \
                   ([f"{self.hours:,} hours".replace(",", " ")] if self.hours is not None else [])
            out.append("   ".join(bits))
        return out


# Why a file system is often mounted read-only, and what helps. From the kernel's own message for HFS+:
# "write access to a journaled filesystem is not supported ... mounting read-only".
READ_ONLY_WHY = {
    "hfsplus": (" It is a Mac (HFS+) drive: Linux mounts these read-only when journaling is on, or when the drive "
                "was not ejected cleanly. Switch journaling off on a Mac, or copy the files off and format it as "
                "exFAT, which Mac, Windows and Linux can all write."),
}


FS_NAMES = {"vfat": "FAT", "exfat": "exFAT", "ntfs": "NTFS", "ntfs3": "NTFS", "fuseblk": "NTFS",
            "hfsplus": "Mac (HFS+)", "hfs": "Mac (HFS)", "apfs": "Mac (APFS)", "xfs": "XFS"}


def fs_name(fstype):
    """The file system's everyday name; the system's own name where there is no better one."""
    return FS_NAMES.get(fstype, fstype or "unknown")


def top_mounts(mounts):
    """Mount points, the top-level one first: a file system mounted again deep inside another folder tree (a
    bind mount) is somebody else's place, and comes last."""
    return sorted(mounts, key=lambda m: (m.rstrip("/").count("/"), len(m)))


def _mounted_ro(point):
    try:
        return bool(os.statvfs(point).f_flag & os.ST_RDONLY)
    except OSError:
        return False


@dataclass
class Drive:
    model: str
    size: int
    kind: str                   # "HDD", "SSD", "NVMe"
    usb: bool
    health: Health
    volumes: list = field(default_factory=list)
    path: str = ""              # the drive service's name for it, used to ask for SMART details
    smart: str = ""             # "ata", "nvme", or "" when the service cannot read SMART from it
    firmware: str = ""          # the drive's firmware version, as the service reports it
    device: str = ""            # the whole drive's name, like sdb (for smartctl through a USB case)
    other_use: str = ""         # "swap space" or "an unlocked encrypted volume" when a part of it is in such use

    @property
    def spinning(self):
        return self.kind == "HDD"

    @property
    def label(self):
        return f"{size_text(self.size)}  {self.kind_text()}\n{self.model}"

    def kind_text(self):
        if self.kind == "stick":
            return "USB stick"
        if self.kind == "card":
            return "Memory card"
        return ("USB " if self.usb else "") + self.kind

    @property
    def flash(self):
        """A USB stick or a memory card: they keep no SMART record."""
        return self.kind in ("stick", "card")

    def target(self, home=None, writable=None, mounted_ro=None):
        """Where a test of this drive would write: (folder, "") or (None, reason in plain words)."""
        home = os.path.realpath(home or os.path.expanduser("~"))
        writable = writable or (lambda p: os.access(p, os.W_OK))
        mounted_ro = mounted_ro or _mounted_ro
        mounted = [v for v in self.volumes if v.mounts]
        if not mounted:
            return None, "Not mounted. Press Mount, under the drive list, to use it."
        hands_off = [v for v in mounted if v.fstype in NTFS and not v.is_system]
        mounted = [v for v in mounted if v not in hands_off]     # never touched unless the user picks a folder
        if not mounted:
            return None, ("NTFS (Windows) drive: not touched automatically, because Linux may change a damaged "
                          "NTFS drive just by reading it. Use Other folder... only if Windows' disk check passed.")
        locked = [v for v in mounted if v.read_only or all(mounted_ro(m) for m in v.mounts)]
        for v in sorted(mounted, key=lambda v: -v.size):
            if v in locked:
                continue
            inside = [m for m in v.mounts if home == m or home.startswith(m.rstrip("/") + "/")]
            if inside or v.is_system:
                # The volume the system or the home folder lives on: only ever our own folder in the home
                # folder. Such a volume is often mounted in more places (a game library, a cache); none of
                # those is ours to write into.
                if inside and writable(home):
                    return os.path.join(home, TEST_FOLDER), ""
                continue
            for m in top_mounts(v.mounts):
                if writable(m):
                    return m, ""
        if len(locked) == len(mounted):
            why = next((READ_ONLY_WHY[v.fstype] for v in locked if v.fstype in READ_ONLY_WHY), "")
            return None, "Mounted read-only, so nothing can be written to it." + why
        return None, "No folder on it that you may write to. Use Other folder... to pick one."


def size_text(n):
    """Decimal units, as printed on the box."""
    for unit, step in (("TB", 10 ** 12), ("GB", 10 ** 9), ("MB", 10 ** 6)):
        if n >= step:
            v = n / step
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{n} bytes"


def _props(ifaces, name):
    return {k: v.get("data") for k, v in ifaces.get(_P + name, {}).items()}


def _celsius(kelvin):
    return round(kelvin - 273.15) if kelvin and kelvin > 0 else None


def _health(ifaces):
    ata, nvme = _props(ifaces, "Drive.Ata"), _props(ifaces, "NVMe.Controller")
    if ata and ata.get("SmartSupported") and ata.get("SmartEnabled") and ata.get("SmartUpdated"):
        failing = bool(ata.get("SmartFailing"))
        secs = ata.get("SmartPowerOnSeconds")
        return Health("failing" if failing else "ok", "Health: FAILING" if failing else "Health: OK",
                      _celsius(ata.get("SmartTemperature")), secs // 3600 if secs else None)
    if nvme and nvme.get("SmartUpdated"):
        warn = nvme.get("SmartCriticalWarning") or []
        return Health("failing" if warn else "ok", "Health: WARNING (" + ", ".join(warn) + ")" if warn else "Health: OK",
                      _celsius(nvme.get("SmartTemperature")), nvme.get("SmartPowerOnHours") or None)
    return Health()


# Media names the drive service gives flash sticks and memory cards (udisks2 "Media" / "MediaCompatibility").
_FLASH_MEDIA = {"thumb", "flash", "flash_cf", "flash_ms", "flash_sm", "flash_sd", "flash_sdhc", "flash_sdxc",
                "flash_sdio", "flash_sd_combo", "flash_mmc"}


def parse(answer):
    """busctl's JSON answer -> list of Drive, biggest first within: removable, then the rest."""
    objs = answer["data"][0] if isinstance(answer, dict) and answer.get("data") else {}
    drives = {}
    for path, ifaces in objs.items():
        d = _props(ifaces, "Drive")
        if "/drives/" not in path or not d or d.get("Optical"):
            continue                                # Size may be 0 for NVMe: taken from its whole-drive block below
        nvme = (_P + "NVMe.Controller") in ifaces
        rpm = d.get("RotationRate") or 0            # -1 = spinning, speed unknown; 0 = not spinning
        media = {d.get("Media") or ""} | set(d.get("MediaCompatibility") or [])
        # The service names the media, so sticks and cards need no guess from the rotation rate.
        flash = "stick" if "thumb" in media else "card" if media & _FLASH_MEDIA else ""
        kind = "NVMe" if nvme else flash or ("SSD" if rpm == 0 else "HDD")
        smart = "nvme" if nvme else "ata" if (_P + "Drive.Ata") in ifaces else ""
        drives[path] = Drive((d.get("Model") or "Drive").strip(), int(d.get("Size") or 0), kind,
                             d.get("ConnectionBus") == "usb", _health(ifaces), path=path, smart=smart,
                             firmware=(d.get("Revision") or "").strip())
    for path, ifaces in objs.items():
        b = _props(ifaces, "Block")
        if "/block_devices/" not in path or b.get("Drive") not in drives:
            continue
        if (_P + "Partition") not in ifaces:
            drives[b["Drive"]].device = path.rsplit("/", 1)[-1]
            if not drives[b["Drive"]].size:         # udisks gives NVMe drives Size 0; the namespace has it
                drives[b["Drive"]].size = int(b.get("Size") or 0)
        if _props(ifaces, "Swapspace").get("Active"):
            drives[b["Drive"]].other_use = "swap space"
        elif _props(ifaces, "Encrypted").get("CleartextDevice", "/") not in ("/", "", None):
            drives[b["Drive"]].other_use = "an unlocked encrypted volume"
    for path, ifaces in objs.items():
        b, fs = _props(ifaces, "Block"), ifaces.get(_P + "Filesystem")
        if "/block_devices/" not in path or fs is None or b.get("Drive") not in drives:
            continue
        mounts = [bytes(m).rstrip(b"\0").decode("utf-8", "replace") for m in (fs.get("MountPoints", {}).get("data") or [])]
        drives[b["Drive"]].volumes.append(Volume(path.rsplit("/", 1)[-1], int(b.get("Size") or 0),
                                                 b.get("IdType") or "", mounts, bool(b.get("ReadOnly"))))
    found = [d for d in drives.values() if d.size]  # no size anywhere = empty card reader: not a drive to show
    return sorted(found, key=lambda d: (not d.usb, -d.size, d.model))


def read(run=subprocess.run):
    """The machine's drives now. An empty list when the service or busctl is not there."""
    try:
        out = run(_CALL, capture_output=True, text=True, timeout=10)
        return parse(json.loads(out.stdout)) if out.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
