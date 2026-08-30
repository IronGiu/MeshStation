##############################
### MeshStation By IronGiu ###
##############################
# Please respect the GNU General Public v3.0 license terms and conditions.
import sys
import os
import time
import argparse
import base64
import socket
import zmq
import json
import asyncio
import threading
import html
import asyncio
import locale
import re
from datetime import datetime
from collections import deque
import platform
import subprocess
import multiprocessing
import atexit
import secrets
import urllib.request
import urllib.error
import math
import signal
import tkinter as tk
from tkinter import scrolledtext, Canvas
import gc

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from meshtastic import mesh_pb2, admin_pb2, telemetry_pb2, config_pb2

from nicegui import ui, app, core
from fastapi import Request

# --- CONSTANTS ---
PROGRAM_NAME = "MeshStation"
PROGRAM_SHORT_DESC = "Meshtastic SDR Analyzer & Desktop GUI"
AUTHOR = "IronGiu"
VERSION = "1.1.1"
LICENSE = "GNU General Public License v3.0"
GITHUB_URL = "https://github.com/IronGiu/MeshStation"
DONATION_URL = "https://ko-fi.com/irongiu"
SUPPORTERS_URL = "https://github.com/IronGiu/MeshStation/blob/main/SUPPORTERS.md"
GITHUB_RELEASES_URL = f"{GITHUB_URL}/releases"
LANG_FILE_NAME = "languages.json"
# --- CLI argument parsing ---
def _parse_args():
    parser = argparse.ArgumentParser(
        prog="MeshStation",
        description=f"{PROGRAM_NAME} — {PROGRAM_SHORT_DESC}",
        add_help=False  # manage --help manually to print and exit without starting anything
    )
    parser.add_argument(
        "--help", action="store_true",
        help="Show this help message and exit"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug mode (verbose logging)"
    )
    parser.add_argument(
        "--nogpu", action="store_true",
        help=(
            "Force software rendering: disable GPU/hardware acceleration. "
            "Useful on systems without a compatible GPU, inside VMs, or when "
            "hardware rendering causes crashes. "
            "Can also be enabled via the environment variable MESHSTATION_NOGPU=1."
        )
    )
    args, _ = parser.parse_known_args()
    if args.help:
        parser.print_help()
        sys.exit(0)
    return args

_cli_args = _parse_args()
DEBUGGING = _cli_args.debug
SHOW_DEV_TOOLS = DEBUGGING
SHUTDOWN_TOKEN = secrets.token_urlsafe(24)
MAIN_LOOP = None

# Chat Constants
_CHAT_DOM_WINDOW = 30      # messages visible in the DOM
_CHAT_LOAD_STEP  = 20      # how many are loaded by pressing "Load more"

# --nogpu: honours both the CLI flag and the env variable MESHSTATION_NOGPU=1.
# Resolved once at startup so child processes inherit the env vars we set below.
_NOGPU_REQUESTED = _cli_args.nogpu or (
    os.environ.get("MESHSTATION_NOGPU", "0").strip() not in ("", "0", "false", "no", "False")
)

if _NOGPU_REQUESTED:
    # Use setdefault so we NEVER overwrite vars the user/system already set.
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    os.environ.setdefault("MESA_GL_VERSION_OVERRIDE", "3.3")
    os.environ.setdefault("MESA_GLSL_VERSION_OVERRIDE", "330")
    os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")
    os.environ.setdefault("QSG_RENDER_LOOP", "basic")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    _existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    _nogpu_flags = "--disable-gpu --disable-gpu-rasterization --disable-gpu-compositing --no-sandbox"
    if "--disable-gpu" not in _existing:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (_existing + " " + _nogpu_flags).strip()

def _attach_windows_console():
    """
    Fix console attachment on Windows, ONLY when at least one CLI flag is present.
    """
    if os.name != "nt":
        return
    if not getattr(sys, "frozen", False):
        return
    import ctypes as ctconsole
    kernel32 = ctconsole.windll.kernel32
    # Try to attach to parent console first (cmd/powershell)
    if not kernel32.AttachConsole(-1):
        # No parent console (or attach failed): allocate a new one
        kernel32.AllocConsole()
    # Either way, reopen standard streams on the console
    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    sys.stdin  = open("CONIN$",  "r", encoding="utf-8", errors="replace")
    print("", flush=True)

# Attach console early if any CLI flag that produces output is active
if os.name == "nt" and any(a in sys.argv for a in ("--help", "--debug")):
    _attach_windows_console()

gc.enable()
# Optimized thresholds: less frequent cleaning to avoid interrupting the SDR stream
gc.set_threshold(1000, 15, 15)

def _patch_nicegui_gc_safety():
    """Patch for known nicegui + garbage collector issues, Prevents C-level crash 0x80000003 on Python 3.10 + Windows"""
    try:
        import nicegui.helpers as _nh
        _original_expects = _nh.expects_arguments

        def _safe_expects_arguments(func):
            try:
                # Let's call the original function; if the object is being destroyed,
                # inspect.signature would fail here.
                return _original_expects(func)
            except Exception:
                # In case of error (dying object), we assume that it does not want arguments
                return False

        _nh.expects_arguments = _safe_expects_arguments
        # We also apply the patch to the events module for security
        import nicegui.events as _ne
        _ne.expects_arguments = _safe_expects_arguments
        
        if DEBUGGING: print("DEBUG: GC safety patch applied safely", flush=True)
    except:
        pass

_patch_nicegui_gc_safety()

def _debug_thread_watchdog():
    import time
    while True:
        time.sleep(180)
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        print(f"[{now}] DEBUG: main thread alive", flush=True)
if DEBUGGING:
    threading.Thread(target=_debug_thread_watchdog, daemon=True).start()

def _parse_version_tuple(v: str) -> tuple[int, int, int]:
    s = (v or "").strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:]
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", s)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0))

def _is_newer_version(current: str, latest: str) -> bool:
    cur = _parse_version_tuple(current)
    lat = _parse_version_tuple(latest)
    if cur == (0, 0, 0) or lat == (0, 0, 0):
        return (latest or "").strip() != (current or "").strip()
    return lat > cur

def _github_repo_slug() -> str | None:
    s = (GITHUB_URL or "").strip()
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)", s, flags=re.IGNORECASE)
    if not m:
        if DEBUGGING:
            print(f"Invalid GITHUB_URL: {s}")
        return None
    owner = m.group(1)
    repo = m.group(2).rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if DEBUGGING:
        print(f"Repo slug: {owner}/{repo}")
    return f"{owner}/{repo}"

def _fetch_latest_github_release(timeout_sec: float = 10.0) -> dict | None:
    slug = _github_repo_slug()
    if not slug:
        if DEBUGGING:
            print(f"Invalid repo slug: {slug}")
        return None
    api_url = f"https://api.github.com/repos/{slug}/releases/latest"
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{PROGRAM_NAME}/{VERSION}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        tag = (data.get("tag_name") or "").strip()
        url = (data.get("html_url") or "").strip() or GITHUB_RELEASES_URL
        if not tag:
            if DEBUGGING:
                print(f"Invalid tag_name: {tag}")
            return None
        return {"tag": tag, "url": url}
    except Exception as e:
        if DEBUGGING:
            print(f"Error fetching latest release: {e}")
        return None

# --- MeshCore Constants ---
MESHCORE_PRESETS = {
    "USA_CANADA": {
        "RECOMMENDED": {"freq": 910.525, "sf": 7,  "bw": 62.5, "cr": 5, "sync": 0x12, "preamble": 16, "description_key": "meshcore.preset.recommended"},
        "LEGACY_WIDE": {"freq": 915.8,   "sf": 10, "bw": 250.0, "cr": 5, "sync": 0x12, "preamble": 16, "description_key": "meshcore.preset.legacy_wide"},
    },
}

PAYLOAD_TYPE_REQ         = 0x00
PAYLOAD_TYPE_RESPONSE    = 0x01
PAYLOAD_TYPE_TXT_MSG     = 0x02
PAYLOAD_TYPE_ACK         = 0x03
PAYLOAD_TYPE_ADVERT      = 0x04
PAYLOAD_TYPE_GRP_TXT     = 0x05
PAYLOAD_TYPE_GRP_DATA    = 0x06
PAYLOAD_TYPE_ANON_REQ    = 0x07
PAYLOAD_TYPE_PATH        = 0x08
PAYLOAD_TYPE_TRACE       = 0x09
PAYLOAD_TYPE_MULTIPART   = 0x0A
PAYLOAD_TYPE_CONTROL     = 0x0B
PAYLOAD_TYPE_RAW_CUSTOM  = 0x0F

ROUTE_TYPE_TRANSPORT_FLOOD  = 0x00
ROUTE_TYPE_FLOOD            = 0x01
ROUTE_TYPE_DIRECT           = 0x02
ROUTE_TYPE_TRANSPORT_DIRECT = 0x03

# --- Meshtastic Region Definitions ---
# Fields: freq_start (MHz), freq_end (MHz), dutycycle, spacing (MHz), power_limit (dBm), wide_lora, name
MESHTASTIC_REGIONS = {
    "UNSET":        {"freq_start": 902.0,   "freq_end": 928.0,   "dutycycle": 0.0,   "spacing": 0.0, "power_limit": 0,  "wide_lora": False, "description": "Not Set"},
    "US":           {"freq_start": 902.0,   "freq_end": 928.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 30, "wide_lora": False, "description": "United States"},
    "EU_433":       {"freq_start": 433.0,   "freq_end": 434.0,   "dutycycle": 10.0,  "spacing": 0.0, "power_limit": 10, "wide_lora": False, "description": "EU 433MHz"},
    "EU_868":       {"freq_start": 869.4,   "freq_end": 869.65,  "dutycycle": 10.0,  "spacing": 0.0, "power_limit": 27, "wide_lora": False, "description": "EU 868MHz"},
    "CN":           {"freq_start": 470.0,   "freq_end": 510.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 19, "wide_lora": False, "description": "China"},
    "JP":           {"freq_start": 920.5,   "freq_end": 923.5,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 13, "wide_lora": False, "description": "Japan"},
    "ANZ":          {"freq_start": 915.0,   "freq_end": 928.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 30, "wide_lora": False, "description": "Australia & NZ"},
    "ANZ_433":      {"freq_start": 433.05,  "freq_end": 434.79,  "dutycycle": 100.0, "spacing": 0.0, "power_limit": 14, "wide_lora": False, "description": "Australia & NZ 433 MHz"},
    "RU":           {"freq_start": 868.7,   "freq_end": 869.2,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 20, "wide_lora": False, "description": "Russia"},
    "KR":           {"freq_start": 920.0,   "freq_end": 923.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 23, "wide_lora": False, "description": "Korea"},
    "TW":           {"freq_start": 920.0,   "freq_end": 925.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 27, "wide_lora": False, "description": "Taiwan"},
    "IN":           {"freq_start": 865.0,   "freq_end": 867.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 30, "wide_lora": False, "description": "India"},
    "NZ_865":       {"freq_start": 864.0,   "freq_end": 868.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 36, "wide_lora": False, "description": "New Zealand 865MHz"},
    "TH":           {"freq_start": 920.0,   "freq_end": 925.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 16, "wide_lora": False, "description": "Thailand"},
    "UA_433":       {"freq_start": 433.0,   "freq_end": 434.7,   "dutycycle": 10.0,  "spacing": 0.0, "power_limit": 10, "wide_lora": False, "description": "Ukraine 433MHz"},
    "UA_868":       {"freq_start": 868.0,   "freq_end": 868.6,   "dutycycle": 1.0,   "spacing": 0.0, "power_limit": 14, "wide_lora": False, "description": "Ukraine 868MHz"},
    "MY_433":       {"freq_start": 433.0,   "freq_end": 435.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 20, "wide_lora": False, "description": "Malaysia 433MHz"},
    "MY_919":       {"freq_start": 919.0,   "freq_end": 924.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 27, "wide_lora": False, "description": "Malaysia 919MHz"},
    "SG_923":       {"freq_start": 917.0,   "freq_end": 925.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 20, "wide_lora": False, "description": "Singapore 923MHz"},
    "PH_433":       {"freq_start": 433.0,   "freq_end": 434.7,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 10, "wide_lora": False, "description": "Philippines 433MHz"},
    "PH_868":       {"freq_start": 868.0,   "freq_end": 869.4,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 14, "wide_lora": False, "description": "Philippines 868MHz"},
    "PH_915":       {"freq_start": 915.0,   "freq_end": 918.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 24, "wide_lora": False, "description": "Philippines 915MHz"},
    "KZ_433":       {"freq_start": 433.075, "freq_end": 434.775, "dutycycle": 100.0, "spacing": 0.0, "power_limit": 10, "wide_lora": False, "description": "Kazakhstan 433MHz"},
    "KZ_863":       {"freq_start": 863.0,   "freq_end": 868.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 30, "wide_lora": False, "description": "Kazakhstan 863MHz"},
    "NP_865":       {"freq_start": 865.0,   "freq_end": 868.0,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 30, "wide_lora": False, "description": "Nepal 865MHz"},
    "BR_902":       {"freq_start": 902.0,   "freq_end": 907.5,   "dutycycle": 100.0, "spacing": 0.0, "power_limit": 30, "wide_lora": False, "description": "Brazil 902MHz"},
    "LORA_24":      {"freq_start": 2400.0,  "freq_end": 2483.5,  "dutycycle": 0.0,   "spacing": 0.0, "power_limit": 10, "wide_lora": True,  "description": "2.4GHz worldwide"},
}

# --- Modem Presets ---
MESHTASTIC_MODEM_PRESETS = {
    "LONG_FAST":       {"channel_name": "LongFast",    "bw_narrow": 250.0,  "bw_wide": 812.5, "sf": 11, "cr": 5, "description": "Long Range, Fast (default)"},
    "MEDIUM_FAST":     {"channel_name": "MediumFast",  "bw_narrow": 250.0,  "bw_wide": 812.5, "sf": 9,  "cr": 5, "description": "Medium Range, Fast"},
    "LONG_SLOW":       {"channel_name": "LongSlow",    "bw_narrow": 125.0,  "bw_wide": 406.25,"sf": 12, "cr": 8, "description": "Long Range, Slow (deprecated)"},
    "MEDIUM_SLOW":     {"channel_name": "MediumSlow",  "bw_narrow": 250.0,  "bw_wide": 812.5, "sf": 10, "cr": 5, "description": "Medium Range, Slow"},
    "SHORT_FAST":      {"channel_name": "ShortFast",   "bw_narrow": 250.0,  "bw_wide": 812.5, "sf": 7,  "cr": 5, "description": "Short Range, Fast"},
    "SHORT_SLOW":      {"channel_name": "ShortSlow",   "bw_narrow": 250.0,  "bw_wide": 812.5, "sf": 8,  "cr": 5, "description": "Short Range, Slow"},
    "SHORT_TURBO":     {"channel_name": "ShortTurbo",  "bw_narrow": 500.0,  "bw_wide": 1625.0,"sf": 7,  "cr": 5, "description": "Short Range, Turbo (not legal everywhere)"},
    "LONG_TURBO":      {"channel_name": "LongTurbo",   "bw_narrow": 500.0,  "bw_wide": 1625.0,"sf": 11, "cr": 8, "description": "Long Range, Turbo"},
    "LONG_MODERATE":   {"channel_name": "LongMod",     "bw_narrow": 125.0,  "bw_wide": 406.25,"sf": 11, "cr": 8, "description": "Long Range, Moderate"},
    "VERY_LONG_SLOW":  {"channel_name": "VLongSlow",   "bw_narrow": 62.5,   "bw_wide": 250.0, "sf": 12, "cr": 8, "description": "Very Long Range, Very Slow"},
}
PRESET_ID_MAP = {
    0:  None,          # unknown / single-preset mode
    1:  "LONG_FAST",
    2:  "MEDIUM_FAST",
    3:  "LONG_SLOW",
    4:  "MEDIUM_SLOW",
    5:  "SHORT_FAST",
    6:  "SHORT_SLOW",
    7:  "SHORT_TURBO",
    8:  "LONG_TURBO",
    9:  "LONG_MODERATE",
    10: "VERY_LONG_SLOW",
}
PRESET_ID_REVERSE = {v: k for k, v in PRESET_ID_MAP.items() if v is not None}
PRESET_COLORS = {
    "LONG_FAST":      "#22c55e",  # green
    "MEDIUM_FAST":    "#3b82f6",  # blue
    "LONG_SLOW":      "#a855f7",  # purple
    "MEDIUM_SLOW":    "#f59e0b",  # amber
    "SHORT_FAST":     "#ef4444",  # red
    "SHORT_SLOW":     "#f97316",  # orange
    "SHORT_TURBO":    "#ec4899",  # pink
    "LONG_TURBO":     "#06b6d4",  # cyan
    "LONG_MODERATE":  "#84cc16",  # lime
    "VERY_LONG_SLOW": "#64748b",  # slate
}

def show_fatal_error(title: str, message: str):
    """Show a fatal error dialog with copyable text, works before NiceGUI starts on all platforms."""
    close_all_splash()
    try:

        BG      = "#f8f8f8"
        FG      = "#1a1a1a"
        FG_ERR  = "#c0392b"
        BTN_BG  = "#e0e0e0"

        root = tk.Tk()
        root.configure(bg=BG)
        root.title(title)
        root.attributes('-topmost', True)
        root.resizable(True, True)
        root.minsize(480, 280)

        # Center on screen
        root.update_idletasks()
        w, h = 520, 320
        x = (root.winfo_screenwidth() // 2) - (w // 2)
        y = (root.winfo_screenheight() // 2) - (h // 2)
        root.geometry(f"{w}x{h}+{x}+{y}")

        # Title label
        tk.Label(root, text=f"⛔ {title}", font=("Helvetica", 13, "bold"), fg=FG_ERR, bg=BG, pady=10).pack()

        # Scrollable, selectable text area
        txt = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier", 10), height=10, relief=tk.FLAT, bg=BG, fg=FG)
        txt.insert(tk.END, message)
        txt.configure(state=tk.DISABLED)  # read-only but still selectable
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        # Buttons row
        btn_frame = tk.Frame(root, bg=BG)
        btn_frame.pack(pady=(0, 10))

        def copy_to_clipboard():
            root.clipboard_clear()
            root.clipboard_append(message)
            copy_btn.configure(text="✓ Copied!")
            root.after(2000, lambda: copy_btn.configure(text="Copy to clipboard"))

        copy_btn = tk.Button(btn_frame, text="Copy to clipboard", command=copy_to_clipboard, width=20, bg=BTN_BG, fg=FG)
        copy_btn.pack(side=tk.LEFT, padx=6)

        tk.Button(btn_frame, text="Open GitHub Issues", width=20, bg=BTN_BG, fg=FG,
                  command=lambda: __import__('webbrowser').open(f"{GITHUB_URL}/issues")).pack(side=tk.LEFT, padx=6)

        tk.Button(btn_frame, text="Close", command=root.destroy, width=10, bg=BTN_BG, fg=FG).pack(side=tk.LEFT, padx=6)

        root.mainloop()

    except Exception:
        print(f"FATAL ERROR — {title}: {message}", file=sys.stderr)

def _djb2_hash(s: str) -> int:
    """djb2 hash algorithm."""
    h = 5381
    for c in s:
        h = ((h << 5) + h + ord(c)) & 0xFFFFFFFF
    return h

def meshtastic_calc_freq(region_key: str, preset_key: str, frequency_slot: int = 0, channel_name: str | None = None) -> dict:
    """
    Calculate center frequency exactly as Meshtastic firmware does.
    
    Args:
        region_key: e.g. "EU_868", "US"
        preset_key: e.g. "LONG_FAST", "MEDIUM_SLOW"
        frequency_slot: 1-based manual slot (0 = auto/hash-based)
        channel_name: custom channel name for hash (None = use preset default name)
    
    Returns dict with:
        center_freq_hz: int - center frequency in Hz (for engine)
        center_freq_mhz: float - center frequency in MHz
        num_slots: int - total available frequency slots
        slot_used: int - 1-based slot number actually used
        bw_khz: float - bandwidth in kHz
        sf: int - spreading factor
        cr: int - coding rate
        channel_name: str - channel name used for hash
        valid: bool - whether the region/preset combo is valid
        error: str | None
    """
    region = MESHTASTIC_REGIONS.get(region_key)
    preset = MESHTASTIC_MODEM_PRESETS.get(preset_key)
    
    if not region:
        return {"valid": False, "error": f"Unknown region: {region_key}"}
    if preset_key == "ALL":
        return {"valid": True, "error": None, "all_presets": True}
    if not preset:
        return {"valid": False, "error": f"Unknown preset: {preset_key}"}
    
    wide_lora = region.get("wide_lora", False)
    bw_khz = preset["bw_wide"] if wide_lora else preset["bw_narrow"]
    sf = preset["sf"]
    cr = preset["cr"]
    spacing = region.get("spacing", 0.0)
    freq_start = region["freq_start"]
    freq_end = region["freq_end"]
    
    bw_mhz = bw_khz / 1000.0
    
    # num_channels = floor((freqEnd - freqStart) / (spacing + bw_MHz))
    band_width = freq_end - freq_start
    slot_width = spacing + bw_mhz
    if slot_width <= 0:
        return {"valid": False, "error": "Invalid slot width (spacing + bw <= 0)"}
    
    num_slots = int(math.floor(band_width / slot_width))
    if num_slots < 1:
        narerrtext = translate("panel.connection.settings.internal.info.band_too_narrow", "Band too narrow for preset: only {band_width}MHz available, need {slot_width}MHz per slot, preset not compatible with this region.").format(band_width=f"{band_width:.3f}", slot_width=f"{slot_width:.3f}")
        return {"valid": False, "error": narerrtext}
    
    # Determine channel_num (0-based)
    ch_name = channel_name if channel_name else preset["channel_name"]
    if frequency_slot != 0:
        # Manual slot: slot is 1-based in firmware, convert to 0-based
        channel_num_0 = (frequency_slot - 1) % num_slots
    else:
        # Hash-based
        channel_num_0 = _djb2_hash(ch_name) % num_slots
    
    # freq = freqStart + spacing/2 + channel_num * (spacing + bw_MHz)
    freq_mhz = freq_start + (bw_mhz / 2.0) + channel_num_0 * slot_width
    
    return {
        "valid": True,
        "error": None,
        "center_freq_mhz": freq_mhz,
        "center_freq_hz": int(round(freq_mhz * 1_000_000)),
        "num_slots": num_slots,
        "slot_used": channel_num_0 + 1,  # back to 1-based for display
        "bw_khz": bw_khz,
        "sf": sf,
        "cr": cr,
        "channel_name": ch_name,
    }

# SVG Icon (Envelope with Antenna)
APP_ICON_SVG = """
<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <!-- Envelope Body -->
  <rect x="10" y="35" width="80" height="50" rx="5" fill="#4CAF50" />
  <!-- Envelope Flap -->
  <path d="M 10 35 L 50 65 L 90 35" stroke="white" stroke-width="4" fill="none" />
  <!-- Antenna Pole -->
  <line x1="75" y1="35" x2="75" y2="10" stroke="#4CAF50" stroke-width="4" />
  <!-- Antenna Tip -->
  <circle cx="75" cy="10" r="3" fill="#4CAF50" />
  <!-- Radio Waves -->
  <path d="M 65 15 Q 55 10 65 5" stroke="#4CAF50" stroke-width="2" fill="none" />
  <path d="M 85 15 Q 95 10 85 5" stroke="#4CAF50" stroke-width="2" fill="none" />
</svg>
"""

def ensure_app_icon_file():
    if getattr(sys, 'frozen', False):
        return
    try:
        base_path = os.path.dirname(os.path.abspath(__file__))
        svg_path = os.path.join(base_path, "app_icon.svg")
        if not os.path.isfile(svg_path):
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(APP_ICON_SVG)
    except Exception:
        pass
    
def setup_static_files():
    maps_dir = get_resource_path('offlinemaps') if getattr(sys, 'frozen', False) else os.path.join(get_app_path(), 'offlinemaps')
    if not os.path.isdir(maps_dir):
        try:
            os.makedirs(maps_dir, exist_ok=True)
        except:
            pass
    
    if os.path.isdir(maps_dir):
        app.add_static_files('/static/offlinemaps', maps_dir)

def has_tile_internet(retries=3, timeout=5) -> bool:
    urls = [
        'https://tile.openstreetmap.org/0/0/0.png',
        'https://tile.openstreetmap.org/1/0/0.png',
        'https://tile.openstreetmap.org',
    ]
    for attempt in range(retries):
        for url in urls:
            try:
                urllib.request.urlopen(url, timeout=timeout)
                return True
            except Exception:
                continue
        if attempt < retries - 1:
            time.sleep(0.5)
    return False

_offline_topology_cache = {}
_offline_geo_cache = {}

def get_offline_topology():
    maps_dir = get_resource_path('offlinemaps') if getattr(sys, 'frozen', False) else os.path.join(get_app_path(), 'offlinemaps')
    topo_path = os.path.join(maps_dir, 'map.json')
    if not os.path.isfile(topo_path):
        return None
    cached = _offline_topology_cache.get(topo_path)
    if cached is not None:
        return cached
    try:
        with open(topo_path, 'r', encoding='utf-8') as f:
            topo = json.load(f)
        _offline_topology_cache[topo_path] = topo
        return topo
    except Exception:
        return None

def _decode_topology_arcs(topology):
    key = id(topology)
    cached = _offline_geo_cache.get(('arcs', key))
    if cached is not None:
        return cached
    arcs = topology.get('arcs') or []
    transform = topology.get('transform') or {}
    scale = transform.get('scale')
    translate = transform.get('translate')
    decoded = []
    for arc in arcs:
        x = 0
        y = 0
        coords = []
        for point in arc:
            x += point[0]
            y += point[1]
            if scale and translate:
                xx = x * scale[0] + translate[0]
                yy = y * scale[1] + translate[1]
            else:
                xx = x
                yy = y
            coords.append([xx, yy])
        decoded.append(coords)
    _offline_geo_cache[('arcs', key)] = decoded
    return decoded

def _topology_transform_coords(coords, scale, translate):
    if not scale or not translate:
        return coords
    if not coords:
        return coords
    first = coords[0]
    if isinstance(first, (int, float)):
        return [coords[0] * scale[0] + translate[0], coords[1] * scale[1] + translate[1]]
    return [_topology_transform_coords(c, scale, translate) for c in coords]

def _topology_object_to_feature_collection(topology, object_name):
    cache_key = ('object', object_name)
    cached = _offline_geo_cache.get(cache_key)
    if cached is not None:
        return cached
    objects = topology.get('objects') or {}
    obj = objects.get(object_name)
    if not obj:
        return None
    decoded_arcs = _decode_topology_arcs(topology)
    transform = topology.get('transform') or {}
    scale = transform.get('scale')
    translate = transform.get('translate')
    def build_line(arc_indices):
        coords = []
        for ai in arc_indices:
            idx = ai if ai >= 0 else ~ai
            if idx < 0 or idx >= len(decoded_arcs):
                continue
            arc = decoded_arcs[idx]
            if ai < 0:
                arc = list(reversed(arc))
            if coords:
                arc = arc[1:]
            coords.extend(arc)
        return coords
    def geometry_to_geo(geom):
        gtype = geom.get('type')
        if gtype == 'Point':
            coords = geom.get('coordinates') or []
            return {'type': 'Point', 'coordinates': _topology_transform_coords(coords, scale, translate)}
        if gtype == 'MultiPoint':
            coords = geom.get('coordinates') or []
            return {'type': 'MultiPoint', 'coordinates': _topology_transform_coords(coords, scale, translate)}
        if gtype == 'LineString':
            arcs = geom.get('arcs') or []
            return {'type': 'LineString', 'coordinates': build_line(arcs)}
        if gtype == 'MultiLineString':
            lines = []
            for part in geom.get('arcs') or []:
                lines.append(build_line(part))
            return {'type': 'MultiLineString', 'coordinates': lines}
        if gtype == 'Polygon':
            rings = []
            for ring_arcs in geom.get('arcs') or []:
                rings.append(build_line(ring_arcs))
            return {'type': 'Polygon', 'coordinates': rings}
        if gtype == 'MultiPolygon':
            polys = []
            for poly_arcs in geom.get('arcs') or []:
                rings = []
                for ring_arcs in poly_arcs:
                    rings.append(build_line(ring_arcs))
                polys.append(rings)
            return {'type': 'MultiPolygon', 'coordinates': polys}
        return None
    def geometry_to_features(geom):
        gtype = geom.get('type')
        if gtype == 'GeometryCollection':
            result = []
            for sub in geom.get('geometries') or []:
                result.extend(geometry_to_features(sub))
            return result
        mapped = geometry_to_geo(geom)
        if not mapped:
            return []
        properties = geom.get('properties') or {}
        return [{'type': 'Feature', 'properties': properties, 'geometry': mapped}]
    if obj.get('type') == 'GeometryCollection':
        features = []
        for g in obj.get('geometries') or []:
            features.extend(geometry_to_features(g))
    else:
        features = geometry_to_features(obj)
    feature_collection = {'type': 'FeatureCollection', 'features': features}
    _offline_geo_cache[cache_key] = feature_collection
    return feature_collection

def _feature_polygon_centroid(geometry):
    gtype = geometry.get('type')
    coords = geometry.get('coordinates')
    if not coords:
        return None
    if gtype == 'Polygon':
        ring = coords[0]
    elif gtype == 'MultiPolygon':
        ring = coords[0][0]
    else:
        return None
    if len(ring) < 3:
        return None
    area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if area == 0.0:
        return ring[0]
    area *= 0.5
    return [cx / (6.0 * area), cy / (6.0 * area)]

def _geometry_bbox(geometry):
    if not geometry:
        return None
    gtype = geometry.get('type')
    coords = geometry.get('coordinates')
    if coords is None:
        return None

    def iter_points(c):
        if not c:
            return
        first = c[0]
        if isinstance(first, (int, float)) and len(c) >= 2:
            yield c
            return
        for sub in c:
            yield from iter_points(sub)

    if gtype == 'Point':
        try:
            lon, lat = coords
        except Exception:
            return None
        return {'south': lat, 'west': lon, 'north': lat, 'east': lon}

    min_lon = None
    min_lat = None
    max_lon = None
    max_lat = None
    for pt in iter_points(coords):
        try:
            lon, lat = pt[0], pt[1]
        except Exception:
            continue
        if min_lon is None:
            min_lon = max_lon = lon
            min_lat = max_lat = lat
        else:
            if lon < min_lon:
                min_lon = lon
            if lon > max_lon:
                max_lon = lon
            if lat < min_lat:
                min_lat = lat
            if lat > max_lat:
                max_lat = lat
    if min_lon is None:
        return None
    return {'south': min_lat, 'west': min_lon, 'north': max_lat, 'east': max_lon}

def _bbox_intersects(view_bounds: dict, feature_bbox: dict) -> bool:
    if not view_bounds or not feature_bbox:
        return False
    return not (
        feature_bbox['north'] < view_bounds['south'] or
        feature_bbox['south'] > view_bounds['north'] or
        feature_bbox['east'] < view_bounds['west'] or
        feature_bbox['west'] > view_bounds['east']
    )

def _ensure_feature_indexes(fc: dict):
    feats = (fc or {}).get('features') or []
    for f in feats:
        if not isinstance(f, dict):
            continue
        if f.get('_mesh_bbox') is None:
            geom = f.get('geometry') or {}
            f['_mesh_bbox'] = _geometry_bbox(geom)
        if f.get('_mesh_centroid') is None:
            geom = f.get('geometry') or {}
            f['_mesh_centroid'] = _feature_polygon_centroid(geom)
    return fc

def _extract_feature_name_en(properties):
    if not properties:
        return None
    for key in ['name_en', 'NAME_EN', 'NAMEEN', 'NAME_ENGLI', 'NAME_ENGL', 'NAMEENG']:
        if key in properties and properties[key]:
            return str(properties[key])
    return None

def _extract_feature_name(properties):
    if not properties:
        return None
    name_en = _extract_feature_name_en(properties)
    if name_en:
        return name_en
    for key in ['region', 'REGION', 'NAME', 'NAME_LONG', 'ADMIN', 'admin', 'name']:
        if key in properties and properties[key]:
            return str(properties[key])
    for value in properties.values():
        if isinstance(value, str) and value:
            return value
    return None

def _normalize_topo_key(s: str) -> str:
    if not isinstance(s, str):
        return ''
    out = []
    prev_us = False
    for ch in s.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        else:
            if not prev_us:
                out.append('_')
                prev_us = True
    norm = ''.join(out).strip('_')
    while '__' in norm:
        norm = norm.replace('__', '_')
    return norm

def _pick_topo_object_name(topo: dict, preferred: list[str]) -> str | None:
    objects = (topo or {}).get('objects') or {}
    if not objects:
        return None

    for name in preferred:
        if name in objects:
            return name

    norm_to_key = {}
    for k in objects.keys():
        norm_to_key[_normalize_topo_key(k)] = k

    for name in preferred:
        nk = _normalize_topo_key(name)
        if nk in norm_to_key:
            return norm_to_key[nk]

    preferred_tokens = []
    for name in preferred:
        nk = _normalize_topo_key(name)
        if nk:
            preferred_tokens.append(nk.split('_'))

    for k in objects.keys():
        ok = _normalize_topo_key(k)
        if not ok:
            continue
        for toks in preferred_tokens:
            if not toks:
                continue
            if all(t in ok.split('_') for t in toks):
                return k
    return None

def _topo_object_stats(obj: dict) -> dict:
    stats = {
        'points': 0,
        'polys': 0,
        'lines': 0,
        'has_name_en': False,
        'type_values': set(),
    }
    if not obj:
        return stats
    geoms = []
    if obj.get('type') == 'GeometryCollection':
        geoms = obj.get('geometries') or []
    else:
        geoms = [obj]
    for g in geoms[:5000]:
        gtype = (g or {}).get('type')
        props = (g or {}).get('properties') or {}
        if isinstance(props, dict) and (props.get('name_en') or props.get('NAME_EN')):
            stats['has_name_en'] = True
        tv = (props.get('type') or props.get('TYPE'))
        if isinstance(tv, str) and tv:
            stats['type_values'].add(tv.strip().lower())
        if gtype in ('Point', 'MultiPoint'):
            stats['points'] += 1
        elif gtype in ('Polygon', 'MultiPolygon'):
            stats['polys'] += 1
        elif gtype in ('LineString', 'MultiLineString'):
            stats['lines'] += 1
    return stats

def _detect_topo_object_names(topo: dict) -> dict:
    objects = (topo or {}).get('objects') or {}
    if not objects:
        return {}

    candidates = []
    for key, obj in objects.items():
        stats = _topo_object_stats(obj)
        candidates.append((key, stats))

    def score_country(key: str, s: dict) -> int:
        nk = _normalize_topo_key(key)
        score = 0
        if 'admin_0' in nk or 'admin0' in nk or 'countries' in nk or 'country' in nk:
            score += 50
        score += min(40, s['polys'])
        if s['has_name_en']:
            score += 20
        return score

    def score_admin1(key: str, s: dict) -> int:
        nk = _normalize_topo_key(key)
        score = 0
        if 'admin_1' in nk or 'admin1' in nk or 'states' in nk or 'provinces' in nk:
            score += 50
        score += min(40, s['polys'])
        if 'province' in s['type_values']:
            score += 40
        if s['has_name_en']:
            score += 10
        return score

    def score_places(key: str, s: dict) -> int:
        nk = _normalize_topo_key(key)
        score = 0
        if 'populated' in nk or 'places' in nk or 'cities' in nk or 'towns' in nk:
            score += 50
        score += min(40, s['points'])
        if s['has_name_en']:
            score += 20
        return score

    def score_regions(key: str, s: dict) -> int:
        nk = _normalize_topo_key(key)
        score = 0
        if 'regions' in nk or 'regioni' in nk:
            score += 60
        score += min(40, s['polys'])
        if s['has_name_en']:
            score += 20
        return score

    best = {'countries': None, 'admin1': None, 'places': None, 'regions': None}
    best_score = {k: -1 for k in best.keys()}
    for key, s in candidates:
        sc = score_country(key, s)
        if sc > best_score['countries']:
            best_score['countries'] = sc
            best['countries'] = key
        sa = score_admin1(key, s)
        if sa > best_score['admin1']:
            best_score['admin1'] = sa
            best['admin1'] = key
        sp = score_places(key, s)
        if sp > best_score['places']:
            best_score['places'] = sp
            best['places'] = key
        sr = score_regions(key, s)
        if sr > best_score['regions']:
            best_score['regions'] = sr
            best['regions'] = key

    return best

# --- CONFIGURATION & STATE ---

class AppState:
    def __init__(self):
        self.network_mode = "meshtastic" # "meshtastic" | "meshcore"
        self.connect_mode = None  # None | "direct" | "external"
        self.engine_proc = None
        self.last_rx_ts = 0.0
        self.rx_seen_once = False
        self.autosave_interval_sec = 30
        self.autosave_last_ts = 0.0

        self.direct_region = "EU_868"
        self.direct_preset = "MEDIUM_FAST"
        self.direct_frequency_slot = 0        # 0 = auto (hash-based), 1..N = manual
        self.direct_channel_name = ""         # "" = use default name of the preset
        self.direct_ppm = 0
        self.direct_gain = 30
        self.direct_device_args = "rtl=0"
        self.direct_device_detected_args = []
        self.direct_bias_tee = False
        self.direct_port = "20002"
        self.direct_key_b64 = "AQ=="

        # MeshCore Settings
        self.meshcore_region = "USA_CANADA"
        self.meshcore_preset = "RECOMMENDED"
        self.meshcore_custom_freq = 910.525
        self.meshcore_custom_sf = 7
        self.meshcore_custom_bw = 62.5
        self.meshcore_custom_cr = 5
        self.meshcore_custom_sync_word = 0x12
        self.meshcore_custom_preamble_len = 16

        self.external_ip = "127.0.0.1"
        self.external_port = "20002"
        self.external_key_b64 = "AQ=="

        self.connected = False
        self.ip_address = "127.0.0.1"
        self.port = "20002"
        self.aes_key_b64 = "AQ==" # Default Meshtastic Key representation (means default)
        self.aes_key_bytes = None

        # Multi-channel monitoring
        # Each entry: {"id": str (uuid), "name": str, "key_b64": str, "label": str}
        self.extra_channels = []
        # Messages per channel: {"channel_id": deque(maxlen=100)}
        self.channel_messages = {}  # channel_id -> list of new messages to render
        self.channel_unread = {}    # channel_id -> bool (has unread)
        self.channel_unread_count = {}  # channel_id -> int
        self.active_channel_id = "default"
        self.channels_order = [] 
        
        # Data Stores
        self.nodes = {} # Key: NodeID (e.g., "!322530e5"), Value: Dict with info
        self.messages = deque(maxlen=100) # List of chat messages
        self.logs = deque(maxlen=500) # Raw logs
        self.seen_packets = deque(maxlen=300) # Deduplication buffer (Sender, PacketID)
        self.raw_packet_count = 0
        
        # UI Update Flags (simple dirty checking)
        self.new_logs = []
        self.new_messages = []
        self.nodes_updated = False
        self.nodes_list_updated = False # Separate flag for grid to avoid conflict
        self.nodes_list_force_refresh = False # Force full reload of grid (e.g. after import)
        self.chat_force_refresh = False # Force full reload of chat (e.g. after import or name change)
        self.chat_force_scroll = False # Flag to force scroll to bottom (e.g. after import)
        self.dirty_nodes = set() # Track modified nodes for delta updates
        self.lock = threading.Lock() # Thread safety for dirty_nodes
        self.verbose_logging = True # Default to verbose logging
        self.theme = "dark"
        self.map_center_lat = None
        self.map_center_lng = None
        self.map_zoom = None

        # Error checking
        self.rtlsdr_error_pending = False
        self.rtlsdr_error_text = ""

        # Connection Popup auto state
        self.connection_dialog_shown = False

        self.update_check_done = False
        self.update_check_running = False
        self.update_available = False
        self.latest_version = None
        self.latest_release_url = None
        self.update_popup_shown = False
        self.update_popup_ack_version = None

class MeshStatsManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.enabled = False
        self.freeze_now = time.time()
        self.reset()

    def set_enabled(self, enabled: bool):
        with self._lock:
            self.enabled = bool(enabled)
            if self.enabled:
                self.freeze_now = None
            else:
                self.freeze_now = time.time()

    def reset(self):
        now = time.time()
        with self._lock:
            self.started_ts = now
            if not self.enabled:
                self.freeze_now = now

            self.total_packets = 0
            self.packet_ts_60s = deque()

            self.node_last_seen_ts = {}
            self.node_first_seen_ts = {}
            self.per_node_packet_count = {}

            self.crc_ok = 0
            self.crc_fail = 0
            self.decrypt_ok = 0
            self.decrypt_fail = 0
            self.invalid_protobuf = 0
            self.unknown_portnum = 0

            self.direct_packets = 0
            self.multihop_packets = 0
            self.hop_sum = 0
            self.hop_count = 0

            self.snr_values = deque(maxlen=250)
            self.rssi_values = deque(maxlen=250)

            self.channel_util_samples = deque()
            self.air_util_tx_samples = deque()

            self.ppm_history = deque(maxlen=180)

            self._crc_invalid_by_packet = {}

    def mark_crc_invalid_packet(self, sender_bytes: bytes, packet_id_bytes: bytes, ts: float | None = None):
        if not sender_bytes or not packet_id_bytes:
            return
        if ts is None:
            ts = time.time()
        k = (bytes(sender_bytes), bytes(packet_id_bytes))
        with self._lock:
            self._crc_invalid_by_packet[k] = float(ts)
            cutoff = float(ts) - 5.0
            stale = [kk for kk, tts in self._crc_invalid_by_packet.items() if tts < cutoff]
            for kk in stale:
                self._crc_invalid_by_packet.pop(kk, None)

    def consume_crc_invalid_packet(self, sender_bytes: bytes, packet_id_bytes: bytes, now: float | None = None) -> bool:
        if not sender_bytes or not packet_id_bytes:
            return False
        if now is None:
            now = time.time()
        k = (bytes(sender_bytes), bytes(packet_id_bytes))
        with self._lock:
            ts = self._crc_invalid_by_packet.pop(k, None)
            if ts is None:
                return False
            return (float(now) - float(ts)) <= 5.0

    @staticmethod
    def _clamp01(x: float) -> float:
        if x < 0.0:
            return 0.0
        if x > 1.0:
            return 1.0
        return x

    def on_frame_ok(self):
        with self._lock:
            if not self.enabled:
                return
            self.crc_ok += 1

    def on_frame_fail(self):
        with self._lock:
            if not self.enabled:
                return
            self.crc_fail += 1

    def on_packet_received(self, sender_id: str | None, hops: int | None, snr: float | None, rssi: float | None, ts: float | None = None):
        with self._lock:
            if not self.enabled:
                return
            if ts is None:
                ts = time.time()
            self.total_packets += 1
            self.packet_ts_60s.append(ts)
            cutoff = ts - 60.0
            while self.packet_ts_60s and self.packet_ts_60s[0] < cutoff:
                self.packet_ts_60s.popleft()

            if sender_id:
                self.node_last_seen_ts[sender_id] = ts
                if sender_id not in self.node_first_seen_ts:
                    self.node_first_seen_ts[sender_id] = ts
                self.per_node_packet_count[sender_id] = self.per_node_packet_count.get(sender_id, 0) + 1

            if isinstance(hops, int):
                if hops <= 0:
                    self.direct_packets += 1
                else:
                    self.multihop_packets += 1
                self.hop_sum += max(0, int(hops))
                self.hop_count += 1

            if isinstance(snr, (int, float)):
                self.snr_values.append(float(snr))
            if isinstance(rssi, (int, float)):
                self.rssi_values.append(float(rssi))

    def on_decrypt_ok(self):
        with self._lock:
            if not self.enabled:
                return
            self.decrypt_ok += 1

    def on_decrypt_fail(self):
        with self._lock:
            if not self.enabled:
                return
            self.decrypt_fail += 1

    def on_invalid_protobuf(self):
        with self._lock:
            if not self.enabled:
                return
            self.invalid_protobuf += 1

    def on_portnum_seen(self, portnum: int, supported: bool):
        if supported:
            return
        with self._lock:
            if not self.enabled:
                return
            self.unknown_portnum += 1

    def on_telemetry(self, node_id: str | None, metrics: dict, ts: float | None = None):
        cu = metrics.get("channel_utilization")
        au = metrics.get("air_util_tx")
        with self._lock:
            if not self.enabled:
                return
            if ts is None:
                ts = time.time()
            if node_id:
                self.node_last_seen_ts[node_id] = ts
            if isinstance(cu, (int, float)):
                self.channel_util_samples.append((ts, float(cu)))
            if isinstance(au, (int, float)):
                self.air_util_tx_samples.append((ts, float(au)))
            cutoff = ts - 600.0
            while self.channel_util_samples and self.channel_util_samples[0][0] < cutoff:
                self.channel_util_samples.popleft()
            while self.air_util_tx_samples and self.air_util_tx_samples[0][0] < cutoff:
                self.air_util_tx_samples.popleft()

    def snapshot(self, now: float | None = None) -> dict:
        with self._lock:
            if now is None:
                now = time.time()
            if not self.enabled and self.freeze_now is not None:
                now = self.freeze_now
            cutoff_60 = now - 60.0
            while self.packet_ts_60s and self.packet_ts_60s[0] < cutoff_60:
                self.packet_ts_60s.popleft()

            ppm = len(self.packet_ts_60s)

            active_5m = 0
            active_10m = 0
            cutoff_5 = now - 300.0
            cutoff_10 = now - 600.0
            for _nid, ts in self.node_last_seen_ts.items():
                if ts >= cutoff_5:
                    active_5m += 1
                if ts >= cutoff_10:
                    active_10m += 1

            new_nodes_last_hour = 0
            cutoff_h = now - 3600.0
            for _nid, ts in self.node_first_seen_ts.items():
                if ts >= cutoff_h:
                    new_nodes_last_hour += 1

            most_active_node = None
            most_active_count = 0
            for nid, cnt in self.per_node_packet_count.items():
                if cnt > most_active_count:
                    most_active_node = nid
                    most_active_count = cnt

            snr_avg = (sum(self.snr_values) / len(self.snr_values)) if self.snr_values else None
            rssi_avg = (sum(self.rssi_values) / len(self.rssi_values)) if self.rssi_values else None

            direct_ratio = None
            multihop_ratio = None
            hop_avg = None
            denom_hops = self.direct_packets + self.multihop_packets
            if denom_hops > 0:
                direct_ratio = (self.direct_packets / denom_hops) * 100.0
                multihop_ratio = (self.multihop_packets / denom_hops) * 100.0
            if self.hop_count > 0:
                hop_avg = self.hop_sum / self.hop_count

            cu_vals = [v for _ts, v in self.channel_util_samples if _ts >= cutoff_10]
            au_vals = [v for _ts, v in self.air_util_tx_samples if _ts >= cutoff_10]
            cu_avg = (sum(cu_vals) / len(cu_vals)) if cu_vals else None
            au_max = (max(au_vals)) if au_vals else None

            errors_total = self.crc_fail + self.invalid_protobuf
            error_rate = (errors_total / self.total_packets) * 100.0 if self.total_packets > 0 else 0.0

            def _dyn_score(pairs: list[tuple[float | None, float]]) -> int:
                num = 0.0
                den = 0.0
                for v, w in pairs:
                    if v is None:
                        continue
                    num += float(v) * float(w)
                    den += float(w)
                if den <= 0.0:
                    return 0
                return int(round(self._clamp01(num / den) * 100.0))

            def _level4(score: int) -> tuple[str, str]:
                if score >= 75:
                    return ("excellent", "green")
                if score >= 55:
                    return ("good", "yellow")
                if score >= 35:
                    return ("fair", "orange")
                return ("poor", "red")

            def _health4(score: int) -> tuple[str, str]:
                if score >= 75:
                    return ("stable", "green")
                if score >= 55:
                    return ("intermittent", "yellow")
                if score >= 35:
                    return ("unstable", "orange")
                return ("critical", "red")

            traffic_score = 0
            integrity_score = 0
            signal_score = 0
            global_health_score = 0
            traffic_level, traffic_color = _level4(0)
            integrity_level, integrity_color = _level4(0)
            signal_level, signal_color = _level4(0)
            global_health_level, global_health_color = _health4(0)

            if self.total_packets > 0:
                if ppm > 0:
                    pps = float(ppm) / 60.0
                    pps_table = [(0.2, 1.0), (0.5, 0.85), (1.0, 0.65), (2.0, 0.35), (4.0, 0.15), (999.0, 0.05)]
                    base = pps_table[-1][1]
                    prev_t, prev_v = 0.0, pps_table[0][1]
                    for t, v in pps_table:
                        if pps <= t:
                            if t <= prev_t:
                                base = v
                            else:
                                frac = (pps - prev_t) / (t - prev_t)
                                base = prev_v + (v - prev_v) * frac
                            break
                        prev_t, prev_v = t, v

                    recent = [t for t in self.packet_ts_60s if t >= (now - 10.0)]
                    burst_mul = 1.0
                    if len(recent) >= 2:
                        recent.sort()
                        min_dt = min((recent[i] - recent[i - 1]) for i in range(1, len(recent)))
                        if min_dt < 0.5:
                            burst_mul = 0.7 + 0.3 * self._clamp01(min_dt / 0.5)
                    traffic_score = int(round(self._clamp01(base * burst_mul) * 100.0))

                ok_pb = max(0, int(self.crc_ok) - int(self.invalid_protobuf))
                bad_crc = int(self.crc_fail)
                bad_pb = int(self.invalid_protobuf)
                integrity_score = _dyn_score([(ok_pb / max(1.0, float(ok_pb + bad_crc + bad_pb)), 1.0)]) if (ok_pb + bad_crc + bad_pb) > 0 else 0

                snr_norm = None
                if snr_avg is not None:
                    snr_norm = self._clamp01((float(snr_avg) - (-20.0)) / (10.0 - (-20.0)))
                rssi_norm = None
                if rssi_avg is not None:
                    rssi_norm = self._clamp01((float(rssi_avg) - (-120.0)) / (-30.0 - (-120.0)))
                signal_score = _dyn_score([(snr_norm, 0.60), (rssi_norm, 0.40)])

                global_health_score = int(round((traffic_score + integrity_score + signal_score) / 3.0))

                traffic_level, traffic_color = _level4(traffic_score)
                integrity_level, integrity_color = _level4(integrity_score)
                signal_level, signal_color = _level4(signal_score)
                global_health_level, global_health_color = _health4(global_health_score)

            return {
                "started_ts": self.started_ts,
                "uptime_sec": max(0.0, now - self.started_ts),

                "total_packets": self.total_packets,
                "packets_per_minute": ppm,
                "active_nodes_5m": active_5m,
                "active_nodes_10m": active_10m,
                "new_nodes_last_hour": new_nodes_last_hour,
                "global_error_rate_pct": error_rate,

                "crc_ok": self.crc_ok,
                "crc_fail": self.crc_fail,
                "decrypt_ok": self.decrypt_ok,
                "decrypt_fail": self.decrypt_fail,
                "invalid_protobuf": self.invalid_protobuf,
                "unknown_portnum": self.unknown_portnum,

                "snr_avg": snr_avg,
                "rssi_avg": rssi_avg,
                "direct_ratio_pct": direct_ratio,
                "multihop_ratio_pct": multihop_ratio,
                "hop_avg": hop_avg,

                "channel_utilization_avg": cu_avg,
                "air_util_tx_max": au_max,
                "most_active_node": most_active_node,
                "most_active_node_packets": most_active_count,

                "mesh_traffic_score": traffic_score,
                "mesh_traffic_level": traffic_level,
                "mesh_traffic_color": traffic_color,
                "packet_integrity_score": integrity_score,
                "packet_integrity_level": integrity_level,
                "packet_integrity_color": integrity_color,
                "mesh_signal_score": signal_score,
                "mesh_signal_level": signal_level,
                "mesh_signal_color": signal_color,
                "mesh_health_score": global_health_score,
                "mesh_health_level": global_health_level,
                "mesh_health_color": global_health_color,
            }

    def sample_packets_per_minute(self, now: float | None = None) -> list[int]:
        with self._lock:
            if now is None:
                now = time.time()
            if not self.enabled and self.freeze_now is not None:
                return list(self.ppm_history)
            cutoff_60 = now - 60.0
            while self.packet_ts_60s and self.packet_ts_60s[0] < cutoff_60:
                self.packet_ts_60s.popleft()
            ppm = len(self.packet_ts_60s)
            self.ppm_history.append(int(ppm))
            return list(self.ppm_history)

    def to_dict(self) -> dict:
        snap = self.snapshot()
        series = self.sample_packets_per_minute()
        return {
            "version": 1,
            "snapshot": snap,
            "ppm_series": series,
        }

    def load_from_dict(self, data: dict | None):
        if not isinstance(data, dict):
            return
        snap = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
        series = data.get("ppm_series") if isinstance(data.get("ppm_series"), list) else []
        with self._lock:
            try:
                self.started_ts = float(snap.get("started_ts", time.time()))
            except Exception:
                self.started_ts = time.time()
            self.total_packets = int(snap.get("total_packets", 0) or 0)
            self.crc_ok = int(snap.get("crc_ok", 0) or 0)
            self.crc_fail = int(snap.get("crc_fail", 0) or 0)
            self.decrypt_ok = int(snap.get("decrypt_ok", 0) or 0)
            self.decrypt_fail = int(snap.get("decrypt_fail", 0) or 0)
            self.invalid_protobuf = int(snap.get("invalid_protobuf", 0) or 0)
            self.unknown_portnum = int(snap.get("unknown_portnum", 0) or 0)
            self.ppm_history = deque([int(x) for x in series if isinstance(x, (int, float))], maxlen=180)

state = AppState()
mesh_stats = MeshStatsManager()

status_label_ref = None
current_language = "en"
languages_data = {}
user_language_from_config = False
language_select_ref = None

def set_connection_status_ui(connected: bool, mode: str | None = None):
    global status_label_ref
    if status_label_ref is None:
        return
    if connected:
        if mode == "direct":
            status_label_ref.text = translate("status.connected_internal", "Connected (Internal)")
            status_label_ref.classes('font-bold mr-4 self-center')
        elif mode == "external":
            status_label_ref.text = translate("status.connected_external", "Connected (External)")
            status_label_ref.classes('font-bold mr-4 self-center')
        else:
            status_label_ref.text = translate("status.connected", "Connected")
        status_label_ref.classes(replace='text-green-500', remove='text-red-500').classes('font-bold mr-4 self-center')
    else:
        status_label_ref.text = translate("status.disconnected", "Disconnected")
        status_label_ref.classes(replace='text-red-500', remove='text-green-500').classes('font-bold mr-4 self-center')

def _shutdown_cleanup():
    if DEBUGGING:
        print("DEBUG: shutdown cleanup called", flush=True)
    try:
        if state.connect_mode == "direct":
            stop_engine_direct()
        state.connected = False
        state.connect_mode = None
    except Exception as e:
        if DEBUGGING:
            print(f"DEBUG: shutdown error {e}", flush=True)
        else:
            pass

atexit.register(_shutdown_cleanup)

# --- HELPER FUNCTIONS ---

def hexStringToBinary(hexString):
    try:
        return bytes.fromhex(hexString)
    except ValueError:
        return b''

def _i16_from_be(b0, b1):
        # Decode signed int16 from big-endian bytes
        v = (b0 << 8) | b1
        return v - 0x10000 if v & 0x8000 else v

def msb2lsb(msb):
    # Converts 32-bit ID from MSB (GnuRadio) to LSB (Meshtastic standard)
    if len(msb) < 8: return msb
    lsb = msb[6] + msb[7] + msb[4] + msb[5] + msb[2] + msb[3] + msb[0] + msb[1]
    return lsb

def parseAESKey(key_b64):
    try:
        # Default Key Handling
        # If user enters "AQ==" (which is technically just 0x01), treat it as the Meshtastic Default Channel Key
        if key_b64 in ["0", "NOKEY", "nokey", "NONE", "none", "HAM", "ham", "AQ==", "", "AA=="]:
            key_b64 = "1PG7OiApB1nwvP+rz05pAQ==" # The actual default AES256 key
        
        decoded = base64.b64decode(key_b64)
        if len(decoded) not in [16, 32]: # 128 or 256 bit
             log_to_console(f"Invalid Key Length: {len(decoded)}. Using default.")
             return base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")
        return decoded
    except Exception as e:
        log_to_console(f"Key Parse Error: {e}. Using default.")
        return base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")

def _xor_hash_bytes(data: bytes) -> int:
    h = 0
    for b in data or b"":
        h ^= int(b) & 0xFF
    return h & 0xFF

def _meshtastic_channel_hash(channel_name: str, key_bytes: bytes) -> int:
    name = (channel_name or "").strip()
    if not name:
        try:
            name = MESHTASTIC_MODEM_PRESETS.get(
                getattr(state, 'direct_preset', 'LONG_FAST'), {}
            ).get('channel_name', 'LongFast')
        except Exception:
            name = "LongFast"
    name_hash = _xor_hash_bytes(name.encode("utf-8", errors="ignore"))
    key_hash = _xor_hash_bytes(bytes(key_bytes or b""))
    return (name_hash ^ key_hash) & 0xFF

_extra_channel_keys_lock = threading.Lock()

def _get_extra_channel_keys() -> list[dict]:
    with _extra_channel_keys_lock:
        try:
            sig = tuple(
                (str(ch.get("id")), str(ch.get("name") or ""), str(ch.get("key_b64") or ""))
                for ch in (getattr(state, "extra_channels", None) or [])
                if isinstance(ch, dict) and ch.get("id") and ch.get("key_b64") is not None
            )
        except Exception:
            sig = ()

        if sig == getattr(state, "_extra_channel_keys_sig", None):
            return getattr(state, "_extra_channel_keys", []) or []

        entries = []
        for cid, name, key_b64 in sig:
            try:
                key_bytes = parseAESKey(key_b64)
                h = _meshtastic_channel_hash(name, key_bytes)
                entries.append({"id": cid, "hash": h, "key": key_bytes})
            except Exception:
                continue

        state._extra_channel_keys_sig = sig
        state._extra_channel_keys = entries
        return entries

def log_to_console(msg, style="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    state.new_logs.append(formatted_msg)
    state.logs.append(formatted_msg)

def get_languages_path():
    base = get_app_path()
    candidate = os.path.join(base, LANG_FILE_NAME)
    try:
        if os.path.isfile(candidate):
            return candidate
    except Exception:
        pass
    if getattr(sys, 'frozen', False):
        try:
            embedded = get_resource_path(LANG_FILE_NAME)
            if os.path.isfile(embedded):
                return embedded
        except Exception:
            pass
    return candidate

def load_languages():
    global languages_data
    path = get_languages_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            languages_data = json.load(f)
    except FileNotFoundError:
        languages_data = {}  # tollerable, hardcoded english fallback
    except json.JSONDecodeError as e:
        languages_data = {}
        show_fatal_error(
            f"{PROGRAM_NAME} — Language File Error",
            f"The language file is corrupted and cannot be parsed:\n{path}\n\nError: {e}\n\n"
            "The application will start in English.\n"
            "Please re-download the language file from the GitHub repository."
        )
    except Exception as e:
        languages_data = {}
        show_fatal_error(
            f"{PROGRAM_NAME} — Language File Error",
            f"Could not read the language file:\n{path}\n\nError: {e}\n\n"
            "The application will start in English."
        )

def get_available_languages():
    if not languages_data:
        return ["en"]
    return sorted(languages_data.keys())

def translate(key: str, default: str | None = None) -> str:
    lang = current_language if current_language in languages_data else "en"
    section = languages_data.get(lang) or languages_data.get("en") or {}
    value = section.get(key)
    if value is None:
        if default is not None:
            return default
        return key
    return value

def get_app_path():
    system = platform.system()
    exe_dir = os.path.dirname(sys.executable)

    if system == "Linux" and os.environ.get('APPIMAGE'):
        return os.path.dirname(os.environ.get('APPIMAGE'))

    if getattr(sys, 'frozen', False):
        if system == "Darwin":
            contents_dir = os.path.dirname(exe_dir)
            app_dir = os.path.dirname(contents_dir)
            parent_dir = os.path.dirname(app_dir)
            return parent_dir
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))

def get_data_path():
    base = get_app_path()
    data_dir = os.path.join(base, "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        pass
    return data_dir

def get_autosave_path():
    base = get_data_path()
    base_name = PROGRAM_NAME.replace(" ", "")
    filename = f"{base_name}-autosave.json"
    return os.path.join(base, filename)

def get_config_path():
    base = get_data_path()
    base_name = PROGRAM_NAME.replace(" ", "")
    filename = f"Config_{base_name}.json"
    return os.path.join(base, filename)

def load_user_config():
    try:
        path = get_config_path()
        if not os.path.isfile(path):
            return
        with open(path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        show_fatal_error(
            f"{PROGRAM_NAME} — Config File Error",
            f"The configuration file is corrupted and will be ignored:\n{get_config_path()}\n\n"
            f"Error: {e}\n\n"
            "Default settings will be used.\n"
            "You can delete/backup the config file and restart to reset all settings."
        )
        return
    except Exception as e:
        log_to_console(f"Config load error: {e}")
        return
    s = state
    v = data.get("direct_region")
    if isinstance(v, str):
        # EU_433, EU_868, US_915 -> US, ecc. (US_915 not exist anymore)
        _old_region_map = {"US_915": "US"}
        tv = _old_region_map.get(v, v)
        if tv in MESHTASTIC_REGIONS:
            s.direct_region = tv
    v = data.get("direct_preset")
    if isinstance(v, str):
        # Backward compatibility: map old preset names to new ones
        _old_to_new = {
            "Medium Fast": "MEDIUM_FAST",
            "Long Fast": "LONG_FAST",
            "Medium Slow": "MEDIUM_SLOW",
            "Long Slow (depr.)": "LONG_SLOW",
            "Long Moderate": "LONG_MODERATE",
            "Short Slow": "SHORT_SLOW",
            "Short Fast": "SHORT_FAST",
            "Short Turbo": "SHORT_TURBO",
        }
        _valid_presets = set(MESHTASTIC_MODEM_PRESETS.keys()) | {"ALL"}
        s.direct_preset = _old_to_new.get(v, v if v in _valid_presets else "LONG_FAST")
    v = data.get("direct_frequency_slot")
    if v is not None:
        try:
            s.direct_frequency_slot = int(v)
        except Exception:
            pass
    v = data.get("direct_channel_name")
    if isinstance(v, str):
        s.direct_channel_name = v
    v = data.get("direct_ppm")
    if v is not None:
        try:
            s.direct_ppm = int(v)
        except Exception:
            pass
    v = data.get("direct_gain")
    if v is not None:
        try:
            s.direct_gain = int(v)
        except Exception:
            pass
    v = data.get("direct_device_args")
    if isinstance(v, str):
        tv = v.strip()
        # Accept any valid osmosdr args pattern, just sanitize and store as-is
        known_drivers = r"\b(rtl|hackrf|bladerf|airspy|airspyhf|uhd|soapy|miri|redpitaya|file|rtl_tcp)\s*="
        if re.search(known_drivers, tv, flags=re.IGNORECASE):
            s.direct_device_args = tv
        elif tv == "":
            s.direct_device_args = ""
        else:
            # Unknown but non-empty: store as-is, engine will validate
            s.direct_device_args = tv
    v = data.get("direct_bias_tee")
    if isinstance(v, bool):
        s.direct_bias_tee = v
    v = data.get("direct_port")
    if isinstance(v, str):
        s.direct_port = v
    v = data.get("direct_key_b64")
    if isinstance(v, str):
        s.direct_key_b64 = v
    v = data.get("external_ip")
    if isinstance(v, str):
        s.external_ip = v
    v = data.get("external_port")
    if isinstance(v, str):
        s.external_port = v
    v = data.get("external_key_b64")
    if isinstance(v, str):
        s.external_key_b64 = v
    v = data.get("autosave_interval_sec")
    if v is not None:
        try:
            s.autosave_interval_sec = int(v)
        except Exception:
            pass
    v = data.get("verbose_logging")
    if isinstance(v, bool):
        s.verbose_logging = v
    v = data.get("theme")
    if isinstance(v, str):
        tv = v.strip().lower()
        if tv in ("auto", "dark", "light"):
            s.theme = "light" if tv == "auto" else tv
    v = data.get("language")
    if isinstance(v, str):
        global current_language, user_language_from_config
        current_language = v
        user_language_from_config = True
    v = data.get("map_center_lat")
    if v is not None:
        try:
            s.map_center_lat = float(v)
        except Exception:
            pass
    v = data.get("map_center_lng")
    if v is not None:
        try:
            s.map_center_lng = float(v)
        except Exception:
            pass
    v = data.get("map_zoom")
    if v is not None:
        try:
            s.map_zoom = int(v)
        except Exception:
            pass
    v = data.get("extra_channels")
    if isinstance(v, list):
        s.extra_channels = [
            ch for ch in v
            if isinstance(ch, dict) and 'id' in ch and 'name' in ch and 'key_b64' in ch
        ]
    v = data.get("channels_order")
    if isinstance(v, list):
        s.channels_order = [x for x in v if isinstance(x, str)]

    v = data.get("network_mode")
    if v in ("meshtastic", "meshcore"):
        s.network_mode = v
    v = data.get("meshcore_region")
    if isinstance(v, str):
        s.meshcore_region = v
    v = data.get("meshcore_preset")
    if isinstance(v, str):
        s.meshcore_preset = v
    for field in ["meshcore_custom_freq", "meshcore_custom_sf", "meshcore_custom_bw",
                  "meshcore_custom_cr", "meshcore_custom_sync_word", "meshcore_custom_preamble_len"]:
        v = data.get(field)
        if v is not None:
            try:
                setattr(s, field, type(getattr(s, field))(v))
            except Exception:
                pass

def save_user_config():
    try:
        path = get_config_path()
        data = {
            "direct_region": state.direct_region,
            "direct_preset": state.direct_preset,
            "direct_frequency_slot": state.direct_frequency_slot,
            "direct_channel_name": state.direct_channel_name,
            "direct_ppm": state.direct_ppm,
            "direct_gain": state.direct_gain,
            "direct_device_args": getattr(state, "direct_device_args", "rtl=0"),
            "direct_bias_tee": getattr(state, "direct_bias_tee", False),
            "direct_port": state.direct_port,
            "direct_key_b64": state.direct_key_b64,
            "external_ip": state.external_ip,
            "external_port": state.external_port,
            "external_key_b64": state.external_key_b64,
            "autosave_interval_sec": state.autosave_interval_sec,
            "verbose_logging": state.verbose_logging,
            "theme": getattr(state, "theme", "light"),
            "language": current_language,
            "map_center_lat": getattr(state, "map_center_lat", None),
            "map_center_lng": getattr(state, "map_center_lng", None),
            "map_zoom": getattr(state, "map_zoom", None),
            "extra_channels": getattr(state, 'extra_channels', []),
            "channels_order": getattr(state, 'channels_order', []),
            "network_mode": state.network_mode,
            "meshcore_region": state.meshcore_region,
            "meshcore_preset": state.meshcore_preset,
            "meshcore_custom_freq": state.meshcore_custom_freq,
            "meshcore_custom_sf": state.meshcore_custom_sf,
            "meshcore_custom_bw": state.meshcore_custom_bw,
            "meshcore_custom_cr": state.meshcore_custom_cr,
            "meshcore_custom_sync_word": state.meshcore_custom_sync_word,
            "meshcore_custom_preamble_len": state.meshcore_custom_preamble_len,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log_to_console(f"Config save error: {e}")

def safe_open_url(url):
    """Open URL in browser with AppImage environment cleaned."""
    import subprocess
    clean_env = os.environ.copy()
    # Remove environment variables that block system processes
    for var in [
        "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "GIO_MODULE_DIR",
        "QT_QPA_PLATFORMTHEME", "QT_QPA_PLATFORM", "QT_PLUGIN_PATH",
        "QT_OPENGL", "LIBGL_ALWAYS_SOFTWARE", "QTWEBENGINE_CHROMIUM_FLAGS",
        "QT_LOGGING_RULES", "GSETTINGS_BACKEND", "GDK_BACKEND",
        "PYWEBVIEW_GUI", "QT_VIDEO_BACKEND",
    ]:
        clean_env.pop(var, None)
    if "SYSTEM_LD_LIBRARY_PATH" in os.environ:
        clean_env["LD_LIBRARY_PATH"] = os.environ["SYSTEM_LD_LIBRARY_PATH"]
    
    try:
        if platform.system() == "Linux":
            # Try xdg-open first, then fallback to other openers
            openers = ["xdg-open", "gio open", "gnome-open", "kde-open", "firefox", "chromium", "xdg-open"]
            opened = False
            for opener in ["xdg-open", "gio", "gnome-open", "kde-open5", "kde-open"]:
                try:
                    if opener == "gio":
                        subprocess.Popen(["gio", "open", url], env=clean_env, start_new_session=True)
                    else:
                        subprocess.Popen([opener, url], env=clean_env, start_new_session=True)
                    opened = True
                    break
                except FileNotFoundError:
                    continue
                except Exception as e:
                    if DEBUGGING: print(f"DEBUG: {opener} failed: {e}")
                    continue
            if not opened:
                import webbrowser
                webbrowser.open(url)
        else:
            import webbrowser
            webbrowser.open(url)
    except Exception as e:
        if DEBUGGING: print(f"DEBUG: Failed to open URL: {e}")

def check_native_runtime_deps() -> bool:
    import ctypes
    system = platform.system()
    if DEBUGGING: print(f"DEBUG: Checking deps for {system}")

    if system == "Windows":
        win_ver = sys.getwindowsversion()
        if win_ver.major < 10 or (win_ver.major == 10 and win_ver.build < 17763):
            show_fatal_error(
                f"{PROGRAM_NAME} — Unsupported Windows Version",
                f"Your Windows version (build {win_ver.build}) is not supported.\n\n"
                f"This application requires Windows 10 version 1809 (build 17763) (2018) or later.\n\n"
                f"Please update Windows via Settings → Update & Security → Windows Update."
            )
            return False
        webview2_missing = True
        try:
            import glob as _glob
            _webview2_paths = [
                r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application",
                r"C:\Program Files\Microsoft\EdgeWebView\Application",
                r"C:\Program Files (x86)\Microsoft\Edge\Application",
                r"C:\Program Files\Microsoft\Edge\Application",
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "EdgeWebView", "Application"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application"),
            ]
            for _base in _webview2_paths:
                if not os.path.isdir(_base):
                    continue
                for _v in _glob.glob(os.path.join(_base, "*")):
                    for _exe in ("msedgewebview2.exe", "msedge.exe"):
                        if os.path.isfile(os.path.join(_v, _exe)):
                            webview2_missing = False
                            break
                    if not webview2_missing:
                        break
                if not webview2_missing:
                    break

            # Fallback: check registry if WebView2 is installed
            if webview2_missing:
                import winreg as _winreg
                _reg_paths = [
                    (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
                    (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
                    (_winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
                    (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}"),
                    (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}"),
                ]
                for _hive, _path in _reg_paths:
                    try:
                        k = _winreg.OpenKey(_hive, _path)
                        _winreg.CloseKey(k)
                        webview2_missing = False
                        break
                    except OSError:
                        continue
        except Exception:
            webview2_missing = True

        if webview2_missing:
            show_fatal_error(
                f"{PROGRAM_NAME} — Missing WebView2 Runtime",
                f"Microsoft Edge WebView2 Runtime is not installed.\n\n"
                f"This component is required to display the application interface.\n\n"
                f"Please download and install it from:\n"
                f"https://go.microsoft.com/fwlink/p/?LinkId=2124703\n"
                f"(click 'Download' under 'Evergreen Bootstrapper' id needed)\n\n"
                f"After installation, restart {PROGRAM_NAME}."
            )
            return False
        return True

    if system == "Linux":
        import shutil
        # Required shared libs for QtWebEngine/Qt backend on Linux (Raspberry etc.)
        required = [
            ("libxcb-cursor.so.0", "libxcb-cursor0"),
            ("libminizip.so.1", "libminizip1"),
        ]

        missing = []
        for soname, pkg in required:
            try:
                ctypes.CDLL(soname)
            except OSError:
                missing.append((soname, pkg))

        if not missing:
            if DEBUGGING: print("DEBUG: All Linux native deps found.")
            return True
        
        # --- Detect distribution and install missing packages ---
        if shutil.which("dnf"):       # Fedora / RHEL
            pkg_list = "xcb-util-cursor minizip"
            install_cmd = "sudo dnf install -y"
        elif shutil.which("pacman"):  # Arch Linux
            pkg_list = "xcb-util-cursor minizip"
            install_cmd = "sudo pacman -S --noconfirm"
        else:                         # Debian / Ubuntu / Mint (Fallback)
            pkg_list = "libxcb-cursor0 libminizip1"
            install_cmd = "sudo apt-get update && sudo apt-get install -y"

        soname_list = ", ".join(s for s, p in missing)

        script = (
            f"echo 'Missing system libraries: {soname_list}'; "
            "echo; "
            "echo 'These packages are required to run the native GUI.'; "
            "echo; "
            f"echo '  {install_cmd} {pkg_list}'; "
            "echo; "
            "read -p 'Install now? [Y/n] ' ans; "
            "if [ \"$ans\" = \"\" ] || [ \"$ans\" = \"y\" ] || [ \"$ans\" = \"Y\" ]; then "
            f"  {install_cmd} {pkg_list}; "
            "fi; "
            "echo; "
            "read -n1 -r -p 'Press any key to close this window...' key"
        )

        if DEBUGGING: print(f"DEBUG: Missing deps: {missing}")

        msg = (
            f"Missing system libraries: {soname_list}\n"
            "These packages are required to run the native GUI on Linux.\n"
            "Install them with:\n\n"
            f"{install_cmd} {pkg_list}\n\n"
            "(or equivalent for your system) Then restart this application.\n"
            "For more information visit our wiki at:\n"
            f"{GITHUB_URL}/wiki/English#linux-1"
        )

        if getattr(sys, "frozen", False):
            # prepare clean environment for terminal prompt
            clean_env = os.environ.copy()
            # Remove AppImage variables
            for var in ["LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "GIO_MODULE_DIR"]:
                clean_env.pop(var, None)
            # restore LD_LIBRARY_PATH origin saved by AppRun if exists
            if "SYSTEM_LD_LIBRARY_PATH" in os.environ:
                clean_env["LD_LIBRARY_PATH"] = os.environ["SYSTEM_LD_LIBRARY_PATH"]
            # Try to show a friendly installer prompt in a terminal first
            clean_env = os.environ.copy()
            for var in ["LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "GIO_MODULE_DIR"]:
                clean_env.pop(var, None)
            if "SYSTEM_LD_LIBRARY_PATH" in os.environ:
                clean_env["LD_LIBRARY_PATH"] = os.environ["SYSTEM_LD_LIBRARY_PATH"]

            launched = False

            terminal_attempts = [
                ["x-terminal-emulator", "-e", "bash", "-lc", script],
                ["xterm", "-e", "bash", "-lc", script],
                # gnome-terminal prefers "--" on many distros
                ["gnome-terminal", "--", "bash", "-lc", script],
                ["konsole", "-e", "bash", "-lc", script],
            ]

            for cmd in terminal_attempts:
                try:
                    subprocess.Popen(cmd, env=clean_env)
                    launched = True
                    break
                except Exception:
                    continue

            if not launched:
                # Fall back to GUI dialogs if terminals are not available
                try:
                    show_fatal_error(f"{PROGRAM_NAME} — Missing libraries", msg)
                    launched = True
                except Exception:
                    pass

            if not launched:
                print(msg, file=sys.stderr)
        else:
            print(msg, file=sys.stderr)

        return False
    # Others/nothing

    return True

def get_resource_path(relative_path):
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))

    # Check for PyInstaller 6+ _internal directory in onedir mode
    path = os.path.join(base_path, relative_path)
    if not os.path.exists(path):
        _internal = os.path.join(base_path, "_internal", relative_path)
        if os.path.exists(_internal):
            return _internal
    return path

# --- MESHTASTIC DECODING LOGIC ---

def dataExtractor(data_hex):
    # Expected min length: dest(8) + sender(8) + packetID(8) + flags(2) + hash(2) + reserved(4) = 32 chars
    if len(data_hex) < 32:
        raise ValueError(f"Packet too short: {len(data_hex)} chars")

    try:
        meshPacketHex = {
            'dest' : hexStringToBinary(data_hex[0:8]),
            'sender' : hexStringToBinary(data_hex[8:16]),
            'packetID' : hexStringToBinary(data_hex[16:24]),
            'flags' : hexStringToBinary(data_hex[24:26]),
            'channelHash' : hexStringToBinary(data_hex[26:28]),
            'reserved' : hexStringToBinary(data_hex[28:32]),
            'data' : hexStringToBinary(data_hex[32:])
        }
        return meshPacketHex
    except Exception as e:
        raise ValueError(f"Extraction failed: {e}")

def dataDecryptor(meshPacketHex, aesKey):
    # Nonce must be 16 bytes.
    # Structure: packetID (4) + 0000 (4) + sender (4) + 0000 (4)
    
    p_id = meshPacketHex['packetID']
    sender = meshPacketHex['sender']
    
    # Ensure 4 bytes each
    if len(p_id) < 4: p_id = p_id.rjust(4, b'\x00')
    if len(sender) < 4: sender = sender.rjust(4, b'\x00')
    
    aesNonce = p_id + b'\x00\x00\x00\x00' + sender + b'\x00\x00\x00\x00'
    
    if len(aesNonce) != 16:
        raise ValueError(f"Invalid nonce size constructed: {len(aesNonce)}")

    cipher = Cipher(algorithms.AES(aesKey), modes.CTR(aesNonce), backend=default_backend())
    decryptor = cipher.decryptor()
    decryptedOutput = decryptor.update(meshPacketHex['data']) + decryptor.finalize()
    return decryptedOutput

def update_node(node_id, **kwargs):
    node_id = str(node_id)
    if not node_id.startswith("!"):
        try:
            node_id = f"!{int(node_id, 16):x}"
        except Exception:
            try:
                node_id = f"!{int(node_id):x}"
            except Exception:
                pass

    is_new = node_id not in state.nodes
    now_ts = time.time()
    
    if is_new:
        state.nodes[node_id] = {
            "id": node_id, 
            "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen_ts": now_ts,
            "lat": None, "lon": None, "location_source": "Unknown", "altitude": None,
            "short_name": "???", "long_name": "Unknown",
            "hw_model": "Unknown", "role": "Unknown",
            "public_key": None, "macaddr": None, "is_unmessagable": False,
            "battery": None, "voltage": None,
            "snr": None, "rssi": None,
            "snr_indirect": None, "rssi_indirect": None,
            "hops": None, "hop_label": None,
            "temperature": None, "relative_humidity": None, "barometric_pressure": None,
            "channel_utilization": None, "air_util_tx": None, "uptime_seconds": None,
            "preset": None,
        }
        state.nodes_updated = True
        state.nodes_list_updated = True # Ensure new nodes appear immediately
        log_to_console(f"New node {node_id}")

    if "hops" in kwargs:
        new_hops = kwargs.get("hops")
        if new_hops is None:
            kwargs.pop("hops", None)
            kwargs.pop("hop_label", None)
        else:
            prev_hops = state.nodes[node_id].get("hops")
            prev_ts = state.nodes[node_id].get("last_seen_ts")
            if prev_hops is not None and prev_ts is not None:
                window = 10 * 60
                if now_ts - prev_ts < window and new_hops > prev_hops:
                    kwargs.pop("hops", None)
                    kwargs.pop("hop_label", None)
    
    # Check if anything actually changed to avoid redundant updates
    changed = False
    
    # If it's a new node, we definitely have changes (initial values)
    if is_new:
        changed = True
        
    for k, v in kwargs.items():
        if state.nodes[node_id].get(k) != v:
            state.nodes[node_id][k] = v
            changed = True
    
    # Always update last seen
    state.nodes[node_id]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.nodes[node_id]["last_seen_ts"] = now_ts
    
    # Force update if new data arrived or it's a new node
    # We also want to update the grid for "last_seen" changes to show real-time activity
    if changed or True: # Force update on every packet for real-time "Last Seen"
        state.nodes_updated = True
        with state.lock:
            state.dirty_nodes.add(node_id) # Track specific node for efficient delta update
        state.nodes_list_updated = True
        
        # If name changed, we might need to refresh chat history to reflect new name
        if changed and ('short_name' in kwargs or 'long_name' in kwargs):
            state.chat_force_refresh = True

def resolveMeshCoreIdentity(node_hash):
    # node_hash is 1 byte int
    target_hex = f"{node_hash:02x}"
    for nid, n in state.nodes.items():
        if nid.startswith(target_hex):
            return n.get('long_name', nid), nid
    return f"Node {target_hex}", target_hex

def decodeMeshCore(packet_bytes, snr=None, rssi=None):
    if not packet_bytes or len(packet_bytes) < 2:
        return None

    header = packet_bytes[0]
    # version = (header >> 6) & 0x03
    payload_type = (header >> 2) & 0x0F
    route_type = header & 0x03

    offset = 1
    if route_type in (ROUTE_TYPE_TRANSPORT_FLOOD, ROUTE_TYPE_TRANSPORT_DIRECT):
        if len(packet_bytes) < offset + 4: return None
        # transport_codes = packet_bytes[offset:offset+4]
        offset += 4

    if len(packet_bytes) < offset + 1: return None
    path_len_byte = packet_bytes[offset]
    offset += 1

    hop_count = path_len_byte & 0x3F
    hash_size = ((path_len_byte >> 6) & 0x03) + 1

    path_bytes_len = hop_count * hash_size
    if len(packet_bytes) < offset + path_bytes_len: return None
    path_data = packet_bytes[offset:offset+path_bytes_len]
    offset += path_bytes_len

    payload = packet_bytes[offset:]

    # Process Payloads
    if payload_type == PAYLOAD_TYPE_ADVERT:
        if len(payload) < 32 + 4 + 64: return None
        pubkey = payload[0:32]
        # timestamp = int.from_bytes(payload[32:36], 'little')
        # signature = payload[36:100]
        appdata = payload[100:]

        node_id = pubkey.hex()
        short_id = node_id[:2]

        name = "Unknown"
        lat = None
        lon = None
        role_label = translate("meshcore.role.companion", "Companion")
        role_internal = "Companion"
        relay_capable = False

        if len(appdata) > 0:
            flags = appdata[0]
            ptr = 1
            if flags & 0x10: # has location
                if len(appdata) >= ptr + 8:
                    lat = int.from_bytes(appdata[ptr:ptr+4], 'little', signed=True) / 1_000_000.0
                    lon = int.from_bytes(appdata[ptr+4:ptr+8], 'little', signed=True) / 1_000_000.0
                    ptr += 8
            if flags & 0x20: ptr += 2 # feature 1
            if flags & 0x40: ptr += 2 # feature 2
            if flags & 0x80: # has name
                name = appdata[ptr:].decode('utf-8', errors='ignore')

            type_bits = flags & 0x07
            if type_bits == 0x02:
                role_label = translate("meshcore.role.repeater", "Repeater")
                role_internal = "Repeater"
                relay_capable = True
            elif type_bits == 0x03:
                role_label = translate("meshcore.role.room_server", "Room Server")
                role_internal = "Room Server"
                relay_capable = True
            elif type_bits == 0x04:
                role_label = translate("meshcore.role.sensor", "Sensor")
                role_internal = "Sensor"

        # Store path info if this advert came through repeaters
        path_list = []
        if hop_count > 0:
            for i in range(hop_count):
                h = path_data[i*hash_size : (i+1)*hash_size]
                path_list.append(h.hex())

        update_node(node_id, short_name=short_id, long_name=name, hw_model=role_label, role=role_label, lat=lat, lon=lon, public_key=node_id, role_id=role_internal)
        state.nodes[node_id]["relay_capable"] = relay_capable
        state.nodes[node_id]["last_path"] = path_list

        log_to_console(f"[MESHCORE] ADVERT from {name} ({short_id}) - Role: {role_internal}")

    elif payload_type == PAYLOAD_TYPE_TXT_MSG:
        if len(payload) >= 4:
            dest_hash = payload[0]
            src_hash = payload[1]

            src_name, src_id = resolveMeshCoreIdentity(src_hash)
            dest_name, dest_id = resolveMeshCoreIdentity(dest_hash)

            now_dt = datetime.now()
            msg_obj = {
                "time": now_dt.strftime("%H:%M"),
                "date": now_dt.strftime("%d/%m/%Y"),
                "from": src_name,
                "from_id": src_id,
                "to": dest_id,
                "text": translate("meshcore.message.encrypted", "[Encrypted Message]"),
                "is_me": False,
            }
            state.messages.append(msg_obj)
            state.new_messages.append(msg_obj)

            log_to_console(f"[MESHCORE] TXT from {src_id} to {dest_id} ({msg_obj['text']})")

    elif payload_type == PAYLOAD_TYPE_GRP_TXT:
        if len(payload) >= 1:
            # channel_hash = payload[0]
            now_dt = datetime.now()
            msg_obj = {
                "time": now_dt.strftime("%H:%M"),
                "date": now_dt.strftime("%d/%m/%Y"),
                "from": "Group",
                "from_id": "group",
                "to": "All",
                "text": translate("meshcore.message.group_encrypted", "[Encrypted Group Message]"),
                "is_me": False,
            }
            state.messages.append(msg_obj)
            state.new_messages.append(msg_obj)
            log_to_console(f"[MESHCORE] GRP_TXT ({msg_obj['text']})")

    return True

def decodeProtobuf(packetData, sourceID, destID, cryptplainprefix, *, count_invalid: bool = True, preset_name: str | None = None, channel_hash: int | None = None, forced_channel_id: str | None = None, packet_id: bytes | None = None):
    try:
        data = mesh_pb2.Data()
        data.ParseFromString(packetData)
    except Exception as e:
        if count_invalid:
            mesh_stats.on_invalid_protobuf()
        return f"INVALID PROTOBUF: {e}"

    log_msg = ""
    decoded_obj = None # Store the parsed protobuf object for verbose logging

    msg_id = None
    try:
        if hasattr(data, "id"):
            msg_id = int(data.id)
        elif hasattr(data, "message_id"):
            msg_id = int(data.message_id)
    except Exception:
        msg_id = None

    try:
        supported_portnums = set(int(v) for v in mesh_pb2.PortNum.values())
    except Exception:
        supported_portnums = {1, 3, 4, 67, 70}
    try:
        mesh_stats.on_portnum_seen(int(data.portnum), int(data.portnum) in supported_portnums)
    except Exception:
        pass

    if data.portnum == 1: # TEXT_MESSAGE_APP
        text = data.payload.decode('utf-8', errors='ignore')
        
        if packet_id is not None and len(packet_id) > 0:
            dedup_key = (sourceID, "PID", bytes(packet_id))
        elif msg_id is not None and msg_id != 0:
            dedup_key = (sourceID, "MID", msg_id)
        else:
            dedup_key = (sourceID, text)
        if dedup_key in state.seen_packets:
            log_msg = f"{cryptplainprefix} DUPLICATE TEXT from {sourceID} (Ignored)"
            # We can return a log msg for debug (or could be empty string to hide completely)
            # We do NOT append to state.messages or state.new_messages
            # If you want to show it in console for logging: return log_msg
            return "" 
            
        state.seen_packets.append(dedup_key)

        # Determine Sender Name
        sender_name = sourceID
        if sourceID in state.nodes:
            n = state.nodes[sourceID]
            s_name = n.get('short_name', '???')
            l_name = n.get('long_name', 'Unknown')
            
            has_short = s_name and s_name != "???"
            has_long = l_name and l_name != "Unknown"
            
            if has_long and has_short:
                sender_name = f"{l_name} ({s_name})"
            elif has_short:
                sender_name = s_name
            elif has_long:
                sender_name = l_name

        now_dt = datetime.now()
        msg_obj = {
            "time": now_dt.strftime("%H:%M"),
            "date": now_dt.strftime("%d/%m/%Y"),
            "from": sender_name,
            "from_id": sourceID,
            "to": destID,
            "text": text,
            "is_me": False,
            "preset": preset_name
        }

        # calculate default channel hash
        default_ch_name = getattr(state, 'direct_channel_name', '') or ''
        if not default_ch_name:
            # use preset name as Meshtastic does
            from meshtastic import mesh_pb2 as _mpb
            default_ch_name = MESHTASTIC_MODEM_PRESETS.get(
                getattr(state, 'direct_preset', 'LONG_FAST'), {}
            ).get('channel_name', 'LongFast')
        default_hash = _meshtastic_channel_hash(default_ch_name, getattr(state, "aes_key_bytes", b""))

        # Route message to correct channel
        routed = False
        if forced_channel_id:
            for ch in state.extra_channels:
                if ch.get('id') == forced_channel_id:
                    ch_id = forced_channel_id
                    if ch_id not in state.channel_messages:
                        state.channel_messages[ch_id] = deque(maxlen=100)
                    state.channel_messages[ch_id].append(msg_obj)
                    if state.active_channel_id != ch_id:
                        state.channel_unread[ch_id] = True
                        try:
                            state.channel_unread_count[ch_id] = int(state.channel_unread_count.get(ch_id, 0)) + 1
                        except Exception:
                            state.channel_unread_count[ch_id] = 1
                    routed = True
                    break

        if not routed and channel_hash is not None:
            for ent in _get_extra_channel_keys():
                if ent.get("hash") == channel_hash:
                    ch_id = str(ent.get("id") or "")
                    if not ch_id:
                        continue
                    if ch_id not in state.channel_messages:
                        state.channel_messages[ch_id] = deque(maxlen=100)
                    state.channel_messages[ch_id].append(msg_obj)
                    if state.active_channel_id != ch_id:
                        state.channel_unread[ch_id] = True
                        try:
                            state.channel_unread_count[ch_id] = int(state.channel_unread_count.get(ch_id, 0)) + 1
                        except Exception:
                            state.channel_unread_count[ch_id] = 1
                    routed = True
                    break

        # If not routed to extra channel, route to default channel
        if not routed:
            state.messages.append(msg_obj)
            state.new_messages.append(msg_obj)
            if state.active_channel_id != 'default':
                state.channel_unread['default'] = True
                try:
                    state.channel_unread_count['default'] = int(state.channel_unread_count.get('default', 0)) + 1
                except Exception:
                    state.channel_unread_count['default'] = 1

        log_msg = f"{cryptplainprefix} TEXT MSG from {sourceID}: {text}"
        update_node(sourceID)
        
    elif data.portnum == 3: # POSITION_APP
        pos = mesh_pb2.Position()
        try:
            pos.ParseFromString(data.payload)
        except Exception as e:
            log_to_console(f"POSITION parse error from {sourceID}: {e}")
            return ""
        decoded_obj = pos
        lat = pos.latitude_i * 1e-7
        lon = pos.longitude_i * 1e-7
        altitude_m = None

        try:
            for desc, value in pos.ListFields():
                if desc.name in ('altitude', 'altitude_m'):
                    altitude_m = value
                    break
        except Exception:
            altitude_m = None
        
        loc_source = "Unknown"
        try:
            val = pos.location_source
            # Robust way to get Enum Name using the object's descriptor
            # This avoids issues with checking hasattr on scalar fields (which always exist in proto3)
            # and avoids hardcoding the class path if it varies by protobuf version.
            # this also ensure some future and retro compatibility.
            loc_source = pos.DESCRIPTOR.fields_by_name['location_source'].enum_type.values_by_number[val].name
        except Exception as e:
            loc_source = f"Enum_{pos.location_source}"
            print(f"Error extracting LocationSource: {e}")

        kwargs = {"lat": lat, "lon": lon, "location_source": loc_source}
        if altitude_m is not None:
            kwargs["altitude"] = altitude_m
        update_node(sourceID, **kwargs)
        log_msg = f"{cryptplainprefix} POSITION from {sourceID}: {lat}, {lon} ({loc_source})"
        
    elif data.portnum == 4: # NODEINFO_APP
        info = mesh_pb2.User()
        try:
            info.ParseFromString(data.payload)
        except Exception as e:
            log_to_console(f"NODEINFO parse error from {sourceID}: {e}")
            return ""
        decoded_obj = info
        
        role_name = "Unknown"
        try:
            if hasattr(info, 'role'):
                role_name = config_pb2.Config.DeviceConfig.Role.Name(info.role)
        except Exception:
            pass

        hw_model_name = "Unknown"
        try:
            if hasattr(info, 'hw_model'):
                hw_model_name = mesh_pb2.HardwareModel.Name(info.hw_model)
        except Exception:
            # Fallback if enum value is not known (e.g. newer firmware)
            hw_model_name = f"Model_{info.hw_model}"

        public_key = None
        try:
            pk = getattr(info, 'public_key', None)
            if isinstance(pk, (bytes, bytearray)) and pk:
                public_key = base64.b64encode(bytes(pk)).decode('ascii')
            elif isinstance(pk, str) and pk:
                s = pk.strip()
                if len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s):
                    public_key = base64.b64encode(bytes.fromhex(s)).decode('ascii')
                else:
                    public_key = s
        except Exception:
            public_key = None

        macaddr = None
        try:
            mac_val = getattr(info, 'macaddr', None)
            if isinstance(mac_val, (bytes, bytearray)) and len(mac_val) == 6:
                macaddr = ":".join(f"{b:02x}" for b in mac_val)
            elif isinstance(mac_val, int) and mac_val:
                mac_bytes = mac_val.to_bytes(6, byteorder="big", signed=False)
                macaddr = ":".join(f"{b:02x}" for b in mac_bytes)
            elif isinstance(mac_val, str) and mac_val:
                macaddr = mac_val
        except Exception:
            macaddr = None

        is_unmessagable = None
        for field_name in ("is_unmessagable", "is_unmessageable"):
            try:
                if hasattr(info, field_name) and bool(getattr(info, field_name)):
                    is_unmessagable = True
                    break
            except Exception:
                pass

        nodeinfo_kwargs = {
            "short_name": info.short_name,
            "long_name": info.long_name,
            "hw_model": hw_model_name,
            "role": role_name,
        }
        if public_key is not None:
            nodeinfo_kwargs["public_key"] = public_key
        if macaddr is not None:
            nodeinfo_kwargs["macaddr"] = macaddr
        if is_unmessagable is not None:
            nodeinfo_kwargs["is_unmessagable"] = is_unmessagable

        update_node(sourceID, **nodeinfo_kwargs)
        log_msg = f"{cryptplainprefix} NODEINFO from {sourceID}: {info.short_name} ({info.long_name})"
        
    elif data.portnum == 67: # TELEMETRY_APP
        tel = telemetry_pb2.Telemetry()
        try:
            tel.ParseFromString(data.payload)
        except Exception as e:
            log_to_console(f"TELEMETRY parse error from {sourceID}: {e}")
            return ""
        decoded_obj = tel
        metrics = {}
        
        # Use ListFields to only capture present fields (avoiding 0 for missing fields)
        if tel.HasField('device_metrics'):
            for desc, value in tel.device_metrics.ListFields():
                if desc.name == 'battery_level':
                    metrics['battery'] = value
                elif desc.name == 'voltage':
                    metrics['voltage'] = value
                elif desc.name == 'channel_utilization':
                    metrics['channel_utilization'] = value
                elif desc.name == 'air_util_tx':
                    metrics['air_util_tx'] = value
                elif desc.name == 'uptime_seconds':
                    metrics['uptime_seconds'] = value
            
        if tel.HasField('environment_metrics'):
            for desc, value in tel.environment_metrics.ListFields():
                if desc.name == 'temperature':
                    metrics['temperature'] = value
                elif desc.name == 'relative_humidity':
                    metrics['relative_humidity'] = value
                elif desc.name == 'barometric_pressure':
                    metrics['barometric_pressure'] = value
            
        update_node(sourceID, **metrics)
        try:
            mesh_stats.on_telemetry(sourceID, metrics)
        except Exception:
            pass
        log_msg = f"{cryptplainprefix} TELEMETRY from {sourceID}"
        
    elif data.portnum == 70: # TRACEROUTE
        route = mesh_pb2.RouteDiscovery()
        try:
            route.ParseFromString(data.payload)
        except Exception as e:
            log_to_console(f"TRACEROUTE parse error from {sourceID}: {e}")
            return ""
        decoded_obj = route
        log_msg = f"{cryptplainprefix} TRACEROUTE from {sourceID}"
        update_node(sourceID) # Update last seen for traceroute source
        
    else:
        log_msg = f"{cryptplainprefix} APP Packet ({data.portnum}) from {sourceID}"
        update_node(sourceID)

    if state.verbose_logging and decoded_obj:
        try:
            # Append clean protobuf string representation
            log_msg += f"\n{decoded_obj}"
        except:
            pass

    return log_msg

# --- FRAME PARSER ---
def parse_framed_stream_bytes(rx_buf: bytearray):
    # Parse frames [type:1][len:2][payload:len] from rx_buf and process them.

    while True:
        if len(rx_buf) < 3:
            return

        ftype = rx_buf[0]
        flen = (rx_buf[1] << 8) | rx_buf[2]

        if len(rx_buf) < 3 + flen:
            return

        body = bytes(rx_buf[3:3 + flen])
        del rx_buf[:3 + flen]

        state.raw_packet_count += 1
        state.last_rx_ts = time.time()
        state.rx_seen_once = True

        # --- Frame type 0x03: Unified (payload + optional metrics) ---
        if ftype == 0x03:
            try:
                if len(body) < 2 + 1 + 4:
                    # payload_len(2) + flags(1) + snr_i16(2) + rssi_i16(2)
                    raise ValueError(f"Unified frame too short: {len(body)} bytes")

                payload_len = (body[0] << 8) | body[1]
                need_min = 2 + payload_len + 1 + 4
                if len(body) < need_min:
                    raise ValueError(f"Unified frame truncated: have {len(body)} need {need_min}")

                payload = body[2:2 + payload_len]

                flags_off = 2 + payload_len
                flags = body[flags_off]

                snr10 = _i16_from_be(body[flags_off + 1], body[flags_off + 2])
                rssi10 = _i16_from_be(body[flags_off + 3], body[flags_off + 4])

                has_metrics = (flags & 0x01) != 0
                snr_val = (snr10 / 10.0) if has_metrics else None
                rssi_val = (rssi10 / 10.0) if has_metrics else None
                # preset_id: optional trailing byte (retrocompatible)
                preset_id_off = flags_off + 5  # flags(1)+snr(2)+rssi(2)
                frame_preset_id = body[preset_id_off] if len(body) > preset_id_off else 0
                frame_preset_name = PRESET_ID_MAP.get(frame_preset_id)

                if state.network_mode == "meshcore":
                    decodeMeshCore(payload, snr=snr_val, rssi=rssi_val)
                    continue

                # 1) Extract Meshtastic fields
                extracted = dataExtractor(payload.hex())

                # Hop parsing
                hops_val = None
                hop_label = None
                try:
                    flags_bytes = extracted.get('flags', b'')
                    if flags_bytes:
                        fb = flags_bytes[0]
                        hop_limit = fb & 0x07
                        hop_start = (fb >> 5) & 0x07
                        hops_val = hop_start - hop_limit
                        if hops_val < 0:
                            hops_val = 0
                        hop_label = "direct" if hops_val == 0 else str(hops_val)
                except Exception as e:
                    log_to_console(f"Hop parse error: {e}")

                try:
                    if mesh_stats.consume_crc_invalid_packet(extracted.get("sender"), extracted.get("packetID")):
                        mesh_stats.on_packet_received(None, hops_val, snr_val, rssi_val)
                        continue
                except Exception:
                    pass

                mesh_stats.on_frame_ok()

                # Channel hash parsing
                ch_hash_byte = extracted.get('channelHash', None)
                channel_hash_int = ch_hash_byte[0] if isinstance(ch_hash_byte, (bytes, bytearray)) and ch_hash_byte else None

                # 2) Decode IDs Before decrypting
                s_id = msb2lsb(extracted['sender'].hex())
                d_id = msb2lsb(extracted['dest'].hex())
                s_id_fmt = f"!{int(s_id, 16):x}"
                d_id_fmt = f"!{int(d_id, 16):x}"

                try:
                    mesh_stats.on_packet_received(s_id_fmt, hops_val, snr_val, rssi_val)
                except Exception:
                    pass
                
                # 3) Try decrypt first, then fallback to plaintext if parsing fails
                #    (This approach is more robust than relying on channelHash alone,
                #     ensuring compatibility even if the field is malformed or evolves)
                info = None
                decrypted_ok = False
                plaintext_ok = False

                candidates: list[tuple[bytes, str | None]] = []
                used_forced_channel_id: str | None = None
                try:
                    extra_keys = _get_extra_channel_keys()
                    if channel_hash_int is not None:
                        for ent in extra_keys:
                            if ent.get("hash") == channel_hash_int and isinstance(ent.get("key"), (bytes, bytearray)):
                                candidates.append((bytes(ent["key"]), str(ent.get("id") or "") or None))
                    if isinstance(state.aes_key_bytes, (bytes, bytearray)):
                        candidates.append((bytes(state.aes_key_bytes), None))
                    for ent in extra_keys:
                        k = ent.get("key")
                        if isinstance(k, (bytes, bytearray)):
                            kb = bytes(k)
                            if not any(existing_k == kb for existing_k, _ in candidates):
                                candidates.append((kb, None))
                except Exception:
                    if isinstance(state.aes_key_bytes, (bytes, bytearray)):
                        candidates = [(bytes(state.aes_key_bytes), None)]

                for key_bytes, forced_id in candidates:
                    try:
                        decrypted = dataDecryptor(extracted, key_bytes)
                        info = decodeProtobuf(
                            decrypted,
                            s_id_fmt,
                            d_id_fmt,
                            "[DECRYPTED]",
                            count_invalid=False,
                            preset_name=frame_preset_name,
                            channel_hash=channel_hash_int,
                            forced_channel_id=forced_id,
                            packet_id=extracted.get('packetID'),
                        )
                        if info and not str(info).startswith("INVALID PROTOBUF"):
                            decrypted_ok = True
                            used_forced_channel_id = forced_id
                            break
                    except Exception:
                        continue

                if not decrypted_ok:
                    try:
                        raw_data = extracted['data']
                        info = decodeProtobuf(
                            raw_data,
                            s_id_fmt,
                            d_id_fmt,
                            "[UNENCRYPTED]",
                            count_invalid=False,
                            preset_name=frame_preset_name,
                            channel_hash=channel_hash_int,
                            packet_id=extracted.get('packetID'),
                        )
                        if info and not str(info).startswith("INVALID PROTOBUF"):
                            plaintext_ok = True
                        else:
                            info = None
                    except Exception as e2:
                        log_to_console(f"[ERROR] Complete parse failure: {e2}")
                        info = None

                try:
                    if decrypted_ok:
                        mesh_stats.on_decrypt_ok()
                    elif not plaintext_ok:
                        try:
                            raw_data = extracted.get('data') or b""
                            first = raw_data[0] if raw_data else None
                            looks_like_plain_pb = first in {0x0A, 0x12, 0x1A, 0x22, 0x2A, 0x32, 0x3A, 0x42, 0x4A, 0x52}
                            if looks_like_plain_pb:
                                mesh_stats.on_invalid_protobuf()
                            else:
                                mesh_stats.on_decrypt_fail()
                        except Exception:
                            mesh_stats.on_decrypt_fail()
                except Exception:
                    pass
                
                # 4) Store metrics if available
                if info:
                    preset_tag = f" -- Preset: {frame_preset_name}" if frame_preset_name else ""
                    log_to_console(f"{info}{preset_tag}")
                
                if info and not str(info).startswith("INVALID PROTOBUF"):
                    if hops_val is not None or hop_label is not None:
                        update_node(s_id_fmt, hops=hops_val, hop_label=hop_label)
                    
                    if has_metrics:
                        if hops_val == 0:
                            update_node(s_id_fmt, snr=snr_val, rssi=rssi_val)
                        else:
                            update_node(s_id_fmt, snr_indirect=snr_val, rssi_indirect=rssi_val)
                    update_node(s_id_fmt, preset=frame_preset_name)

            except Exception as e:
                try:
                    mesh_stats.on_frame_fail()
                    mesh_stats.on_packet_received(None, None, None, None)
                except Exception:
                    pass
                log_to_console(f"Parse Error (0x03): {e}")
            continue

        # Unknown frame type
        log_to_console(f"Unknown frame type: 0x{ftype:02X} len={flen}")

# --- ZMQ WORKER ---

def zmq_worker():
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    
    log_to_console(f"Connecting to tcp://{state.ip_address}:{state.port}...")
    try:
        socket.connect(f"tcp://{state.ip_address}:{state.port}")
        socket.setsockopt(zmq.SUBSCRIBE, b'')
        log_to_console("Connected!")
    except Exception as e:
        log_to_console(f"Connection Failed: {e}")
        if state.connected and state.connect_mode == "external":
            _request_stop_connection_from_thread()
        return

    # Buffer for reconstructing frames (type+len+data) even if split across multiple recv()
    rx_buf = bytearray()

    while state.connected and state.connect_mode == "external":
        try:
            if socket.poll(100) != 0:
                chunk = socket.recv()
                if not chunk:
                    continue

                rx_buf.extend(chunk)
                # parse frame via function
                parse_framed_stream_bytes(rx_buf)

            else:
                # Idle
                pass

        except Exception as e:
            log_to_console(f"Socket Error: {e}")
            break
            
    log_to_console("Disconnected.")
    if state.connected and state.connect_mode == "external":
        _request_stop_connection_from_thread()

# --- TCP WORKER ---

def tcp_worker():
    log_to_console(f"[INTERNAL] Connecting TCP to {state.ip_address}:{state.port} ...")
    rx_buf = bytearray()

    s = None
    max_wait = 300.0
    retry_sleep = 3.0
    connect_timeout = 2.0
    start_ts = time.time()
    attempt = 0

    while state.connected and state.connect_mode == "direct":
        if state.engine_proc is not None and state.engine_proc.poll() is not None:
            log_to_console("[INTERNAL] Engine process exited while waiting for TCP")
            s = None
            break

        elapsed = time.time() - start_ts
        if elapsed >= max_wait:
            break

        attempt += 1
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(connect_timeout)
            s.connect((state.ip_address, int(state.port)))
            s.settimeout(0.5)
            break
        except Exception as e:
            log_to_console(f"[INTERNAL][WAIT] TCP connect failed (attempt {attempt}): {e}")
            try:
                s.close()
            except Exception:
                pass
            s = None
            if (time.time() - start_ts) >= max_wait:
                break
            time.sleep(retry_sleep)

    if s is None:
        log_to_console("[INTERNAL] Giving up TCP connection to engine")
        if state.connected and state.connect_mode == "direct":
            _request_stop_connection_from_thread()
        return

    log_to_console("[INTERNAL] TCP connected (waiting for data...)")

    while state.connected and state.connect_mode == "direct":
        try:
            chunk = s.recv(4096)
            if not chunk:
                # peer closed
                log_to_console("[INTERNAL] TCP closed by peer")
                break
            rx_buf.extend(chunk)
            parse_framed_stream_bytes(rx_buf)
        except socket.timeout:
            continue
        except Exception as e:
            log_to_console(f"[INTERNAL] TCP error: {e}")
            break

    try:
        s.close()
    except Exception:
        pass

    log_to_console("[INTERNAL] TCP worker stopped")
    if state.connected and state.connect_mode == "direct":
        _request_stop_connection_from_thread()

# Start/Stop internal radio engine

def show_engine_error_dialog(message: str):
    with ui.dialog() as dlg, ui.card().classes('w-110'):
        ui.label(translate("popup.error.internalengine.title", "Internal Engine Error")).classes('text-lg font-bold mb-2 text-red-600')
        ui.label(message).classes('text-sm text-gray-800 mb-2')
        ui.label(
            translate("popup.error.internalengine.body1", "To use the internal SDR engine, the 'engine' folder with the correct runtime must be located in the same directory as this application.")
        ).classes('text-sm text-gray-700 mb-1')
        ui.label(
            translate("popup.error.internalengine.body2", "Alternatively, you can select External mode and use a GNU Radio flowgraph that is specifically configured for this GUI and its custom frame format.")
        ).classes('text-sm text-gray-700')
        ui.button('OK', on_click=dlg.close).classes('w-full mt-3 bg-red-600 text-white')
    dlg.open()

def show_rtlsdr_device_error_dialog():
    with ui.dialog() as dlg, ui.card().classes('w-110'):
        ui.label(translate("popup.error.rtlsdrdevice.title", "SDR Device Error")).classes('text-lg font-bold mb-2 text-red-600')
        ui.label(
            translate("popup.error.rtlsdrdevice.body1", "Wrong RTL-SDR device index or no supported devices found was reported by the internal engine.")
        ).classes('text-sm text-gray-800 mb-2')
        ui.label(
            translate("popup.error.rtlsdrdevice.body2", "Please connect a compatible RTL-SDR dongle and install the correct drivers for your operating system (Windows or Linux).")
        ).classes('text-sm text-gray-700 mb-1')
        ui.label(
            translate("popup.error.rtlsdrdevice.body3", "If you need help, consult the Wiki section on the project's GitHub repository, reachable from the About menu.\nYou can also reach it directly by clicking here:")
        ).classes('text-sm text-gray-700 whitespace-pre-line')
        wikiexacturl = f"{GITHUB_URL}/wiki/English#rtl-sdr-drivers-setup-linux"
        ui.link("Wiki-SDR-Drivers", wikiexacturl, new_tab=True).classes('text-blue-500 mb-2')
        ui.button(translate("button.close", "Close"), on_click=dlg.close).classes('w-full mt-3 bg-red-600 text-white')
    dlg.open()

def _engine_paths():
    system = platform.system()
    machine = platform.machine().lower()

    roots = []

    if getattr(sys, 'frozen', False):
        if system == "Linux" and os.environ.get('APPIMAGE'):
            roots.append(os.path.dirname(os.environ.get('APPIMAGE')))
        exe_dir = os.path.dirname(sys.executable)
        roots.append(exe_dir)
        if system == "Darwin":
            contents_dir = os.path.dirname(exe_dir)
            app_dir = os.path.dirname(contents_dir)
            parent_dir = os.path.dirname(app_dir)
            roots.append(app_dir)
            roots.append(parent_dir)
    else:
        roots.append(os.path.dirname(os.path.abspath(__file__)))

    engine_dir = None
    engine_os_root = "os"
    for r in roots:
        candidate = os.path.join(r, "engine")
        if os.path.isdir(candidate):
            engine_dir = candidate
            break
    if engine_dir is None:
        engine_dir = os.path.join(roots[0], "engine")

    if system == "Windows":
        runtime = os.path.join(engine_dir, engine_os_root, "win_x86_64", "runtime")
        py = os.path.join(runtime, "python.exe")
        return engine_dir, runtime, py, system

    if system == "Darwin":
        if machine in ("x86_64", "amd64", "i386"):
            runtime = os.path.join(engine_dir, engine_os_root, "macos_x86_64", "runtime")
        elif machine in ("arm64", "aarch64"):
            runtime = os.path.join(engine_dir, engine_os_root, "macos_arm64", "runtime")
        else:
            runtime = os.path.join(engine_dir, engine_os_root, "macos_x86_64", "runtime")
        py = os.path.join(runtime, "bin", "python")
        return engine_dir, runtime, py, system

    if system == "Linux":
        if machine in ("aarch64", "arm64"):
            runtime = os.path.join(engine_dir, engine_os_root, "linux_aarch64", "runtime")
        else:
            runtime = os.path.join(engine_dir, engine_os_root, "linux_x86_64", "runtime")
        py = os.path.join(runtime, "bin", "python")
        return engine_dir, runtime, py, system

    raise RuntimeError(f"Unsupported platform: {system} / {machine}")

def _conda_unpack_path(runtime: str, system: str) -> str | None:
    if system == "Windows":
        p = os.path.join(runtime, "Scripts", "conda-unpack.exe")
        return p if os.path.isfile(p) else None
    if system == "Linux":
        p = os.path.join(runtime, "bin", "conda-unpack")
        return p if os.path.isfile(p) else None
    return None


def ensure_conda_unpacked(runtime: str, system: str) -> None:
    # Run conda-unpack only once (portable runtime, even if not really needed, just to be sure and ensure future/backward compatibility and support for various env)
    marker = os.path.join(runtime, ".conda_unpacked_ok")
    app_path = get_app_path()
    normalized_app_path = os.path.normcase(os.path.abspath(app_path))

    stored_path = None
    if os.path.isfile(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            for line in lines:
                if line.startswith("app_path="):
                    stored_path = line.split("=", 1)[1]
                    break
        except Exception:
            stored_path = None

        if stored_path:
            stored_path_norm = os.path.normcase(os.path.abspath(stored_path))
            if stored_path_norm == normalized_app_path:
                return

    unpack = _conda_unpack_path(runtime, system)
    if not unpack:
        # No conda-unpack available; consider it OK (common on macOS or in our actual approach)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("no conda-unpack found; skipping\n")
            f.write(f"app_path={normalized_app_path}\n")
        return

    # Run from runtime dir
    try:
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        p = subprocess.run(
            [unpack],
            cwd=runtime,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        # Save output for debugging
        with open(os.path.join(runtime, "conda-unpack.log"), "w", encoding="utf-8") as f:
            f.write(p.stdout or "")
        if p.returncode != 0:
            raise RuntimeError(f"conda-unpack failed (code {p.returncode})")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("ok\n")
            f.write(f"app_path={normalized_app_path}\n")
    except Exception as e:
        # Don't crash silently: bubble up so caller can show dialog
        raise RuntimeError(f"conda-unpack error: {e}")

def list_internal_sdr_devices() -> tuple[list[tuple[str, str]], str | None]:
    try:
        engine_dir, runtime, py, system = _engine_paths()
    except Exception as e:
        return [], str(e)

    if not os.path.isdir(engine_dir):
        return [], "no_engine_dir"

    if not os.path.isdir(runtime) or not os.path.isfile(py):
        return [], "no_runtime"

    try:
        ensure_conda_unpacked(runtime, system)
    except Exception as e:
        return [], str(e)

    env = os.environ.copy()
    env["PYTHONPATH"] = engine_dir
    if system == "Windows":
        env["PATH"] = (
            f"{os.path.join(runtime,'Library','bin')};"
            f"{os.path.join(runtime,'Scripts')};"
            f"{runtime};"
            f"{env.get('PATH','')}"
        )
    else:
        env["PATH"] = f"{os.path.join(runtime,'bin')}:{env.get('PATH','')}"
        env["CONDA_PREFIX"] = runtime
        env["PYTHONNOUSERSITE"] = "1"

    if DEBUGGING:
        log_to_console(f"[SDRSCAN] start py={py}")

    # Probe each known osmosdr driver type separately
    DRIVER_PROBES = [
        ("rtl",      "rtl={idx}"),
        ("hackrf",   "hackrf={idx}"),
        ("bladerf",  "bladerf={idx}"),
        ("airspy",   "airspy={idx}"),
        ("airspyhf", "airspyhf={idx}"),
        ("uhd",      "uhd={idx}"),
        ("soapy",    "soapy={idx}"),
    ]

    probe_code_tpl = (
        "import json, sys\n"
        "args = sys.argv[1]\n"
        "try:\n"
        " import osmosdr\n"
        " s = osmosdr.source(args='numchan=1 ' + args)\n"
        " try:\n"
        "  s.set_sample_rate(1_000_000)\n"
        " except Exception:\n"
        "  pass\n"
        " print(json.dumps({'ok': True}))\n"
        "except Exception as e:\n"
        " print(json.dumps({'ok': False, 'error': str(e)}))\n"
        " sys.exit(1)\n"
    )

    results: list[tuple[str, str]] = []
    scan_errors = []

    _cf = subprocess.CREATE_NO_WINDOW if (os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")) else 0

    for driver, args_pattern in DRIVER_PROBES:
        for idx in range(0, 4):  # try idx 0..3 per driver
            dev_args = args_pattern.format(idx=idx)
            try:
                p = subprocess.run(
                    [py, "-c", probe_code_tpl, dev_args],
                    cwd=engine_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=300,
                    creationflags=_cf,
                )
            except Exception as e:
                scan_errors.append(f"{dev_args}: {e}")
                if DEBUGGING:
                    log_to_console(f"[SDRSCAN] exception probing {dev_args}: {e}")
                break  # if subprocess itself fails, skip rest of driver

            stderr_out = (p.stderr or "").strip()
            stdout_out = (p.stdout or "").strip()

            if DEBUGGING:
                log_to_console(f"[SDRSCAN] probe {dev_args} rc={p.returncode} stdout={stdout_out[:120]} stderr={stderr_out[:120]}")

            if p.returncode != 0:
                # No device at this index, stop trying higher indexes for this driver
                err_line = stderr_out.splitlines()[0][:300] if stderr_out else ""
                if err_line:
                    scan_errors.append(f"{dev_args}: {err_line}")
                break

            # Parse device name/serial from osmosdr stderr
            name = None
            sn = None
            m = re.search(
                r"Using device #\d+[:\s]+(.+?)(?:\s+SN[:\s]*([^\r\n]+))?(?:\r?\n|$)",
                stderr_out, flags=re.MULTILINE
            )
            if m:
                name = (m.group(1) or "").strip()
                sn = (m.group(2) or "").strip() or None

            label = f"{driver.upper()} #{idx}"
            if name:
                label = f"{name} #{idx}"
            if sn:
                label += f" (SN: {sn})"

            results.append((dev_args, label))
            # Found device at idx, continue to next idx

    if DEBUGGING:
        log_to_console(f"[SDRSCAN] final results={results} errors={scan_errors[:5]}")

    if results:
        return results, None
    return [], None

def start_engine_direct():
    if state.engine_proc is not None and state.engine_proc.poll() is None:
        return

    try:
        engine_dir, runtime, py, system = _engine_paths()
    except Exception as e:
        msg = f"Internal SDR engine could not be started: {e}."
        log_to_console(f"[ENGINE] {msg}")
        show_engine_error_dialog(msg)
        return

    if not os.path.isdir(engine_dir):
        msg = "Internal SDR engine could not be started: no 'engine' folder found."
        log_to_console(f"[ENGINE] {msg}")
        show_engine_error_dialog(msg)
        return

    if not os.path.isdir(runtime) or not os.path.isfile(py):
        msg = "Internal SDR engine could not be started: engine runtime missing or incomplete."
        log_to_console(f"[ENGINE] {msg}")
        show_engine_error_dialog(msg)
        return

    try:
        ensure_conda_unpacked(runtime, system)
    except Exception as e:
        msg = f"Internal SDR engine could not prepare portable runtime: {e}"
        log_to_console(f"[ENGINE] {msg}")
        show_engine_error_dialog(msg)
        return

    env = os.environ.copy()
    env["PYTHONPATH"] = engine_dir
    if system == "Windows":
        env["PATH"] = (f"{os.path.join(runtime,'Library','bin')};{os.path.join(runtime,'Scripts')};{runtime};{env.get('PATH','')}")
    else:
        env["PATH"] = f"{os.path.join(runtime,'bin')}:{env.get('PATH','')}"
        env["CONDA_PREFIX"] = runtime
        env["PYTHONNOUSERSITE"] = "1"

    if state.network_mode == "meshcore":
        # MeshCore direct engine start
        cmd = [
            py, "-m", "meshtastic_engine.run_engine",
            "--host", "127.0.0.1",
            "--port", str(state.port),
            "--center-freq", str(int(round(state.meshcore_custom_freq * 1_000_000))),
            "--samp-rate", "1000000",
            "--lora-bw", str(int(round(state.meshcore_custom_bw * 1000))),
            "--sf", str(int(state.meshcore_custom_sf)),
            "--gain", str(int(state.direct_gain)),
            "--ppm", str(int(state.direct_ppm)),
            "--sync-word", str(int(state.meshcore_custom_sync_word)),
            "--preamble-len", str(int(state.meshcore_custom_preamble_len)),
        ]

        raw_device_args = str(getattr(state, "direct_device_args", "") or "").strip()
        if raw_device_args:
            cmd.extend(["--device-args", raw_device_args])
        if getattr(state, 'direct_bias_tee', False):
            cmd.append("--bias-tee")

        log_to_console(f"[ENGINE][MESHCORE] Radio settings: freq={state.meshcore_custom_freq} MHz, bw={state.meshcore_custom_bw} kHz, sf={state.meshcore_custom_sf}")

        primary_calc = {"center_freq_hz": int(round(state.meshcore_custom_freq * 1_000_000)), "bw_khz": state.meshcore_custom_bw, "sf": state.meshcore_custom_sf}
        valid_configs = []
        primary_key = "MESHCORE"
        primary_preset_id = 0
    else:
        region = getattr(state, "direct_region", "EU_868")
    preset_name = state.direct_preset
    freq_slot = getattr(state, "direct_frequency_slot", 0)
    ch_name = getattr(state, "direct_channel_name", "") or None

    raw_device_args = str(getattr(state, "direct_device_args", "") or "").strip()
    device_args = raw_device_args
    if raw_device_args and not re.search(r"\b(rtl|hackrf|bladerf|uhd|soapy|airspy|plutosdr|lime)=", raw_device_args, flags=re.IGNORECASE):
        log_to_console(f"[ENGINE] Passing through unrecognized device args: {raw_device_args}")
    try:
        avail = getattr(state, "direct_device_detected_args", None)
        if device_args and isinstance(avail, list) and avail and device_args not in avail:
            log_to_console(f"[ENGINE] Selected device not available: {device_args}; falling back to auto")
            device_args = ""
            state.direct_device_args = ""
            save_user_config()
    except Exception:
        pass

    # --- Build preset configs (unified: single or ALL) ---
    if preset_name == "ALL":
        all_presets = list(MESHTASTIC_MODEM_PRESETS.keys())
    else:
        all_presets = [preset_name]

    valid_configs = []
    primary = None
    for pk in all_presets:
        pid = PRESET_ID_REVERSE.get(pk, 0)
        calc = meshtastic_calc_freq(region, pk, freq_slot, ch_name)
        if not calc.get("valid"):
            log_to_console(f"[ENGINE] Skipping {pk}: {calc.get('error')}")
            continue
        entry = {
            "sf": calc["sf"],
            "bw": int(round(calc["bw_khz"] * 1000)),
            "center_freq": calc["center_freq_hz"],
            "preset_id": pid,
        }
        if primary is None:
            primary = (pk, pid, calc, entry)
        else:
            valid_configs.append(entry)

    if state.network_mode != "meshcore":
        if primary is None:
            msg = f"No valid preset found for region {region}."
            log_to_console(f"[ENGINE] {msg}")
            show_engine_error_dialog(msg)
            return

        primary_key, primary_preset_id, primary_calc, _ = primary
    log_to_console(f"[ENGINE] Primary: {primary_key} (preset_id={primary_preset_id}), extra chains: {len(valid_configs)}")

    if state.network_mode != "meshcore":
        cmd = [
            py, "-m", "meshtastic_engine.run_engine",
            "--host", "127.0.0.1",
            "--port", str(state.port),
            "--center-freq", str(primary_calc["center_freq_hz"]),
            "--samp-rate", "1000000",
            "--lora-bw", str(int(round(primary_calc["bw_khz"] * 1000))),
            "--sf", str(primary_calc["sf"]),
            "--gain", str(int(state.direct_gain)),
            "--ppm", str(int(state.direct_ppm)),
            "--preset-id", str(primary_preset_id),
        ]
    if valid_configs:
        import json as _json
        cmd.extend(["--extra-demod-configs", _json.dumps(valid_configs)])
    if device_args:
        cmd.extend(["--device-args", device_args])
    if getattr(state, 'direct_bias_tee', False):
        cmd.append("--bias-tee")

    log_to_console(f"[ENGINE] Radio settings: freq={primary_calc['center_freq_hz']}, bw={int(round(primary_calc['bw_khz']*1000))}, sf={primary_calc['sf']}")

    try:
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        state.engine_proc = subprocess.Popen(
            cmd, cwd=engine_dir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, universal_newlines=True,
            creationflags=creationflags,
        )
    except Exception as e:
        msg = f"Internal SDR engine failed to start: {e}."
        log_to_console(f"[ENGINE] {msg}")
        show_engine_error_dialog(msg)
        state.engine_proc = None
        return

    def _pump():
        rtlsdr_notified = False
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        last_rx_msg_bytes = None
        _noise_re = re.compile(r"allocate_buffer: tried to allocate")

        def _parse_engine_rx_msg(s: str) -> bytes | None:
            try:
                idx = s.find("rx msg:")
                if idx < 0:
                    return None
                payload = s[idx + len("rx msg:"):].strip()
                if not payload:
                    return None
                parts = [p.strip() for p in payload.split(",")]
                out = bytearray()
                for p in parts:
                    if not p:
                        continue
                    v = int(p, 16) if p.lower().startswith("0x") else int(p, 10)
                    if v < 0 or v > 255:
                        return None
                    out.append(v)
                return bytes(out) if out else None
            except Exception:
                return None

        try:
            for line in state.engine_proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                if _noise_re.search(line):
                    continue
                log_to_console(f"[ENGINE] {line}")
                plain = ansi_re.sub("", line)
                if "rx msg:" in plain:
                    last_rx_msg_bytes = _parse_engine_rx_msg(plain)
                if "CRC invalid" in plain:
                    try:
                        if last_rx_msg_bytes:
                            extracted = dataExtractor(last_rx_msg_bytes.hex())
                            mesh_stats.mark_crc_invalid_packet(extracted.get("sender"), extracted.get("packetID"))
                            last_rx_msg_bytes = None
                        mesh_stats.on_frame_fail()
                    except Exception:
                        pass
                if (not rtlsdr_notified) and (
                    "Wrong rtlsdr device index" in line
                    or "No supported devices found" in line
                    or "failed to open rtlsdr device" in line
                    or "not found or driver not available" in line
                ):
                    rtlsdr_notified = True
                    state.rtlsdr_error_pending = True
                    state.rtlsdr_error_text = line
        except Exception:
            pass

        rc = state.engine_proc.poll() if state.engine_proc else None
        if rc is not None and DEBUGGING:
            print(f"DEBUG: ENGINE PROCESS EXITED rc={rc}", flush=True)
        if rc is not None and state.connected and state.connect_mode == "direct":
            log_to_console(f"[ENGINE] EXIT code={rc}")
            try:
                loop = MAIN_LOOP
                if loop and loop.is_running():
                    msg = translate("notification.error.enginecrash", "Engine crashed/exited (code {code})").format(code=rc)
                    
                    def safe_notify(m):
                        # This try to iterates over all active clients (in native mode there is only one but for security and compatibility we iterate)
                        try:
                            clients = app.clients() if callable(app.clients) else app.clients
                        except Exception:
                            clients = []
                        for client in clients:
                            with client:
                                ui.notify(m, color="negative")
                    
                    loop.call_soon_threadsafe(safe_notify, msg)
                    loop.call_soon_threadsafe(stop_connection)
            except Exception:
                _request_stop_connection_from_thread()

    threading.Thread(target=_pump, daemon=True).start()

def stop_engine_direct():
    if state.engine_proc is None:
        return
    try:
        if state.engine_proc.poll() is None:
            state.engine_proc.terminate()
            try:
                state.engine_proc.wait(timeout=3)
            except Exception:
                state.engine_proc.kill()
    except Exception:
        pass
    state.engine_proc = None

def start_connection(mode: str):
    if state.connected:
        return

    state.aes_key_bytes = parseAESKey(state.aes_key_b64)
    state.rx_seen_once = False
    state.last_rx_ts = 0.0
    state.connect_mode = mode
    state.connected = True
    mesh_stats.set_enabled(True)

    if mode == "external":
        t = threading.Thread(target=zmq_worker, daemon=True)
        t.start()
    elif mode == "direct":
        start_engine_direct()
        t = threading.Thread(target=tcp_worker, daemon=True)
        t.start()

def stop_connection():
    mode = state.connect_mode
    state.connected = False
    mesh_stats.set_enabled(False)
    if mode == "direct":
        stop_engine_direct()
    state.connect_mode = None
    set_connection_status_ui(False, mode)

def _request_stop_connection_from_thread():
    """Thread-safe: schedule stop_connection in the primary asyncio loop."""
    try:
        loop = MAIN_LOOP
        if loop and loop.is_running():
            loop.call_soon_threadsafe(stop_connection)
        else:
            stop_connection()
    except Exception:
        # minimal fallback without touching UI
        state.connected = False
        state.connect_mode = None

_splash_process = None

def _run_splash_process():
    """Function that runs the tk splash screen in a separate process."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes('-topmost', True)
        root.configure(bg='#0b1220')
        W, H = 520, 280
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - W) // 2
        y = (sh - H) // 2
        root.geometry(f"{W}x{H}+{x}+{y}")

        canvas = Canvas(root, width=W, height=H, bg='#0b1220', highlightthickness=0)
        canvas.pack(fill='both', expand=True)

        canvas.create_rectangle(2, 2, W-2, H-2, outline='#1e3a5f', width=2)
        canvas.create_text(W//2, 90, text=PROGRAM_NAME, fill='#ffffff', font=('Helvetica', 28, 'bold'))
        canvas.create_text(W//2, 130, text=f"v{VERSION}  by {AUTHOR}", fill='#94a3b8', font=('Helvetica', 13))

        BAR_W, BAR_H = 360, 6
        BAR_X = (W - BAR_W) // 2
        BAR_Y = 200
        canvas.create_rectangle(BAR_X, BAR_Y, BAR_X+BAR_W, BAR_Y+BAR_H, fill='#1e3a5f', outline='')
        seg = canvas.create_rectangle(BAR_X, BAR_Y, BAR_X, BAR_Y+BAR_H, fill='#3b82f6', outline='')
        canvas.create_text(W//2, BAR_Y+28, text='Loading...', fill='#64748b', font=('Helvetica', 11))

        pos = [0]
        direction = [1]
        SEG_LEN = 80
        
        def animate():
            pos[0] += direction[0] * 4
            if pos[0] + SEG_LEN >= BAR_W:
                pos[0] = BAR_W - SEG_LEN
                direction[0] = -1
            elif pos[0] <= 0:
                pos[0] = 0
                direction[0] = 1

            canvas.coords(seg, BAR_X+pos[0], BAR_Y, BAR_X+pos[0]+SEG_LEN, BAR_Y+BAR_H)
            root.after(16, animate)

        animate()
        root.mainloop()
    except:
        pass

def start_tk_splash():
    global _splash_process
    # Close Pyinstaller splash if open
    if platform.system() == 'Windows':
        try:
            import pyi_splash
            pyi_splash.close()
        except Exception:
            pass
    if _splash_process: return
    try:
        # Launch the splash screen in a separate process
        _splash_process = multiprocessing.Process(target=_run_splash_process, daemon=True)
        _splash_process.start()
    except Exception as e:
        print(f"Splash failed: {e}")

def close_tk_splash():
    global _splash_process
    if _splash_process:
        try:
            if _splash_process.is_alive():
                _splash_process.terminate() # Politically ask the process to close
                _splash_process.join(timeout=0.2)
                if _splash_process.is_alive():
                    _splash_process.kill() # Force close if still alive
        except Exception as e:
            print(f"Error closing splash process: {e}")
        finally:
            _splash_process = None

def close_all_splash():
    """Closes both the PyInstaller legacy splash and the tk cross-platform splash."""
    close_tk_splash()
    if platform.system() == 'Windows':
        try:
            import pyi_splash
            pyi_splash.close()
        except Exception:
            pass

def _copy_text_to_system_clipboard(text: str) -> bool:
    """Universal clipboard copy: Windows/macOS/Linux, venv and bundled. No external deps required."""
    system = platform.system()

    # --- Windows: pure ctypes Win32, zero deps ---
    if system == 'Windows':
        try:
            import ctypes
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE  = 0x0002
            encoded = (text + '\0').encode('utf-16-le')
            k32 = ctypes.windll.kernel32
            u32 = ctypes.windll.user32
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not h: raise OSError("GlobalAlloc failed")
            ptr = k32.GlobalLock(h)
            if not ptr:
                k32.GlobalFree(h)
                raise OSError("GlobalLock failed")
            ctypes.memmove(ctypes.c_char_p(ptr), encoded, len(encoded))
            k32.GlobalUnlock(h)
            if not u32.OpenClipboard(0): raise OSError("OpenClipboard failed")
            u32.EmptyClipboard()
            u32.SetClipboardData(CF_UNICODETEXT, h)
            u32.CloseClipboard()
            return True
        except Exception as e:
            if DEBUGGING: print(f"DEBUG: Win32 clipboard: {e}", flush=True)
        return False

    # --- macOS: pbcopy is always present ---
    if system == 'Darwin':
        try:
            subprocess.run(['pbcopy'], input=text.encode('utf-8'), check=True, timeout=3)
            return True
        except Exception as e:
            if DEBUGGING: print(f"DEBUG: pbcopy: {e}", flush=True)
        return False

    # --- Linux ---
    clean_env = os.environ.copy()
    for var in ["LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "GIO_MODULE_DIR"]:
        clean_env.pop(var, None)

    is_wayland = bool(os.environ.get('WAYLAND_DISPLAY'))
    cmds = (
        [['wl-copy'], ['xclip', '-selection', 'clipboard'], ['xsel', '--clipboard', '--input']]
        if is_wayland else
        [['xclip', '-selection', 'clipboard'], ['xsel', '--clipboard', '--input'], ['wl-copy']]
    )
    for cmd in cmds:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    env=clean_env, start_new_session=True)
            proc.communicate(input=text.encode('utf-8'), timeout=3)
            if proc.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            continue

    # Last resort: tkinter hidden window — already a dep of this app, no extras needed.
    # We keep the root alive 30 s so X11 clipboard ownership doesn't vanish immediately.
    try:
        import tkinter as _tk
        ready = threading.Event()
        result = [False]

        def _tk_clip():
            try:
                root = _tk.Tk()
                root.withdraw()
                root.clipboard_clear()
                root.clipboard_append(text)
                root.update()
                result[0] = True
            except Exception as ex:
                if DEBUGGING: print(f"DEBUG: tkinter clipboard: {ex}", flush=True)
            finally:
                ready.set()
            try:
                root.after(30000, root.destroy)
                root.mainloop()
            except Exception:
                pass

        threading.Thread(target=_tk_clip, daemon=True).start()
        ready.wait(timeout=2)
        return result[0]
    except Exception as e:
        if DEBUGGING: print(f"DEBUG: tkinter clipboard fallback: {e}", flush=True)

    return False

@app.post('/shutdown_app')
async def shutdown_app(request: Request):
    # Shutdown via beacon only on macOS (on other OS pywebview handles the lifecycle)
    if platform.system() != 'Darwin':
        return {'status': 'ignored'}
    token = request.query_params.get('token')
    if token != SHUTDOWN_TOKEN:
        return {'status': 'ignored'}
    def do_shutdown():
        time.sleep(0.2)
        app.shutdown()
    threading.Thread(target=do_shutdown, daemon=True).start()
    return {'status': 'shutting down'}

@app.post('/set_theme')
async def set_theme(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    v = data.get('theme') if isinstance(data, dict) else None
    if isinstance(v, str):
        tv = v.strip().lower()
        if tv in ('auto', 'dark', 'light'):
            state.theme = "light" if tv == "auto" else tv
            state.chat_force_refresh = True
            save_user_config()
            return {'status': 'ok'}
    return {'status': 'ignored'}

@app.post('/set_map_center')
async def set_map_center(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    lat = data.get('lat') if isinstance(data, dict) else None
    lng = data.get('lng') if isinstance(data, dict) else None
    zoom = data.get('zoom') if isinstance(data, dict) else None
    try:
        if lat is not None and lng is not None:
            state.map_center_lat = float(lat)
            state.map_center_lng = float(lng)
            if zoom is not None:
                state.map_zoom = int(zoom)
            save_user_config()
            return {'status': 'ok'}
    except Exception:
        pass
    return {'status': 'ignored'}

@app.post('/open_url')
async def open_url_endpoint(request: Request):
    data = await request.json()
    url = data.get('url')
    if url:
        safe_open_url(url)
    return {'status': 'ok'}


@app.post('/copy_to_clipboard')
async def copy_to_clipboard_endpoint(request: Request):
    data = await request.json()
    text = data.get('text', '')
    if not text:
        return {'status': 'empty'}
    ok = _copy_text_to_system_clipboard(text)
    return {'status': 'ok' if ok else 'error'}

# --- GUI ---

@ui.page('/')
def main_page():
    setup_static_files()
    load_user_config()
    if platform.system() == 'Darwin':
        ui.add_head_html('<meta name="google" content="notranslate" />')
        ui.add_head_html(f'<script>window.mesh_shutdown_token = {json.dumps(SHUTDOWN_TOKEN)};</script>')
        ui.add_head_html('''
            <script>
            window.addEventListener('beforeunload', function () {
                try {
                    if (window.sessionStorage && sessionStorage.getItem('mesh_skip_shutdown') === '1') {
                        sessionStorage.removeItem('mesh_skip_shutdown');
                        return;
                    }
                    const url = '/shutdown_app?token=' + encodeURIComponent(window.mesh_shutdown_token || '');
                    if (navigator && navigator.sendBeacon) {
                        navigator.sendBeacon(url, '');
                    } else {
                        fetch(url, {method: 'POST', keepalive: true});
                    }
                } catch (e) {}
            });
            </script>
        ''')
    close_all_splash()

    # Style
    ui.add_head_html(f'<script>window.mesh_initial_theme = {json.dumps(getattr(state, "theme", "light"))};</script>')
    ui.add_head_html('''
        <style>
        .device-row { overflow: hidden !important; }
        .device-row { width: 100% !important; }
        .device-select-wrap { min-width: 0 !important; overflow: hidden !important; }
        .device-select { min-width: 0 !important; max-width: 100% !important; width: 100% !important; }
        .device-select .q-field__control { min-width: 0 !important; }
        .device-select .q-field__control-container { min-width: 0 !important; }
        .device-select .q-field__native { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .bias-tee-cb .q-checkbox__bg { border-color: white !important; }
        .channel-tab-btn { 
            border-radius: 0 !important; 
            min-width: fit-content;
            white-space: nowrap;
        }
        .mesh-dark .channel-tab-btn { 
            color: #94a3b8 !important; 
        }
        .mesh-dark .channel-tab-btn.border-blue-500 { 
            color: #60a5fa !important; 
        }
        .channel-tabs-row {
            scrollbar-width: none !important;
            -ms-overflow-style: none !important;
            overflow-x: auto !important;
            overflow-y: visible !important;
            scroll-behavior: smooth;
        }
        .channel-tabs-row::-webkit-scrollbar {
            display: none !important;
        }
        .channel-tabs-wrapper {
            position: relative;
            width: 100%;
        }
        .channel-tabs-arrow {
            position: absolute;
            top: 0; bottom: 0;
            width: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            z-index: 20;
            background: linear-gradient(to right, var(--tab-arrow-bg, #f1f5f9), transparent);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s;
        }
        .channel-tabs-arrow.left  { left: 0; background: linear-gradient(to right, var(--tab-arrow-bg, #f1f5f9), transparent); }
        .channel-tabs-arrow.right { right: 0; background: linear-gradient(to left,  var(--tab-arrow-bg, #f1f5f9), transparent); }
        .channel-tabs-arrow.visible { opacity: 1; pointer-events: all; }
        .mesh-dark .channel-tabs-arrow { --tab-arrow-bg: #0f172a; }
        .channel-tabs-row .q-btn__wrapper {
            min-width: 0 !important;
            padding: 4px 8px !important;
        }
        .channel-tabs-row .q-badge--floating {
            position: absolute;
            top: 3px;
            cursor: inherit;
        }
        .mesh-kpi-card {
            container-type: inline-size;
            container-name: kpi-card;
        }
        .mesh-kpi-label { font-size: 0.75rem; }
        .mesh-kpi-icon  { font-size: 14px; }
        .mesh-kpi-value { font-size: 1.25rem; }
        .mesh-kpi-badge { font-size: 0.75rem; }

        @container kpi-card (min-width: 160px) {
            .mesh-kpi-label { font-size: 0.8rem; }
            .mesh-kpi-icon  { font-size: 15px; }
            .mesh-kpi-value { font-size: 1.35rem; }
            .mesh-kpi-badge { font-size: 0.75rem; }
        }
        @container kpi-card (min-width: 200px) {
            .mesh-kpi-label { font-size: 0.875rem; }
            .mesh-kpi-icon  { font-size: 16px; }
            .mesh-kpi-value { font-size: 1.5rem; }
            .mesh-kpi-badge { font-size: 0.75rem; }
        }
        .nicegui-log,
        .nicegui-log * {
            user-select: text !important;
            -webkit-user-select: text !important;
            cursor: text;
        }
        </style>
    ''')
    ui.add_head_html('''
        <script>
        // Open external links in the default browser
        document.addEventListener('click', function(e) {
            const link = e.target.closest('a');
            if (link && (link.getAttribute('target') === '_blank' || link.href.includes('github.com') || link.href.includes('ko-fi.com'))) {
                e.preventDefault();
                fetch('/open_url', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: link.href})
                });
            }
        }, true);
        </script>
    ''')
    ui.add_head_html('''
        <script>
        // Right-click context menu on external links
        (function() {
            var _ctxMenu = null;
            function removeMenu() {
                if (_ctxMenu) { _ctxMenu.remove(); _ctxMenu = null; }
            }
            window.contextMenuT = (key, fallback) => {
                try {
                    if (window.mesh_i18n && window.mesh_i18n[key]) return window.mesh_i18n[key];
                } catch (e) { }
                return fallback ? String(fallback) : String(key || '');
            };
            document.addEventListener('contextmenu', function(e) {
                const link = e.target.closest('a');
                if (!link || !link.href || (!link.getAttribute('target') && !link.href.includes('http'))) return;
                e.preventDefault();
                removeMenu();
                const label = window.contextMenuT('contextmenu.copylink', 'Copy Link');
                const menu = document.createElement('div');
                menu.style.cssText = 'position:fixed;z-index:99999;background:#fff;border:1px solid #ccc;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,0.18);padding:4px 0;min-width:140px;font-size:14px;font-family:sans-serif;';
                const item = document.createElement('div');
                item.textContent = label;
                item.style.cssText = 'padding:8px 18px;cursor:pointer;color:#222;';
                item.addEventListener('mouseenter', function() { item.style.background='#f0f4ff'; });
                item.addEventListener('mouseleave', function() { item.style.background=''; });
                item.addEventListener('click', async function() {
                    const ok = await window.meshCopyToClipboard(link.href);
                    if (ok) {
                        window.meshNotify(window.contextMenuT('notification.positive.copytext', 'Copied to clipboard'), 'positive');
                    } else {
                        window.meshNotify(window.contextMenuT('notification.error.copytext', 'Copy text failed'), 'negative');
                    }
                    removeMenu();
                });
                menu.appendChild(item);
                menu.style.left = Math.min(e.clientX, window.innerWidth - 160) + 'px';
                menu.style.top  = Math.min(e.clientY, window.innerHeight - 60) + 'px';
                document.body.appendChild(menu);
                _ctxMenu = menu;
            });
            document.addEventListener('click', removeMenu, true);
            document.addEventListener('keydown', function(e) { if(e.key==='Escape') removeMenu(); });
        })();
        </script>
    ''')
    ui.add_head_html('''
        <script>
        // Suppress the cosmetic "ResizeObserver loop" console warning on Linux/pywebview.
        // This warning is benign - it means Chromium had more ResizeObserver callbacks
        // than it could deliver in one frame. Suppressing it does not affect functionality.
        (function() {
            var _origError = console.error.bind(console);
            console.error = function() {
                var msg = arguments[0];
                if (typeof msg === 'string' && msg.indexOf('ResizeObserver loop') !== -1) return;
                return _origError.apply(console, arguments);
            };
            // Also suppress via window error handler (Chromium reports it both ways)
            window.addEventListener('error', function(e) {
                if (e && e.message && e.message.indexOf('ResizeObserver loop') !== -1) {
                    e.stopImmediatePropagation();
                    e.preventDefault();
                    return true;
                }
            }, true);
        })();
        </script>
    ''')
    ui.add_head_html('''
        <script>
        (function () {
            const KEY = 'mesh_theme';
            function getSaved() {
                try { return localStorage.getItem(KEY); } catch (e) { return null; }
            }
            function save(mode) {
                try { localStorage.setItem(KEY, mode); } catch (e) { }
            }
            function updateToggle(isDark) {
                try {
                    const btn = document.getElementById('mesh-theme-toggle');
                    if (!btn) return;
                    if (isDark) btn.classList.add('is-dark');
                    else btn.classList.remove('is-dark');
                    const sun = btn.querySelector('.mesh-theme-icon.sun');
                    const moon = btn.querySelector('.mesh-theme-icon.moon');
                    if (sun) sun.style.opacity = isDark ? '0.55' : '1';
                    if (moon) moon.style.opacity = isDark ? '1' : '0.55';
                } catch (e) { }
            }
            function persistBackend(mode) {
                try {
                    fetch('/set_theme', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ theme: mode }),
                        keepalive: true,
                    });
                } catch (e) { }
            }
            function normalize(mode) {
                const v = String(mode || '').trim().toLowerCase();
                if (v === 'dark' || v === 'light') return v;
                if (v === 'auto') return 'light';
                return 'light';
            }
            function getMainMap() {
                try {
                    const id = window.mesh_main_map_id;
                    if (!id || typeof getElement !== 'function') return null;
                    const el = getElement(id);
                    const map = el && el.map;
                    return map || null;
                } catch (e) {
                    return null;
                }
            }
            function applyMapTheme(isDark) {
                try {
                    if (!window.L) return;
                    const map = getMainMap();
                    if (!map) return;

                    const canUseTiles = window.mesh_tile_internet !== false;
                    try {
                        const container = map.getContainer && map.getContainer();
                        if (container && container.style) {
                            if (canUseTiles) {
                                container.style.backgroundColor = isDark ? '#0f1a2f' : '';
                            } else {
                                container.style.backgroundColor = isDark ? '#0f1a2f' : '#aad3df';
                            }
                        }
                    } catch (e) { }

                    if (canUseTiles) {
                        const lightUrl = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
                        const lightAttr = '&copy; OpenStreetMap contributors';

                        if (!map._meshTileLayerLight) {
                            map._meshTileLayerLight = L.tileLayer(lightUrl, { maxZoom: 19, attribution: lightAttr });
                        }
                        const target = map._meshTileLayerLight;
                        map.eachLayer(function (layer) {
                            try {
                                if (layer && layer instanceof L.TileLayer && layer !== target) {
                                    map.removeLayer(layer);
                                }
                            } catch (e) { }
                        });
                        try {
                            if (!map.hasLayer(target)) target.addTo(map);
                        } catch (e) { }
                    } else {
                        map.eachLayer(function (layer) {
                            try {
                                if (layer && layer instanceof L.TileLayer) {
                                    map.removeLayer(layer);
                                }
                            } catch (e) { }
                        });
                    }

                    map.eachLayer(function (layer) {
                        try {
                            if (!layer || !(layer instanceof L.GeoJSON) || typeof layer.setStyle !== 'function') return;
                            if (!layer._meshOrigStyle) {
                                const s = (layer.options && layer.options.style) ? layer.options.style : {};
                                layer._meshOrigStyle = JSON.parse(JSON.stringify(s || {}));
                            }
                            if (!isDark) {
                                layer.setStyle(layer._meshOrigStyle);
                                return;
                            }
                            const orig = layer._meshOrigStyle || {};
                            const fillOpacity = (orig.fillOpacity !== undefined && orig.fillOpacity !== null) ? Number(orig.fillOpacity) : 0;
                            const hasFill = fillOpacity > 0;
                            const newStyle = {
                                color: '#334155',
                                weight: (orig.weight !== undefined && orig.weight !== null) ? orig.weight : 1.2,
                                opacity: (orig.opacity !== undefined && orig.opacity !== null) ? orig.opacity : 1.0,
                                fillOpacity: hasFill ? 0.92 : 0.0,
                            };
                            if (hasFill) newStyle.fillColor = '#111c33';
                            layer.setStyle(newStyle);
                        } catch (e) { }
                    });

                    try {
                        if (map._meshOfflineLabelLayer && typeof map._meshOfflineLabelLayer._redraw === 'function') {
                            map._meshOfflineLabelLayer._redraw();
                        }
                    } catch (e) { }
                } catch (e) { }
            }
            window.meshEnsurePathLayer = (map) => {
                if (!window.L || !map) return;
                if (map._meshPathLayer) return;
                map._meshPathLayer = window.L.layerGroup().addTo(map);
            };

            window.meshDrawPaths = (mapElementId, paths) => {
                try {
                    const el = (typeof getElement === 'function') ? getElement(mapElementId) : null;
                    const map = el && el.map;
                    if (!window.L || !map) return;
                    window.meshEnsurePathLayer(map);
                    map._meshPathLayer.clearLayers();

                    paths.forEach(p => {
                        if (!p.coords || p.coords.length < 2) return;
                        window.L.polyline(p.coords, {
                            color: '#3b82f6',
                            weight: 2,
                            opacity: 0.6,
                            dashArray: '5, 10'
                        }).addTo(map._meshPathLayer);
                    });
                } catch (e) { }
            };

            window.meshApplyThemeToMapWhenReady = (tries) => {
                let remaining = Number.isFinite(Number(tries)) ? Number(tries) : 40;
                const tick = () => {
                    try {
                        const map = getMainMap();
                        if (map) {
                            try {
                                if (map._loaded || map._initHooksCalled || map._panes) {
                                    applyMapTheme(document.documentElement.classList.contains('mesh-dark'));
                                    return;
                                }
                            } catch (e) {
                                applyMapTheme(document.documentElement.classList.contains('mesh-dark'));
                                return;
                            }
                        }
                    } catch (e) { }
                    remaining -= 1;
                    if (remaining <= 0) return;
                    try { setTimeout(tick, 120); } catch (e) { }
                };
                tick();
            };
            function apply(mode, persistLocal, persistServer) {
                const root = document.documentElement;
                const m = normalize(mode);
                const isDark = m === 'dark';
                root.classList.toggle('mesh-dark', isDark);
                root.classList.toggle('mesh-light', !isDark);
                if (persistLocal) save(isDark ? 'dark' : 'light');
                updateToggle(isDark);
                if (persistServer) persistBackend(isDark ? 'dark' : 'light');
                applyMapTheme(isDark);
            }
            window.meshGetTheme = () => {
                const root = document.documentElement;
                return root.classList.contains('mesh-dark') ? 'dark' : 'light';
            };
            window.meshSetTheme = (mode) => {
                apply(String(mode || '').toLowerCase() === 'dark' ? 'dark' : 'light', false, false);
            };
            window.meshToggleTheme = () => {
                apply(window.meshGetTheme() === 'dark' ? 'light' : 'dark', true, true);
            };

            const initial = normalize(window.mesh_initial_theme);
            if (initial === 'dark' || initial === 'light') {
                apply(initial, false, false);
            } else {
                const saved = getSaved();
                if (saved === 'dark' || saved === 'light') {
                    apply(saved, false, false);
                } else {
                    apply('light', false, false);
                }
            }

            window.addEventListener('DOMContentLoaded', () => {
                try { updateToggle(window.meshGetTheme() === 'dark'); } catch (e) { }
                try { if (window.meshApplyThemeToMapWhenReady) window.meshApplyThemeToMapWhenReady(); } catch (e) { }
            });
        })();

        window.meshNotify = (message, color) => {
            const msg = (message === null || message === undefined) ? '' : String(message);
            const col = color ? String(color) : 'positive';
            try {
                if (window.Quasar && window.Quasar.Notify && typeof window.Quasar.Notify.create === 'function') {
                    window.Quasar.Notify.create({ message: msg, color: col, timeout: 900, position: 'top' });
                    return true;
                }
            } catch (e) { }
            try {
                if (window.$q && typeof window.$q.notify === 'function') {
                    window.$q.notify({ message: msg, color: col, timeout: 900, position: 'top' });
                    return true;
                }
            } catch (e) { }
            return false;
        };

        window.meshT = (key, fallback) => {
            try {
                if (window.mesh_i18n && window.mesh_i18n[key]) return window.mesh_i18n[key];
            } catch (e) { }
            return fallback ? String(fallback) : String(key || '');
        };

        window.meshCopyToClipboard = async (text) => {
            const val = (text === null || text === undefined) ? '' : String(text);
            // Primary: Python backend (reliable on all platforms/distros/venv/bundled)
            try {
                const r = await fetch('/copy_to_clipboard', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: val})
                });
                const data = await r.json();
                if (data.status === 'ok') return true;
            } catch (e) { }
            // Fallback 1: navigator.clipboard (works in browsers, sometimes in webview)
            try {
                if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(val);
                    return true;
                }
            } catch (e) { }
            // Fallback 2: execCommand (deprecated but last resort)
            try {
                const ta = document.createElement('textarea');
                ta.value = val;
                ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                const ok = document.execCommand('copy');
                document.body.removeChild(ta);
                return ok;
            } catch (e) { }
            return false;
        };

        window.meshPresetCellRenderer = (params) => {
            const val = params && params.value ? String(params.value) : '';
            if (!val) return '';
            const colors = {
                'LONG_FAST':'#22c55e','MEDIUM_FAST':'#3b82f6','LONG_SLOW':'#a855f7',
                'MEDIUM_SLOW':'#f59e0b','SHORT_FAST':'#ef4444','SHORT_SLOW':'#f97316',
                'SHORT_TURBO':'#ec4899','LONG_TURBO':'#06b6d4','LONG_MODERATE':'#84cc16',
                'VERY_LONG_SLOW':'#64748b'
            };
            const shortNames = {
                'LONG_FAST':'LongFast','MEDIUM_FAST':'MedFast','LONG_SLOW':'LongSlow',
                'MEDIUM_SLOW':'MedSlow','SHORT_FAST':'ShortFast','SHORT_SLOW':'ShortSlow',
                'SHORT_TURBO':'ShrtTurbo','LONG_TURBO':'LngTurbo','LONG_MODERATE':'LngMod',
                'VERY_LONG_SLOW':'VLongSlow'
            };
            const col = colors[val] || '#64748b';
            const label = shortNames[val] || val;
            const span = document.createElement('span');
            span.style.cssText = `background:${col};color:white;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;`;
            span.textContent = label;
            return span;
        };

        window.meshCopyCellRenderer = (params) => {
            const value = params && params.value !== undefined && params.value !== null ? String(params.value) : '';
            const isClickable = !!(params && params.data && params.data.lat && params.data.lon);
            const wrap = document.createElement('span');
            wrap.style.display = 'inline-flex';
            wrap.style.alignItems = 'center';
            wrap.style.gap = '6px';
            wrap.style.width = '100%';
            if (isClickable) {
                wrap.title = window.meshT('tooltip.openinmap.title', 'Click to open in map');
                wrap.style.cursor = 'pointer';
            }

            const textEl = document.createElement('span');
            textEl.textContent = value;
            textEl.style.userSelect = 'text';
            textEl.style.overflow = 'hidden';
            textEl.style.textOverflow = 'ellipsis';
            textEl.style.whiteSpace = 'nowrap';

            const btn = document.createElement('button');
            btn.type = 'button';
            try { btn.classList.add('mesh-copy-btn'); } catch (e) { }
            btn.textContent = '⧉';
            btn.title = window.meshT('tooltip.copytext.title', 'Copy');
            btn.style.cursor = 'pointer';
            btn.style.border = '1px solid rgba(0,0,0,0.15)';
            btn.style.borderRadius = '6px';
            btn.style.padding = '0 6px';
            btn.style.lineHeight = '16px';
            btn.style.height = '18px';
            btn.style.fontSize = '12px';
            btn.style.userSelect = 'none';

            btn.addEventListener('click', async (ev) => {
                try { ev.stopPropagation(); } catch (e) { }
                const ok = await window.meshCopyToClipboard(value);
                if (ok) {
                    window.meshNotify(window.meshT('notification.positive.copytext', 'Copied to clipboard'), 'positive');
                } else {
                    window.meshNotify(window.meshT('notification.error.copytext', 'Copy text failed'), 'negative');
                }
            });

            wrap.appendChild(textEl);
            if (value) wrap.appendChild(btn);
            return wrap;
        };

        window.upsertNodeData = (elementId, newRows) => {
            // Use global API reference registered by onGridReady
            const api = window.mesh_grid_api;
            
            if (!api) {
                // Check if we are simply not visible yet (e.g. Map tab active)
                // In this case, we don't need to panic. The data is in state.nodes.
                // When the user switches to the tab, the grid will init with full data.
                return;
            }
            
            // Check if destroyed
            if (api.isDestroyed && api.isDestroyed()) return;

            const toAdd = [];
            const toUpdate = [];
            
            newRows.forEach(row => {
                // Use getRowNode to check existence
                if (api.getRowNode(row.id)) {
                    toUpdate.push(row);
                } else {
                    toAdd.push(row);
                }
            });
            
            if (toAdd.length > 0 || toUpdate.length > 0) {
                api.applyTransaction({ add: toAdd, update: toUpdate });
            }
        };
        
        // Helper to bridge Map Popup clicks to Python
        window.goToNode = (nodeId) => {
            const input = document.querySelector('.node-target-input input');
            if (input) {
                // Always reset first to ensure change detection even for same ID
                // We use a small timeout to ensure the clear event is processed 
                // separately from the set event, guaranteeing the change triggers.
                input.value = "";
                input.dispatchEvent(new Event('input', { bubbles: true }));
                
                setTimeout(() => {
                    input.value = nodeId;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                }, 50);
            }
        };

        window.meshEscapeHtml = (s) => {
            try {
                return String(s ?? '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/\"/g, '&quot;')
                    .replace(/'/g, '&#39;');
            } catch (e) {
                return '';
            }
        };

        window.meshNodeAgeClass = (hours) => {
            const h = Number(hours);
            if (!Number.isFinite(h)) return 'mesh-node-orange';
            if (h <= 3) return 'mesh-node-green';
            if (h <= 6) return 'mesh-node-yellow';
            return 'mesh-node-orange';
        };

        window.meshEnsureNodeLegend = (map) => {
            try {
                if (!window.L || !map) return;
                if (map._meshNodeLegendControl) return;
                const legend = window.L.control({ position: 'bottomleft' });
                legend.onAdd = function () {
                    const div = window.L.DomUtil.create('div', 'mesh-node-legend');
                    const title = (window.meshT ? window.meshT('map.legend.lastheard', 'Last Heard') : 'Last Heard');
                   div.innerHTML =
                        '<div class="mesh-node-legend-title">' + window.meshEscapeHtml(title) + '</div>' +
                        '<div class="mesh-node-legend-row"><span class="mesh-node-legend-swatch mesh-node-green"></span><span>≤ 3h</span></div>' +
                        '<div class="mesh-node-legend-row"><span class="mesh-node-legend-swatch mesh-node-yellow"></span><span>≤ 6h</span></div>' +
                        '<div class="mesh-node-legend-row"><span class="mesh-node-legend-swatch mesh-node-orange"></span><span>&gt; 6h</span></div>' +
                        '<div style="border-top:1px solid rgba(0,0,0,0.10);margin:6px 0 4px;"></div>' +
                        '<div class="mesh-node-legend-row"><span class="mesh-node-legend-swatch" style="border:3px solid #00f5ff;box-shadow:0 0 0 2px rgba(0,245,255,0.30),0 0 8px 2px rgba(0,245,255,0.22);"></span><span>Direct (0 hops)</span></div>' +
                        '<div class="mesh-node-legend-row"><span class="mesh-node-legend-swatch" style="border:1px solid rgba(116,116,116,0.92);"></span><span>Indirect (relayed)</span></div>' +
                        '<div class="mesh-node-legend-row"><span class="mesh-node-legend-swatch" style="border:1.5px dashed rgba(200,200,200,0.72);"></span><span>Hops unknown</span></div>';
                    return div;
                };
                legend.addTo(map);
                map._meshNodeLegendControl = legend;
            } catch (e) { }
        };

        window.meshMarkerHtml = (label, cls, hopCls, roleCls, relayCls) => {
            const text = window.meshEscapeHtml(label ?? '');
            const c = window.meshEscapeHtml(cls ?? '');
            const h = window.meshEscapeHtml(hopCls ?? 'mesh-node-unknown');
            const r = window.meshEscapeHtml(roleCls ?? '');
            const rl = window.meshEscapeHtml(relayCls ?? '');
            return '<div class="mesh-node-marker ' + c + ' ' + h + ' ' + r + ' ' + rl + '"><div class="mesh-node-text">' + text + '</div></div>';
        };

        window.meshGetNodeMarkerDims = (map) => {
            try {
                if (map && map._meshNodeMarkerDims && typeof map._meshNodeMarkerDims === 'object') {
                    const d = map._meshNodeMarkerDims;
                    if (Number.isFinite(d.w) && Number.isFinite(d.h) && Number.isFinite(d.t)) {
                        if ((Date.now() - d.t) < 2000) return { w: d.w, h: d.h };
                    }
                }
            } catch (e) { }
            let w = 35;
            let h = 35;
            try {
                const probe = document.createElement('div');
                probe.style.position = 'fixed';
                probe.style.left = '-10000px';
                probe.style.top = '-10000px';
                probe.style.pointerEvents = 'none';
                probe.innerHTML = window.meshMarkerHtml('TEST', 'mesh-node-green');
                document.body.appendChild(probe);
                const node = probe.querySelector('.mesh-node-marker');
                if (node) {
                    const r = node.getBoundingClientRect();
                    if (r && Number.isFinite(r.width) && Number.isFinite(r.height)) {
                        w = Math.max(8, Math.round(r.width));
                        h = Math.max(8, Math.round(r.height));
                    }
                }
                document.body.removeChild(probe);
            } catch (e) { }
            try {
                if (map) map._meshNodeMarkerDims = { w: w, h: h, t: Date.now() };
            } catch (e) { }
            return { w: w, h: h };
        };

        window.meshPulseMarker = (marker, totalMs = 9000, intervalMs = 1000) => {
            try {
                if (!marker || typeof marker.getElement !== 'function') return;
                const el = marker.getElement();
                if (!el) return;
                const node = el.querySelector('.mesh-node-marker');
                if (!node) return;

                try {
                    if (marker._meshPulseInterval) {
                        window.clearInterval(marker._meshPulseInterval);
                        marker._meshPulseInterval = null;
                    }
                    if (marker._meshPulseStopTimeout) {
                        window.clearTimeout(marker._meshPulseStopTimeout);
                        marker._meshPulseStopTimeout = null;
                    }
                } catch (e) { }

                const doPulse = () => {
                    try {
                        node.classList.remove('mesh-pulse');
                        void node.offsetWidth;
                        node.classList.add('mesh-pulse');
                    } catch (e) { }
                };

                doPulse();

                const iv = Math.max(200, Number(intervalMs) || 1000);
                const total = Math.max(0, Number(totalMs) || 0);
                if (total <= 0) return;

                marker._meshPulseInterval = window.setInterval(doPulse, iv);
                marker._meshPulseStopTimeout = window.setTimeout(() => {
                    try {
                        if (marker._meshPulseInterval) {
                            window.clearInterval(marker._meshPulseInterval);
                            marker._meshPulseInterval = null;
                        }
                        marker._meshPulseStopTimeout = null;
                    } catch (e) { }
                }, total);
            } catch (e) { }
        };

        window.meshRefreshNodeMarkerColors = (map) => {
            try {
                if (!map || !map._meshNodeMarkers) return;
                const store = map._meshNodeMarkers;
                const now = (Date.now() / 1000);
                for (const nid in store) {
                    const marker = store[nid];
                    if (!marker) continue;
                    const lastSeenTs = Number(marker._mesh_last_seen_ts || 0);
                    const ageH = lastSeenTs > 0 ? ((now - lastSeenTs) / 3600) : 1e9;
                    const cls = window.meshNodeAgeClass(ageH);
                    if (typeof marker.getElement === 'function') {
                        const el = marker.getElement();
                        if (!el) continue;
                        const node = el.querySelector('.mesh-node-marker');
                        if (!node) continue;
                        node.classList.remove('mesh-node-green', 'mesh-node-yellow', 'mesh-node-orange');
                        node.classList.add(cls);
                        const lastHops = marker._mesh_hops;
                        node.classList.remove('mesh-node-direct', 'mesh-node-indirect', 'mesh-node-unknown');
                        const hopCls2 = (lastHops === 0) ? 'mesh-node-direct' : (lastHops === null || lastHops === undefined) ? 'mesh-node-unknown' : 'mesh-node-indirect';
                        node.classList.add(hopCls2);
                    }
                }
            } catch (e) { }
        };

        window.meshShowOverlapPopup = (map, lat, lon, overlapping, store) => {
            try {
                if (!window.L || !map) return;
                if (window._meshOverlapPopup) {
                    window._meshOverlapPopup.remove();
                    window._meshOverlapPopup = null;
                }
                window._meshOverlapMap = map;
                window._meshOverlapStore = store;

                const container = document.createElement('div');
                container.style.minWidth = '160px';
                container.style.maxWidth = '260px';

                const title = document.createElement('div');
                title.style.cssText = 'font-weight:700;margin-bottom:6px;font-size:13px;';
                title.textContent = '📍 ' + overlapping.length + ' ' + (window.meshT ? window.meshT('map.overlap.nodes_here', 'nodes here') : 'nodes here');
                container.appendChild(title);

                overlapping.forEach(function(item) {
                    const row = document.createElement('div');
                    row.style.cssText = 'padding:5px 0;border-top:1px solid rgba(0,0,0,0.10);cursor:pointer;';

                    const nameSpan = document.createElement('span');
                    nameSpan.style.fontWeight = '600';
                    nameSpan.textContent = item.label;

                    const idSpan = document.createElement('span');
                    idSpan.style.cssText = 'font-size:11px;color:#64748b;margin-left:6px;';
                    idSpan.textContent = item.id;

                    row.appendChild(nameSpan);
                    row.appendChild(idSpan);

                    row.addEventListener('click', function() {
                        try {
                            if (window._meshOverlapPopup) {
                                window._meshOverlapPopup.remove();
                                window._meshOverlapPopup = null;
                            }
                            const m = window._meshOverlapStore && window._meshOverlapStore[item.id];
                            if (m && m._mesh_popup_html) {
                                const latlng = m.getLatLng();
                                window.L.popup({ closeButton: true, autoClose: true })
                                    .setLatLng(latlng)
                                    .setContent(m._mesh_popup_html)
                                    .openOn(window._meshOverlapMap);
                            }
                        } catch(e) {}
                    });

                    container.appendChild(row);
                });

                window._meshOverlapPopup = window.L.popup({ closeButton: true, autoClose: true })
                    .setLatLng([lat, lon])
                    .setContent(container)
                    .openOn(map);
            } catch(e) {}
        };

        window.meshUpsertNodesOnMap = (mapElementId, nodes) => {
            try {
                const el = (typeof getElement === 'function') ? getElement(mapElementId) : null;
                const map = el && el.map;
                if (!window.L || !map) return;
                window.meshEnsureNodeLegend(map);
                if (!map._meshNodeMarkers) map._meshNodeMarkers = {};
                const store = map._meshNodeMarkers;
                const now = (Date.now() / 1000);
                const dims = window.meshGetNodeMarkerDims(map);
                const iconW = Math.max(8, Number(dims.w) || 48);
                const iconH = Math.max(8, Number(dims.h) || 48);
                const anchorX = iconW / 2;
                const anchorY = iconH / 2;
                const popupY = -anchorY + 4;

                if (!map._meshNodeColorTimer) {
                    map._meshNodeColorTimer = window.setInterval(() => {
                        try { window.meshRefreshNodeMarkerColors(map); } catch(e) {}
                    }, 30000);
                    //Register cleanup if the map is removed
                    map.on('remove', function() {
                        try {
                            if (map._meshNodeColorTimer) {
                                window.clearInterval(map._meshNodeColorTimer);
                                map._meshNodeColorTimer = null;
                            }
                        } catch(e) {}
                    });
                }

                const list = Array.isArray(nodes) ? nodes : [];
                for (let i = 0; i < list.length; i++) {
                    const n = list[i] || {};
                    const nid = String(n.id ?? '');
                    if (!nid) continue;
                    const lat = Number(n.lat);
                    const lon = Number(n.lon);
                    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
                    const lastSeenTs = Number(n.last_seen_ts || 0);
                    const ageH = lastSeenTs > 0 ? ((now - lastSeenTs) / 3600) : 1e9;
                    const cls = window.meshNodeAgeClass(ageH);
                    const label = String(n.marker_label ?? '');
                    const popup = String(n.popup ?? '');

                    const hops = n.hops;
                    const hopCls = (hops === 0) ? 'mesh-node-direct' : (hops === null || hops === undefined) ? 'mesh-node-unknown' : 'mesh-node-indirect';

                    let roleCls = '';
                    if (n.role_id === 'Repeater') roleCls = 'mesh-node-repeater';
                    else if (n.role_id === 'Room Server') roleCls = 'mesh-node-room-server';
                    else if (n.role_id === 'Companion') roleCls = 'mesh-node-companion';

                    const relayCls = n.relay_capable ? 'mesh-node-relay-capable' : '';

                    const html = window.meshMarkerHtml(label, cls, hopCls, roleCls, relayCls);
                    const icon = window.L.divIcon({
                        className: 'mesh-node-divicon',
                        html: html,
                        iconSize: [iconW, iconH],
                        iconAnchor: [anchorX, anchorY],
                        popupAnchor: [0, popupY],
                    });

                    if (!store[nid]) {
                        const marker = window.L.marker([lat, lon], { icon: icon, interactive: true });
                        marker._mesh_last_seen_ts = lastSeenTs;
                        marker._mesh_hops = (hops === null || hops === undefined) ? null : Number(hops);
                        marker._mesh_icon_html = html;
                        marker._mesh_node_label = label;
                        marker._mesh_popup_html = popup;
                        marker._map = map;
                        marker.addTo(map);
                        store[nid] = marker;
                        marker.on('click', function(e) {
                            try {
                                const thisLat = marker.getLatLng().lat;
                                const thisLon = marker.getLatLng().lng;
                                const p0 = marker.getLatLng();
                                const THRESH_M = 9;
                                const overlapping = [];
                                for (const oid in store) {
                                    const om = store[oid];
                                    if (!om) continue;
                                    const ol = om.getLatLng();
                                    if (p0 && ol && typeof p0.distanceTo === 'function' && p0.distanceTo(ol) <= THRESH_M) {
                                        overlapping.push({ id: oid, label: om._mesh_node_label || oid });
                                    }
                                }
                                if (overlapping.length > 1) {
                                    window.L.DomEvent.stopPropagation(e);
                                    window.meshShowOverlapPopup(map, thisLat, thisLon, overlapping, store);
                                } else {
                                    const p = marker._mesh_popup_html;
                                    if (p) {
                                        window.L.popup({ closeButton: true, autoClose: true })
                                            .setLatLng(marker.getLatLng())
                                            .setContent(p)
                                            .openOn(map);
                                    }
                                }
                            } catch(ex) {}
                        });
                        continue;
                    }

                    const marker = store[nid];
                    try { marker.setLatLng([lat, lon]); } catch (e) { }
                    if (marker._mesh_icon_html !== html) {
                        try { marker.setIcon(icon); } catch (e) { }
                        marker._mesh_icon_html = html;
                        try { marker.off('click'); } catch(e) {}
                        marker.on('click', function(e) {
                            try {
                                const thisLat = marker.getLatLng().lat;
                                const thisLon = marker.getLatLng().lng;
                                const p0 = marker.getLatLng();
                                const THRESH_M = 9;
                                const overlapping = [];
                                for (const oid in store) {
                                    const om = store[oid];
                                    if (!om) continue;
                                    const ol = om.getLatLng();
                                    if (p0 && ol && typeof p0.distanceTo === 'function' && p0.distanceTo(ol) <= THRESH_M) {
                                        overlapping.push({ id: oid, label: om._mesh_node_label || oid });
                                    }
                                }
                                if (overlapping.length > 1) {
                                    window.L.DomEvent.stopPropagation(e);
                                    window.meshShowOverlapPopup(map, thisLat, thisLon, overlapping, store);
                                } else {
                                    const p = marker._mesh_popup_html;
                                    if (p) {
                                        window.L.popup({ closeButton: true, autoClose: true })
                                            .setLatLng(marker.getLatLng())
                                            .setContent(p)
                                            .openOn(map);
                                    }
                                }
                            } catch(ex) {}
                        });
                    }
                    const prevTs = Number(marker._mesh_last_seen_ts || 0);
                    if (lastSeenTs > 0 && prevTs > 0 && lastSeenTs > prevTs + 0.5) {
                        window.meshPulseMarker(marker, 9000, 1000);
                    }
                    marker._mesh_last_seen_ts = lastSeenTs;
                    marker._mesh_hops = (hops === null || hops === undefined) ? null : Number(hops);
                    marker._mesh_popup_html = popup;
                    marker._mesh_node_label = label;
                }
            } catch (e) { }
        };

        window.meshOpenNodePopup = (mapElementId, nodeId) => {
            try {
                const el = (typeof getElement === 'function') ? getElement(mapElementId) : null;
                const map = el && el.map;
                if (!map || !map._meshNodeMarkers) return;
                const marker = map._meshNodeMarkers[String(nodeId ?? '')];
                if (!marker || !marker._mesh_popup_html) return;
                // Open popup manually (markers use L.popup directly, not bindPopup)
                try {
                    window.L.popup({ closeButton: true, autoClose: true })
                        .setLatLng(marker.getLatLng())
                        .setContent(marker._mesh_popup_html)
                        .openOn(map);
                } catch (e) { }
            } catch (e) { }
        };
        </script>
        <style>
            body {
                margin: 0;
                overflow: hidden;
            }
            .ag-row.mesh-row-clickable {
                cursor: pointer;
            }
            .matrix-log {
                background-color: black;
                color: #00FF00;
                font-family: 'Courier New', monospace;
                padding: 10px;
                height: 100%;
                overflow-y: auto;
                font-size: 0.85em;
            }
            .dashboard-card {
                height: 100%;
                display: flex;
                flex-direction: column;
            }
            .support-link {
                color: white;
                text-decoration: none;
            }
            .language-select .q-field__native,
            .language-select .q-field__append {
                color: white !important;
            }
            .language-select .q-menu .q-item__label {
                text-transform: uppercase;
            }
            .language-select .q-field__native {
                text-align: center;
            }
            .q-dialog .q-scrollarea__content {
                max-width: 100%;
                overflow-x: hidden;
            }

            .q-dialog .q-scrollarea__container {
                overflow-x: hidden;
            }
            .mesh-label-text, .mesh-city-text {
                background: transparent !important;
                border: 0 !important;
                box-shadow: none !important;
                color: #111 !important;
                font-weight: 600;
                font-size: 12px;
                padding: 0 !important;
                margin: 0 !important;
                pointer-events: none !important;  /* IMPORTANT: don't steal clicks from node markers */
                user-select: none !important;
            }
            .mesh-city-text {
                font-weight: 500;
                font-size: 11px;
            }
            .mesh-offline-label-canvas {
                position: absolute;
                top: 0;
                left: 0;
                pointer-events: none;
            }
            .mesh-offline-loading .q-dialog__backdrop {
                background: rgba(0, 0, 0, 0.78) !important;
            }
            .mesh-node-divicon {
                background: transparent !important;
                border: 0 !important;
            }
            .mesh-node-marker {
                width: 35px;
                height: 35px;
                border-radius: 999px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: 800;
                font-size: 9px;
                letter-spacing: 0.5px;
                color: rgba(255,255,255,0.98);
                border: 1px solid rgba(116,116,116,0.92);
                box-shadow: 0 8px 18px rgba(0,0,0,0.28);
                position: relative;
                user-select: none;
            }
            .mesh-node-text {
                pointer-events: none;
                text-shadow: 0 1px 2px rgba(0,0,0,0.35);
            }
            .mesh-node-green { background: #4CAF50; }
            .mesh-node-yellow { background: #FBBF24; color: rgba(17,24,39,0.95); border-color: rgba(255,255,255,0.92); }
            .mesh-node-yellow .mesh-node-text { text-shadow: none; }
            .mesh-node-orange { background: #FB923C; }
            .mesh-node-direct   { border: 3px solid #00f5ff !important; box-shadow: 0 0 0 2px rgba(0,245,255,0.30), 0 0 10px 2px rgba(0,245,255,0.22), 0 8px 18px rgba(0,0,0,0.28); }
            .mesh-node-indirect { border: 1px solid rgba(116,116,116,0.92) !important; }
            .mesh-node-unknown  { border: 1.5px dashed rgba(200,200,200,0.72) !important; }

            /* MeshCore Marker Shapes */
            .mesh-node-companion { border-radius: 999px; }
            .mesh-node-repeater { border-radius: 0; clip-path: polygon(50% 0%, 0% 100%, 100% 100%); width: 40px !important; height: 40px !important; }
            .mesh-node-room-server { border-radius: 4px; width: 38px !important; height: 38px !important; }

            .mesh-node-relay-capable { border: 4px double #ffffff !important; }

            .mesh-node-marker.mesh-pulse::after {
                content: '';
                position: absolute;
                left: 50%;
                top: 50%;
                width: 100%;
                height: 100%;
                border-radius: 999px;
                transform: translate(-50%, -50%) scale(1);
                opacity: 0.75;
                box-shadow: 0 0 0 3px rgba(255,255,255,0.65);
                animation: meshPulse 1.4s ease-out 1;
                pointer-events: none;
            }
            @keyframes meshPulse {
                0% { transform: translate(-50%, -50%) scale(1); opacity: 0.75; }
                100% { transform: translate(-50%, -50%) scale(2.35); opacity: 0; }
            }

            .mesh-node-legend {
                background: rgba(255,255,255,0.92);
                border: 1px solid rgba(0,0,0,0.12);
                border-radius: 10px;
                padding: 8px 10px;
                font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
                font-size: 12px;
                color: #111827;
                box-shadow: 0 8px 18px rgba(0,0,0,0.20);
            }
            .mesh-node-legend-title {
                font-weight: 700;
                margin-bottom: 6px;
            }
            .mesh-node-legend-row {
                display: flex;
                align-items: center;
                gap: 8px;
                line-height: 1.2;
                margin-top: 4px;
            }
            .mesh-node-legend-swatch {
                width: 14px;
                height: 14px;
                border-radius: 999px;
                border: 2px solid rgba(255,255,255,0.92);
                box-shadow: 0 2px 6px rgba(0,0,0,0.18);
                display: inline-block;
            }

            .mesh-theme-toggle {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                height: 30px;
                padding: 0 10px;
                border-radius: 999px;
                border: 1px solid rgba(255,255,255,0.25);
                background: linear-gradient(135deg, rgba(250,204,21,0.35), rgba(251,146,60,0.25));
                box-shadow: 0 10px 25px rgba(0,0,0,0.22);
                cursor: pointer;
                user-select: none;
                transition: background 180ms ease, border-color 180ms ease, transform 100ms ease;
            }
            .mesh-theme-toggle:hover {
                transform: translateY(-1px);
            }
            .mesh-theme-toggle:active {
                transform: translateY(0);
            }
            .mesh-theme-toggle.is-dark {
                border-color: rgba(59,130,246,0.45);
                background: linear-gradient(135deg, rgba(30,58,138,0.55), rgba(2,6,23,0.85));
                box-shadow: 0 10px 25px rgba(2,6,23,0.55);
            }
            .mesh-theme-icon {
                font-family: 'Material Icons';
                font-size: 18px;
                line-height: 18px;
                display: inline-block;
                transition: opacity 180ms ease;
            }
            .mesh-theme-icon.sun { color: #fde68a; text-shadow: 0 1px 10px rgba(250,204,21,0.35); }
            .mesh-theme-icon.moon { color: #93c5fd; text-shadow: 0 1px 10px rgba(59,130,246,0.35); }

            .mesh-dark body {
                background: #0b1220;
                color: #e5e7eb;
            }
            .mesh-dark .q-layout,
            .mesh-dark .q-page-container {
                background: #0b1220 !important;
                color: #e5e7eb;
            }
            .mesh-dark .q-tab-panels,
            .mesh-dark .q-tab-panel,
            .mesh-dark .nicegui-tab-panel {
                background: #0f172a !important;
                color: #e5e7eb !important;
            }
            .mesh-dark .q-card,
            .mesh-dark .q-dialog__inner > div {
                background: #0f172a !important;
                color: #e5e7eb !important;
                border: 1px solid rgba(148,163,184,0.16);
            }
            .mesh-dark .q-menu,
            .mesh-dark .q-list {
                background: #0f172a !important;
                color: #e5e7eb !important;
                border: 1px solid rgba(148,163,184,0.16);
            }
            .mesh-dark .q-item__label,
            .mesh-dark .q-item__section {
                color: #e5e7eb !important;
            }
            .mesh-dark .q-separator {
                background: rgba(148,163,184,0.16) !important;
            }
            .mesh-dark .leaflet-popup-content-wrapper,
            .mesh-dark .leaflet-popup-tip {
                background: #0f172a !important;
                color: #e5e7eb !important;
                border: 1px solid rgba(148,163,184,0.16);
            }
            .mesh-dark .leaflet-control-attribution {
                background: rgba(2,6,23,0.62) !important;
                color: rgba(226,232,240,0.8) !important;
            }
            .mesh-dark .mesh-node-legend {
                background: rgba(15,23,42,0.92);
                border-color: rgba(148,163,184,0.18);
                color: #e5e7eb;
            }

            .mesh-muted { color: #4b5563; }
            .mesh-chat-meta { font-size: 0.75rem; color: #6b7280; }

            .mesh-dark .mesh-muted { color: #94a3b8 !important; }
            .mesh-dark .mesh-chat-meta { color: #94a3b8 !important; }

            .mesh-dark .bg-gray-50 { background-color: rgba(15,23,42,0.92) !important; }
            .mesh-dark .bg-gray-100 { background-color: #0f172a !important; }
            .mesh-dark .bg-slate-50 { background-color: rgba(15,23,42,0.92) !important; }

            .mesh-dark .text-gray-500 { color: #94a3b8 !important; }
            .mesh-dark .text-gray-600 { color: #cbd5e1 !important; }
            .mesh-dark .text-gray-700 { color: #e5e7eb !important; }
            .mesh-dark .text-slate-900 { color: #e5e7eb !important; }
            .mesh-dark .text-blue-500 { color: #60a5fa !important; }
            .mesh-dark .hover\\:text-blue-600:hover { color: #93c5fd !important; }

            .mesh-dark .border,
            .mesh-dark .border-b {
                border-color: rgba(148,163,184,0.16) !important;
            }

            .mesh-dark .q-field__control,
            .mesh-dark .q-field__native,
            .mesh-dark .q-field__marginal {
                color: #e5e7eb !important;
            }
            .mesh-dark .q-field--filled .q-field__control,
            .mesh-dark .q-field--outlined .q-field__control {
                background: rgba(2,6,23,0.55) !important;
            }
            .mesh-dark .q-field__control:before,
            .mesh-dark .q-field__control:after {
                border-color: rgba(148,163,184,0.22) !important;
            }
            .mesh-dark .q-field__label {
                color: #cbd5e1 !important;
            }
            .mesh-dark .q-field--highlighted .q-field__label,
            .mesh-dark .q-field--focused .q-field__label {
                color: #93c5fd !important;
            }
            .mesh-dark .q-field__bottom,
            .mesh-dark .q-field__messages,
            .mesh-dark .q-field__hint {
                color: #94a3b8 !important;
            }

            .mesh-dark .q-message-text {
                color: #e5e7eb !important;
            }
            .mesh-dark .q-message-name {
                color: #cbd5e1 !important;
            }

            .mesh-dark .ag-root-wrapper,
            .mesh-dark .ag-root-wrapper-body,
            .mesh-dark .ag-center-cols-clipper {
                background: #0b1220 !important;
                color: #e5e7eb !important;
            }
            .mesh-dark .ag-header,
            .mesh-dark .ag-header-row,
            .mesh-dark .ag-header-cell,
            .mesh-dark .ag-header-group-cell {
                background: #0f172a !important;
                color: #e5e7eb !important;
                border-color: rgba(148,163,184,0.16) !important;
            }
            .mesh-dark .ag-row {
                background: #0b1220 !important;
                color: #e5e7eb !important;
                border-color: rgba(148,163,184,0.10) !important;
            }
            .mesh-dark .ag-row-hover {
                background: rgba(59,130,246,0.10) !important;
            }
            .mesh-dark .ag-cell {
                border-color: rgba(148,163,184,0.08) !important;
            }
            .mesh-dark .ag-paging-panel,
            .mesh-dark .ag-status-bar {
                background: #0f172a !important;
                color: #e5e7eb !important;
                border-color: rgba(148,163,184,0.16) !important;
            }
            .mesh-dark .ag-input-field-input,
            .mesh-dark .ag-filter-filter,
            .mesh-dark .ag-text-field-input {
                background: rgba(2,6,23,0.55) !important;
                color: #e5e7eb !important;
                border-color: rgba(148,163,184,0.22) !important;
            }
            .mesh-dark .ag-popup-child {
                background: #0f172a !important;
                color: #e5e7eb !important;
                border: 1px solid rgba(148,163,184,0.16) !important;
            }
            .mesh-dark .leaflet-container {
                background: #0b1220 !important;
            }
            .mesh-dark .leaflet-tile-pane {
                filter: invert(100%) hue-rotate(200deg) brightness(1.8) contrast(0.9) saturate(0.5);
            }

            .mesh-copy-btn {
                background: rgba(255,255,255,0.9);
            }
            .mesh-dark .mesh-copy-btn {
                background: rgb(67 67 67 / 90%);
                border-color: rgba(148,163,184,0.22) !important;
                color: #e5e7eb;
            }

            .mesh-dark * {
                scrollbar-color: rgba(148,163,184,0.35) rgba(2,6,23,0.55);
            }
            .mesh-dark *::-webkit-scrollbar {
                width: 10px;
                height: 10px;
            }
            .mesh-dark *::-webkit-scrollbar-track {
                background: rgba(2,6,23,0.55);
            }
            .mesh-dark *::-webkit-scrollbar-thumb {
                background: rgba(148,163,184,0.35);
                border-radius: 10px;
                border: 2px solid rgba(2,6,23,0.55);
            }
            .mesh-dark *::-webkit-scrollbar-thumb:hover {
                background: rgba(148,163,184,0.52);
            }
            /* Stabilizza AG Grid per evitare loop di ridimensionamento */
            .ag-root-wrapper {
                contain: strict; /* Impedisce modifiche di layout esterne */
            }
            .ag-body-viewport, .ag-body-horizontal-scroll-viewport {
                overflow-y: scroll !important;
                overflow-x: auto !important;
            }
        </style>
        <script>
        (function () {
            function ensurePane(map) {
                try {
                    if (!map.getPane('meshOfflineLabels')) {
                        var pane = map.createPane('meshOfflineLabels');
                        pane.style.zIndex = 450;
                        pane.style.pointerEvents = 'none';
                        try { pane.classList.add('leaflet-zoom-animated'); } catch (e) { }
                    }
                } catch (e) { }
            }

            function createLayer(map) {
                ensurePane(map);
                var Layer = L.Layer.extend({
                    initialize: function () {
                        this._labels = [];
                        this._zoom = null;
                    },
                    onAdd: function (map) {
                        this._map = map;
                        var pane = map.getPane('meshOfflineLabels') || map.getPane('overlayPane');
                        this._canvas = L.DomUtil.create('canvas', 'mesh-offline-label-canvas', pane);
                        this._ctx = this._canvas.getContext('2d');
                        try { this._canvas.style.opacity = '1'; } catch (e) { }
                        this._updateSize();
                        map.on('move resize zoomend', this._redraw, this);
                        map.on('zoomstart', this._hide, this);
                        map.on('zoomend', this._show, this);
                        this._redraw();
                    },
                    onRemove: function (map) {
                        map.off('move resize zoomend', this._redraw, this);
                        map.off('zoomstart', this._hide, this);
                        map.off('zoomend', this._show, this);
                        if (this._canvas && this._canvas.parentNode) {
                            this._canvas.parentNode.removeChild(this._canvas);
                        }
                        this._map = null;
                        this._canvas = null;
                        this._ctx = null;
                    },
                    _hide: function () {
                        try { if (this._canvas) this._canvas.style.opacity = '0'; } catch (e) { }
                    },
                    _show: function () {
                        try { if (this._canvas) this._canvas.style.opacity = '1'; } catch (e) { }
                        this._redraw();
                    },
                    setLabels: function (labels, zoom) {
                        this._labels = Array.isArray(labels) ? labels : [];
                        this._zoom = zoom;
                        this._redraw();
                    },
                    _updateSize: function () {
                        if (!this._map || !this._canvas) return;
                        var size = this._map.getSize();
                        var ratio = window.devicePixelRatio || 1;
                        this._canvas.width = Math.round(size.x * ratio);
                        this._canvas.height = Math.round(size.y * ratio);
                        this._canvas.style.width = size.x + 'px';
                        this._canvas.style.height = size.y + 'px';
                        if (this._ctx) {
                            this._ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
                        }
                    },
                    _redraw: function () {
                        if (!this._map || !this._canvas || !this._ctx) return;
                        if (this._map._animatingZoom || this._map._zooming) return;
                        this._updateSize();
                        var ctx = this._ctx;
                        var size = this._map.getSize();
                        try {
                            var topLeft = this._map.containerPointToLayerPoint([0, 0]);
                            L.DomUtil.setPosition(this._canvas, topLeft);
                        } catch (e) { }
                        ctx.clearRect(0, 0, size.x, size.y);
                        var zoom = this._zoom;
                        if (zoom === null || zoom === undefined) {
                            zoom = this._map.getZoom();
                        }
                        var topLeft2 = null;
                        try { topLeft2 = this._map.containerPointToLayerPoint([0, 0]); } catch (e) { }
                        var placed = [];
                        function overlaps(r) {
                            for (var i = 0; i < placed.length; i++) {
                                var p = placed[i];
                                if (!(r.x + r.w < p.x || p.x + p.w < r.x || r.y + r.h < p.y || p.y + p.h < r.y)) {
                                    return true;
                                }
                            }
                            return false;
                        }

                        for (var i = 0; i < this._labels.length; i++) {
                            var lab = this._labels[i];
                            if (!lab || typeof lab.text !== 'string') continue;
                            var lat = lab.lat;
                            var lon = lab.lon;
                            if (typeof lat !== 'number' || typeof lon !== 'number') continue;
                            var pt;
                            try {
                                var lp = this._map.latLngToLayerPoint([lat, lon]);
                                if (topLeft2) {
                                    pt = lp.subtract(topLeft2);
                                } else {
                                    pt = this._map.latLngToContainerPoint([lat, lon]);
                                }
                            } catch (e) {
                                pt = this._map.latLngToContainerPoint([lat, lon]);
                            }
                            if (!pt) continue;
                            if (pt.x < -50 || pt.y < -50 || pt.x > size.x + 50 || pt.y > size.y + 50) continue;

                            var kind = lab.kind || 'label';
                            var fontSize = (kind === 'city') ? 11 : 12;
                            var weight = (kind === 'city') ? '500' : '600';
                            var darkMode = false;
                            try { darkMode = document.documentElement.classList.contains('mesh-dark'); } catch (e) { }
                            var color = darkMode ? '#e5e7eb' : '#111';
                            if (kind === 'country') {
                                fontSize = 12;
                                weight = '700';
                            }
                            if (kind === 'province') {
                                fontSize = 10;
                                weight = '600';
                                color = darkMode ? '#94a3b8' : '#444';
                            }
                            ctx.font = weight + ' ' + fontSize + 'px sans-serif';
                            ctx.fillStyle = color;
                            ctx.textAlign = 'center';
                            ctx.textBaseline = 'middle';
                            var w = ctx.measureText(lab.text).width;
                            var h = fontSize + 4;
                            var rect = { x: pt.x - w / 2 - 2, y: pt.y - h / 2, w: w + 4, h: h };
                            if (overlaps(rect)) continue;
                            placed.push(rect);
                            ctx.fillText(lab.text, pt.x, pt.y);
                        }
                    },
                });

                var layer = new Layer();
                layer.addTo(map);
                return layer;
            }

            window.meshOfflineLabels = {
                set: function (map, labels, zoom) {
                    if (!window.L) return;
                    this._pending = { labels: labels, zoom: zoom };
                    var m = map || window.mesh_offline_map;
                    if (!m) return;
                    if (!m._meshOfflineLabelLayer) {
                        m._meshOfflineLabelLayer = createLayer(m);
                    }
                    try {
                        m._meshOfflineLabelLayer.setLabels(labels, zoom);
                    } catch (e) { }
                },
            };
        })();
        </script>
    ''')

    load_languages()
    js_i18n = {
        "tooltip.openinmap.title": translate("tooltip.openinmap.title", "Click to open in map"),
        "tooltip.copytext.title": translate("tooltip.copytext.title", "Copy"),
        "contextmenu.copylink": translate("contextmenu.copylink", "Copy Link"),
        "notification.positive.copytext": translate("notification.positive.copytext", "Copied to clipboard"),
        "notification.error.copytext": translate("notification.error.copytext", "Copy text failed"),
        "map.legend.lastheard": translate("map.legend.lastheard", "Last Heard"),
        "button.toggletheme": translate("button.toggletheme", "Toggle Theme"),
        "map.overlap.nodes_here": translate("map.overlap.nodes_here", "nodes here"),
    }
    ui.run_javascript(f'window.mesh_i18n = {json.dumps(js_i18n)};')

    with ui.header().classes('bg-slate-900 text-white'):
        with ui.row().classes('items-center gap-3 self-center'):
            ui.label(f'{PROGRAM_NAME} - {PROGRAM_SHORT_DESC}').classes('text-xl font-bold')
            ui.html(f'''
                <button id="mesh-theme-toggle" class="mesh-theme-toggle" type="button" onclick="try{{window.meshToggleTheme();}}catch(e){{}}" title="{translate("button.toggletheme", "Toggle Theme")}">
                    <span class="mesh-theme-icon sun">light_mode</span>
                    <span class="mesh-theme-icon moon">dark_mode</span>
                </button>
            ''', sanitize=False)
        ui.space()
        with ui.link('', DONATION_URL, new_tab=True).classes('mr-4 support-link inline-flex items-center whitespace-nowrap self-center'):
            ui.icon('favorite').classes('text-pink-400 mr-1')
            ui.label(translate("header.support", "Support the project"))
        available_langs = get_available_languages()
        pending_language_change = {"value": None}
        with ui.dialog() as language_change_dialog:
            with ui.card().classes('w-110'):
                ui.label(
                    translate("popup.language_change.requires_disconnect.title", "Disconnect required")
                ).classes('text-lg font-bold mb-2')
                ui.label(
                    translate(
                        "popup.language_change.requires_disconnect.body",
                        "To change language, disconnect first. The app will reload to apply the new language.",
                    )
                ).classes('text-sm text-gray-700 mb-3')
                with ui.row().classes('w-full justify-end gap-2'):
                    ui.button(
                        translate("button.cancel", "Cancel"),
                        on_click=lambda: (pending_language_change.__setitem__("value", None), language_change_dialog.close()),
                    ).classes('bg-slate-200 text-slate-900')

                    async def disconnect_and_reload_for_language_change():
                        global current_language, user_language_from_config
                        requested_lang = pending_language_change.get("value")
                        pending_language_change["value"] = None
                        language_change_dialog.close()
                        if state.connected:
                            stop_connection()
                            ui.notify(translate("status.disconnected", "Disconnected"))
                        if isinstance(requested_lang, str) and requested_lang in available_langs:
                            current_language = requested_lang
                            user_language_from_config = True
                            save_user_config()
                            try:
                                await ui.run_javascript("sessionStorage.setItem('mesh_skip_shutdown','1'); location.reload()")
                            except Exception:
                                pass

                    ui.button(
                        translate("button.disconnect", "Disconnect"),
                        on_click=disconnect_and_reload_for_language_change,
                    ).classes('bg-blue-600 text-white')

        async def on_language_change(e):
            global current_language, user_language_from_config
            value = e.value
            if isinstance(value, str) and value in available_langs:
                if value == current_language:
                    return
                if state.connected:
                    if language_select_ref is not None:
                        language_select_ref.value = current_language
                    pending_language_change["value"] = value
                    language_change_dialog.open()
                    return
                current_language = value
                user_language_from_config = True
                save_user_config()
                try:
                    await ui.run_javascript("sessionStorage.setItem('mesh_skip_shutdown','1'); location.reload()")
                except Exception:
                    pass
        global language_select_ref
        language_select_ref = ui.select(
            options=available_langs,
            value=current_language if current_language in available_langs else "en",
            on_change=on_language_change,
        ).props("dense options-dense borderless").style(
            'color: white; text-transform: uppercase;'
        ).classes('mr-2 w-auto self-center language-select')
        status_label = ui.label(translate("status.disconnected", "Disconnected")).classes('text-red-500 font-bold mr-4 self-center')
        global status_label_ref
        status_label_ref = status_label
        

    if not user_language_from_config:
        async def _auto_detect_language():
            try:
                lang = await ui.run_javascript("navigator.language || navigator.userLanguage || 'en'", timeout=5.0)
                if not isinstance(lang, str):
                    return
                lang = lang.split('-')[0].lower()
                available = get_available_languages()
                if lang in available:
                    global current_language
                    current_language = lang
                    if language_select_ref is not None:
                        language_select_ref.value = lang
                    save_user_config()
                    await ui.run_javascript("sessionStorage.setItem('mesh_skip_shutdown','1'); location.reload()")
            except Exception:
                pass

        ui.timer(0.1, _auto_detect_language, once=True)

    def on_direct_ppm_change(e):
        if e.value in (None, ''):
            return
        try:
            state.direct_ppm = int(e.value)
        except (TypeError, ValueError):
            pass
        save_user_config()

    def on_direct_gain_change(e):
        if e.value in (None, ''):
            return
        try:
            state.direct_gain = int(e.value)
        except (TypeError, ValueError):
            pass
        save_user_config()

    def update_config_field(name):
        def inner(e):
            setattr(state, name, e.value)
            save_user_config()
        return inner

    def on_autosave_interval_change(e):
        try:
            if e.value in (None, ''):
                v = 0
            else:
                v = int(e.value)
        except Exception:
            v = 0
        if v < 0:
            v = 0
        state.autosave_interval_sec = v
        save_user_config()

    def _rtlsdr_error_ui_tick():
        if not state.rtlsdr_error_pending:
            return
        state.rtlsdr_error_pending = False
        ui.notify(translate("notification.error.rtlsdrdevice", "SDR device error: Wrong RTL-SDR device index."), color="negative")
        show_rtlsdr_device_error_dialog()

    ui.timer(0.2, _rtlsdr_error_ui_tick)

    with ui.dialog() as connection_dialog:
        with ui.card().classes('w-110').style('height: 100%; max-height: 760px'):
            with ui.scroll_area().style('height: 100%;'):
                with ui.column().classes('w-full'):
                    ui.label(translate("panel.connection.settings.title", "Connection Settings")).classes('text-lg font-bold mb-2')

                    with ui.row().classes('w-full items-center mb-2'):
                        ui.label(translate("panel.connection.settings.network_mode", "Network Ecosystem:")).classes('mr-2 font-bold')
                        network_mode_toggle = ui.toggle(
                            {"meshtastic": "Meshtastic", "meshcore": "MeshCore"},
                            value=state.network_mode,
                            on_change=lambda e: setattr(state, 'network_mode', e.value) or save_user_config() or connection_dialog.update()
                        ).props('dense rounded unelevated color=blue')

                    with ui.tabs().classes('w-full mb-2') as tabs:
                        tab_direct = ui.tab(translate("panel.connection.settings.internaltab", "Internal"))
                        tab_ext = ui.tab(translate("panel.connection.settings.externaltab", "External"))

                    with ui.tab_panels(tabs, value=tab_direct).classes('w-full'):
                        with ui.tab_panel(tab_direct):
                          if state.network_mode == "meshcore":
                            ui.label(translate("panel.connection.settings.meshcore.internal.title", "MeshCore SDR Engine")).classes('font-bold mb-0')
                            ui.markdown(translate("panel.connection.settings.meshcore.internal.help", "Configure MeshCore radio parameters for the internal engine.")).classes('text-sm text-gray-600')

                            # MeshCore Region/Preset select
                            mc_region_options = {k: k.replace("_", "/") for k in MESHCORE_PRESETS.keys()}
                            mc_region_options["CUSTOM"] = translate("ui.custom", "Custom")

                            def _on_mc_region_change(e):
                                state.meshcore_region = e.value
                                if e.value != "CUSTOM":
                                    first_preset = list(MESHCORE_PRESETS[e.value].keys())[0]
                                    state.meshcore_preset = first_preset
                                    # Sync custom fields with preset
                                    p = MESHCORE_PRESETS[e.value][first_preset]
                                    state.meshcore_custom_freq = p["freq"]
                                    state.meshcore_custom_sf = p["sf"]
                                    state.meshcore_custom_bw = p["bw"]
                                    state.meshcore_custom_cr = p["cr"]
                                    state.meshcore_custom_sync_word = p["sync"]
                                    state.meshcore_custom_preamble_len = p["preamble"]
                                save_user_config()
                                connection_dialog.update()

                            ui.select(
                                options=mc_region_options,
                                value=state.meshcore_region,
                                on_change=_on_mc_region_change,
                                label=translate("panel.connection.settings.internal.label.region", "Region")
                            ).props('dense').classes('w-full mb-1')

                            if state.meshcore_region != "CUSTOM":
                                mc_preset_options = {k: translate(v["description_key"]) for k, v in MESHCORE_PRESETS[state.meshcore_region].items()}

                                def _on_mc_preset_change(e):
                                    state.meshcore_preset = e.value
                                    p = MESHCORE_PRESETS[state.meshcore_region][e.value]
                                    state.meshcore_custom_freq = p["freq"]
                                    state.meshcore_custom_sf = p["sf"]
                                    state.meshcore_custom_bw = p["bw"]
                                    state.meshcore_custom_cr = p["cr"]
                                    state.meshcore_custom_sync_word = p["sync"]
                                    state.meshcore_custom_preamble_len = p["preamble"]
                                    save_user_config()
                                    connection_dialog.update()

                                ui.select(
                                    options=mc_preset_options,
                                    value=state.meshcore_preset,
                                    on_change=_on_mc_preset_change,
                                    label=translate("panel.connection.settings.internal.label.modempreset", "Preset")
                                ).props('dense').classes('w-full mb-1')

                            with ui.row().classes('w-full gap-2'):
                                ui.number(translate("panel.connection.settings.meshcore.label.freq", "Freq (MHz)"), value=state.meshcore_custom_freq, format="%.3f", on_change=lambda e: setattr(state, 'meshcore_custom_freq', e.value) or save_user_config()).props('dense').classes('flex-1')
                                ui.number(translate("panel.connection.settings.meshcore.label.sf", "SF"), value=state.meshcore_custom_sf, precision=0, on_change=lambda e: setattr(state, 'meshcore_custom_sf', e.value) or save_user_config()).props('dense').classes('w-16')

                            with ui.row().classes('w-full gap-2'):
                                ui.number(translate("panel.connection.settings.meshcore.label.bw", "BW (kHz)"), value=state.meshcore_custom_bw, format="%.1f", on_change=lambda e: setattr(state, 'meshcore_custom_bw', e.value) or save_user_config()).props('dense').classes('flex-1')
                                ui.number(translate("panel.connection.settings.meshcore.label.cr", "CR"), value=state.meshcore_custom_cr, precision=0, on_change=lambda e: setattr(state, 'meshcore_custom_cr', e.value) or save_user_config()).props('dense').classes('w-16')

                            mc_advanced = ui.checkbox(translate("panel.connection.settings.meshcore.label.advanced", "Advanced LoRa Settings")).props('dense').classes('text-xs mt-1')
                            with ui.row().classes('w-full gap-2').bind_visibility_from(mc_advanced, 'value'):
                                ui.number(translate("panel.connection.settings.meshcore.label.sync_word", "Sync Word (hex)"), value=state.meshcore_custom_sync_word, format="0x%02X", on_change=lambda e: setattr(state, 'meshcore_custom_sync_word', int(e.value)) or save_user_config()).props('dense').classes('flex-1')
                                ui.number(translate("panel.connection.settings.meshcore.label.preamble", "Preamble"), value=state.meshcore_custom_preamble_len, precision=0, on_change=lambda e: setattr(state, 'meshcore_custom_preamble_len', e.value) or save_user_config()).props('dense').classes('w-20')

                          else:
                            ui.label(translate("panel.connection.settings.internal.title", "Internal SDR Engine")).classes('font-bold mb-0')
                            ui.markdown(translate("panel.connection.settings.internal.help", 'The app manages the internal SDR engine for you.<br> Just select Region, Channel, PPM for your device and a suitable RF Gain.')).classes('text-sm text-gray-600')
                            _saved_device_args = str(getattr(state, "direct_device_args", "rtl=0") or "").strip()
                            _direct_device_options = {
                                "": translate("panel.connection.settings.internal.device.auto", "Auto (first detected)"),
                            }
                            if _saved_device_args and _saved_device_args not in _direct_device_options:
                                m_driver = re.match(r"^([a-zA-Z_]+)=(\d+)", _saved_device_args)
                                if m_driver:
                                    drv = m_driver.group(1).upper()
                                    idx = m_driver.group(2)
                                    _direct_device_options[_saved_device_args] = f"{drv} #{idx}"
                                else:
                                    _direct_device_options[_saved_device_args] = _saved_device_args
                            with ui.row().classes('w-full items-end gap-2 no-wrap device-row'):
                                def _update_device_tooltip():
                                    try:
                                        v = direct_device_select.value
                                        full = (direct_device_select.options or {}).get(v)
                                        direct_device_tooltip.text = str(full or v or "")
                                    except Exception:
                                        pass

                                def on_direct_device_change(e):
                                    state.direct_device_args = e.value
                                    save_user_config()
                                    _update_device_tooltip()

                                def _refresh_slot_select():
                                    slot_select.options = _get_slot_options()
                                    slot_select.update()
                                def _on_region_change(e):
                                    update_config_field('direct_region')(e)
                                    _refresh_slot_select()
                                def _on_preset_change(e):
                                    update_config_field('direct_preset')(e)
                                    _refresh_slot_select()

                                with ui.element('div').style('flex: 1 1 0; min-width: 0; max-width: calc(100% - 90px); overflow: hidden;'):
                                    direct_device_select = ui.select(
                                        options=_direct_device_options,
                                        value=_saved_device_args if _saved_device_args in _direct_device_options else "",
                                        on_change=on_direct_device_change,
                                        label=translate("panel.connection.settings.internal.label.device", "SDR Device"),
                                    ).props('dense').classes('w-full device-select')
                                    direct_device_tooltip = ui.tooltip("")
                                    _update_device_tooltip()
                                refresh_devices_btn = ui.button(
                                    translate("panel.connection.settings.internal.button.refresh_devices", "Refresh"),
                                ).props('dense').classes('bg-slate-200 text-slate-900').style('flex-shrink: 0; white-space: nowrap;')
                            direct_device_status = ui.label("").classes('text-xs text-gray-500 mb-0')
                            _scan_state = {'running': False, 'cancel': False}
                            with ui.row().classes('w-full items-center gap-2 mt-0 mb-0'):
                                bias_tee_checkbox = ui.checkbox(
                                    translate("panel.connection.settings.internal.label.bias_tee", "Enable Bias-T / Antenna Power"),
                                    value=getattr(state, 'direct_bias_tee', False),
                                    on_change=lambda e: setattr(state, 'direct_bias_tee', e.value) or save_user_config()
                                ).props('dense').classes('text-xs bias-tee-cb')
                                ui.icon('warning').classes('text-orange-500 text-xs mt-0')
                                ui.tooltip(
                                    translate(
                                        "panel.connection.settings.internal.tooltip.bias_tee",
                                        "⚠ Enable only if you know what you are doing!\nThis powers the antenna port (Bias-T).\nConnecting unsupported hardware may damage your SDR or antenna."
                                    )
                                ).classes('whitespace-pre-line')
                            region_options = {k: f"{k} — {v['description']}" for k, v in MESHTASTIC_REGIONS.items() if k != "UNSET"}
                            # Region select
                            ui.select(
                                options=region_options,
                                value=state.direct_region if state.direct_region in region_options else "EU_868",
                                on_change=_on_region_change,
                                label=translate("panel.connection.settings.internal.label.region", "Region")
                            ).props('dense options-dense').classes('w-full mb-0')
                            # Preset select
                            preset_options = {k: f"{k} — {v['description']}" for k, v in MESHTASTIC_MODEM_PRESETS.items()}
                            preset_options["ALL"] = translate("panel.connection.settings.internal.option.allpresets", "ALL — Scan all presets simultaneously (Intensive)")
                            ui.select(
                                options=preset_options,
                                value=state.direct_preset if state.direct_preset in preset_options else "LONG_FAST",
                                on_change=_on_preset_change,
                                label=translate("panel.connection.settings.internal.label.modempreset", "Modem Preset")
                            ).props('dense options-dense').classes('w-full mb-0')
                            # Helper function to calculate slot options based on current region and preset.
                            def _get_slot_options():
                                import math
                                r = MESHTASTIC_REGIONS.get(state.direct_region)
                                p = MESHTASTIC_MODEM_PRESETS.get(state.direct_preset)
                                if not r or not p:
                                    return {"0": "Auto (hash-based)"}
                                wide = r.get("wide_lora", False)
                                bw_mhz = (p["bw_wide"] if wide else p["bw_narrow"]) / 1000.0
                                spacing = r.get("spacing", 0.0)
                                slot_w = spacing + bw_mhz
                                if slot_w <= 0:
                                    return {"0": "Auto (hash-based)"}
                                num_slots = int(math.floor((r["freq_end"] - r["freq_start"]) / slot_w))
                                if num_slots < 1:
                                    return {"0": "Auto (hash-based) — preset not compatible with this region"}
                                opts = {"0": f"Auto (hash → slot {(_djb2_hash(state.direct_channel_name or p['channel_name']) % num_slots) + 1})"}
                                for i in range(1, num_slots + 1):
                                    ch_num = i - 1
                                    freq = r["freq_start"] + bw_mhz / 2.0 + ch_num * slot_w
                                    opts[str(i)] = f"Slot {i} — {freq:.3f} MHz"
                                return opts
                            # Frequency Slot (0=auto)
                            slot_select = ui.select(
                                options=_get_slot_options(),
                                value=str(getattr(state, 'direct_frequency_slot', 0)),
                                on_change=lambda e: setattr(state, 'direct_frequency_slot', int(e.value or 0)) or save_user_config(),
                                label=translate("panel.connection.settings.internal.slot", "Frequency Slot (0 = auto/hash)")
                            ).props('dense options-dense').classes('w-full mb-0')
                            # Info dynamically updated
                            with ui.column().classes('gap-0 mb-1') as freq_info_container:
                                freq_info_label = ui.label("").classes('text-xs text-gray-500')
                                freq_info_warning = ui.label("").classes('text-xs text-orange-500 hidden')
                            def _update_freq_info():
                                r = state.direct_region
                                p = state.direct_preset
                                slot = getattr(state, 'direct_frequency_slot', 0)
                                ch = getattr(state, 'direct_channel_name', '') or None
                                calc = meshtastic_calc_freq(r, p, slot, ch)
                                if calc.get("all_presets"):
                                    freq_info_label.text = translate(
                                        "panel.connection.settings.internal.info.allpresets.scan",
                                        "→ Scanning all {n} presets across the {band} band"
                                    ).format(n=len(MESHTASTIC_MODEM_PRESETS), band=r)
                                    freq_info_label.classes(remove='text-red-500 text-orange-500', add='text-gray-500')
                                    freq_info_warning.text = translate(
                                        "panel.connection.settings.internal.info.allpresets.warning",
                                        "⚠ Decoding all presets simultaneously is resource-intensive and may affect system performance."
                                    )
                                    freq_info_warning.classes(remove='hidden')
                                elif calc.get("valid"):
                                    freq_info_label.text = (
                                        f"→ {calc['center_freq_mhz']:.3f} MHz | "
                                        f"Slot {calc['slot_used']}/{calc['num_slots']} | "
                                        f"BW {calc['bw_khz']:.0f}kHz SF{calc['sf']} CR4/{calc['cr']} | "
                                        f"CH: \"{calc['channel_name']}\""
                                    )
                                    freq_info_label.classes(remove='text-red-500 text-orange-500', add='text-gray-500')
                                    freq_info_warning.text = ""
                                    freq_info_warning.classes(add='hidden')
                                else:
                                    freq_info_label.text = f"⚠ {calc.get('error', 'Invalid combination')}"
                                    freq_info_label.classes(remove='text-gray-500 text-orange-500', add='text-red-500')
                                    freq_info_warning.text = ""
                                    freq_info_warning.classes(add='hidden')
                            # Update info when relevant parameters change
                            ui.timer(0.5, _update_freq_info)
                            with ui.expansion(
                                translate("panel.connection.settings.internal.label.default_channel_settings", "Edit default channel settings")
                            ).classes('w-full mb-0 text-xs'):
                                # Alert explaining default channel
                                ui.label(
                                    translate(
                                        "panel.connection.settings.internal.label.default_channel_alert",
                                        "⚠ This is the default channel. It is recommended to leave these settings as-is. "
                                        "To monitor additional channels, click '+ Add Channel' above the chat area."
                                    )
                                ).classes('text-xs text-orange-500 mb-2')
                                direct_channel_input = ui.input(
                                    translate("panel.connection.settings.internal.channelname", "Channel Name (blank = preset default)"),
                                    value=getattr(state, 'direct_channel_name', ''),
                                    on_change=update_config_field('direct_channel_name')
                                ).classes('w-full mb-0')
                                direct_key_input = ui.input(
                                    translate("panel.connection.settings.internal.label.aes_key", "AES Public Key (Base64)"),
                                    value=state.direct_key_b64,
                                    on_change=update_config_field('direct_key_b64')
                                ).classes('w-full mb-1')
                                ui.label(
                                    translate(
                                        "panel.connection.settings.internal.label.aes_key.hint",
                                        "Key size is auto-detected from the Base64 length (16 bytes = 128-bit, 32 bytes = 256-bit). 'AQ==' = Meshtastic default."
                                    )
                                ).classes('text-xs text-gray-400 mt-0 mb-1')
                            ui.number(
                                translate("panel.connection.settings.internal.label.ppm", "PPM correction"),
                                value=state.direct_ppm,
                                on_change=on_direct_ppm_change
                            ).props('dense').classes('w-full mb-0')
                            ui.number(
                                translate("panel.connection.settings.internal.label.rf_gain", "RF Gain"),
                                value=state.direct_gain,
                                on_change=on_direct_gain_change
                            ).props('dense').classes('w-full mb-0')
                            with ui.row().classes('w-full items-center gap-1 mb-0'):
                                ui.input(
                                    translate("panel.connection.settings.internal.label.port", "Port (don't change if everything works)") + " ⓘ",
                                    value=state.direct_port,
                                    on_change=update_config_field('direct_port')
                                ).classes('flex-1')
                                ui.tooltip(
                                    translate(
                                        "panel.connection.settings.internal.label.port.tooltip",
                                        "Only change this if the internal engine cannot bind the default port, "
                                        "or if you are running multiple parallel instances of this application "
                                        "from different folders with different SDR devices."
                                    )
                                ).classes('whitespace-pre-line')
                            with ui.row().classes('w-full justify-end gap-2'):
                                ui.button(translate("button.cancel", "Cancel"), on_click=connection_dialog.close).classes('bg-slate-200 text-slate-900')
                                def _on_connect_direct_click():
                                    # If a scan is running and "Auto" is used, cancel it immediately.
                                    if _scan_state['running']:
                                        _scan_state['cancel'] = True
                                    _do_connect_direct()
                                    connection_dialog.close()

                                connect_direct_btn = ui.button(
                                    translate("button.connect", "Connect"),
                                    on_click=_on_connect_direct_click
                                ).classes('bg-blue-600 text-white')

                        with ui.tab_panel(tab_ext):
                            ui.label(translate("panel.connection.settings.external.title", "External GNU Radio / ZMQ stream")).classes('font-bold mb-1')
                            ui.label(translate("panel.connection.settings.external.help1", "Requires an external specific (our custom frame) GNU Radio flowgraph with a ZMQ PUB block.")).classes('text-sm text-gray-600')
                            ui.label(translate("panel.connection.settings.external.help2", "Configure here the IP, port and AES key of that source.")).classes('text-sm text-gray-600 mb-1')
                            external_ip_input = ui.input(
                                translate("panel.connection.settings.external.label.ip", "IP Address"),
                                value=state.external_ip,
                                on_change=update_config_field('external_ip')
                            ).classes('w-full mb-1')
                            external_port_input = ui.input(
                                translate("panel.connection.settings.external.label.port", "Port"),
                                value=state.external_port,
                                on_change=update_config_field('external_port')
                            ).classes('w-full mb-1')
                            external_key_input = ui.input(
                                translate("panel.connection.settings.external.label.aes_key", "AES Key (Base64)"),
                                value=state.external_key_b64,
                                on_change=update_config_field('external_key_b64')
                            ).classes('w-full mb-2')
                            ui.label(
                                translate(
                                    "panel.connection.settings.internal.label.aes_key.hint",
                                    "Key size is auto-detected from the Base64 length (16 bytes = 128-bit, 32 bytes = 256-bit). 'AQ==' = Meshtastic default."
                                )
                            ).classes('text-xs text-gray-400 mt-0 mb-1')
                            with ui.row().classes('w-full justify-end gap-2'):
                                ui.button(translate("button.cancel", "Cancel"), on_click=connection_dialog.close).classes('bg-slate-200 text-slate-900')
                                ui.button(translate("button.connect", "Connect"), on_click=lambda: ( _do_connect_external(), connection_dialog.close() )).classes('bg-blue-600 text-white')

    async def _refresh_direct_devices():
        # If a scan is already running, signal cancellation and wait
        if _scan_state['running']:
            _scan_state['cancel'] = True
            # Wait max 2s for the previous scan to finish
            for _ in range(20):
                await asyncio.sleep(0.1)
                if not _scan_state['running']:
                    break

        _scan_state['running'] = True
        _scan_state['cancel'] = False

        # Disable buttons during the scan
        refresh_devices_btn.disable()
        connect_direct_btn.disable()

        # Show "scanning" label in the select
        scanning_label = translate(
            "panel.connection.settings.internal.device.scanning", "Scanning devices..."
        )
        direct_device_select.options = {"": scanning_label}
        direct_device_select.value = ""
        direct_device_select.update()
        direct_device_status.text = scanning_label

        try:
            devices, err = await asyncio.to_thread(list_internal_sdr_devices)

            # If cancellation was signaled, stop processing
            if _scan_state['cancel']:
                return

            if DEBUGGING:
                log_to_console(f"[SDRSCAN] ui_result n={len(devices)} err={err}")
            if err == "no_runtime":
                direct_device_status.text = translate("panel.connection.settings.internal.device.no_runtime", "Internal engine runtime not found.")
            elif err == "no_engine_dir":
                direct_device_status.text = translate("panel.connection.settings.internal.device.no_engine_dir", "Engine folder not found.")
            elif err:
                direct_device_status.text = translate("panel.connection.settings.internal.device.scan_failed_details", "Device scan failed (see Console Log).")
            else:
                if devices:
                    direct_device_status.text = translate(
                        "panel.connection.settings.internal.device.found",
                        "Detected devices: {n}",
                    ).format(n=len(devices))
                else:
                    direct_device_status.text = translate(
                        "panel.connection.settings.internal.device.none",
                        "No devices detected.",
                    )

            options = {
                "": translate("panel.connection.settings.internal.device.auto", "Auto (first detected)"),
            }
            for dev_args, label in devices:
                options[dev_args] = label
            state.direct_device_detected_args = list(options.keys())
            direct_device_select.options = options
            cur = getattr(state, "direct_device_args", "")
            if cur not in options:
                if cur:
                    m_driver = re.match(r"^([a-zA-Z_]+)=(\d+)", cur)
                    if m_driver:
                        drv = m_driver.group(1).upper()
                        idx = m_driver.group(2)
                        options[cur] = f"{drv} #{idx} (not detected)"
                    else:
                        options[cur] = f"{cur} (not detected)"
                    direct_device_select.options = options
                else:
                    cur = ""
                    state.direct_device_args = ""
                    save_user_config()
            direct_device_select.value = cur
            direct_device_select.update()
            _update_device_tooltip()
        except Exception:
            direct_device_status.text = translate("panel.connection.settings.internal.device.scan_failed_details", "Device scan failed (see Console Log).")
        finally:
            _scan_state['running'] = False
            refresh_devices_btn.enable()
            connect_direct_btn.enable()

    refresh_devices_btn.on_click(lambda: asyncio.create_task(_refresh_direct_devices()))
    connection_dialog.on('show', lambda: asyncio.create_task(_refresh_direct_devices()))
    def _sync_connection_dialog_fields():
        """Re-populate dialog fields with current state values on every open."""
        try:
            fields = [
                (direct_key_input,     state.direct_key_b64),
                (direct_channel_input, state.direct_channel_name),
                (external_key_input,   state.external_key_b64),
                (external_ip_input,    state.external_ip),
                (external_port_input,  state.external_port),
            ]
            for field, value in fields:
                field.value = value
                field.update()
        except Exception as e:
            log_to_console(f"[SYNC DIALOG] Error: {e}")

    connection_dialog.on('show', lambda: _sync_connection_dialog_fields())

    def _do_connect_direct():
        state.ip_address = "127.0.0.1"
        state.port = state.direct_port
        state.aes_key_b64 = state.direct_key_b64
        save_user_config()
        start_connection("direct")
        set_connection_status_ui(True, "direct")
        ui.notify(translate("notification.positive.directenginestarting", "Direct engine starting..."), color='positive')

    def _do_connect_external():
        state.ip_address = state.external_ip
        state.port = state.external_port
        state.aes_key_b64 = state.external_key_b64
        save_user_config()
        start_connection("external")
        set_connection_status_ui(True, "external")
        ui.notify(translate("notification.positive.externalconnect", "External connect..."), color='positive')

    def _import_data_from_dict(data):
        imported_nodes_count = 0
        total_nodes_in_file = 0

        def _msg_signature(msg: dict):
            if not isinstance(msg, dict):
                return None
            msg_id_val = msg.get('id', None)
            if msg_id_val is None:
                msg_id_val = msg.get('message_id', None)
            if msg_id_val is None:
                msg_id_val = msg.get('mid', None)
            if msg_id_val is not None:
                return (
                    str(msg.get('from_id', '')).strip(),
                    str(msg.get('to', '')).strip(),
                    str(msg_id_val).strip(),
                )
            return (
                str(msg.get('from_id', '')).strip(),
                str(msg.get('to', '')).strip(),
                str(msg.get('date', '')).strip(),
                str(msg.get('time', '')).strip(),
                str(msg.get('text', '')).strip(),
                str(msg.get('is_me', '')).strip(),
            )

        if "nodes" in data:
            total_nodes_in_file = len(data["nodes"])
            for k, v in data["nodes"].items():
                try:
                    node_id_int = int(k)
                    canonical_id = f"!{node_id_int:x}"
                except ValueError:
                    canonical_id = k
                v['id'] = canonical_id
                try:
                    raw_unmsg = v.get("is_unmessagable", None)
                    if raw_unmsg is None:
                        raw_unmsg = v.get("is_unmessageable", None)
                    if raw_unmsg is None:
                        raw_unmsg = v.get("isUnmessagable", None)
                    if raw_unmsg is None:
                        raw_unmsg = v.get("isUnmessageable", None)

                    if isinstance(raw_unmsg, bool):
                        v["is_unmessagable"] = raw_unmsg
                    elif isinstance(raw_unmsg, (int, float)):
                        v["is_unmessagable"] = bool(raw_unmsg)
                    elif isinstance(raw_unmsg, str):
                        s = raw_unmsg.strip().lower()
                        v["is_unmessagable"] = s in {"1", "true", "yes", "y", "on"}
                    else:
                        v["is_unmessagable"] = False
                except Exception:
                    v["is_unmessagable"] = False
                if 'last_seen_ts' not in v:
                    v['last_seen_ts'] = time.time()
                state.nodes[canonical_id] = v
                imported_nodes_count += 1

            state.nodes_updated = True
            state.nodes_list_updated = True
            state.nodes_list_force_refresh = True
            state.chat_force_refresh = True

        if "messages" in data:
            imported_msgs = data["messages"]
            existing_sigs = set()
            for existing_msg in state.messages:
                sig = _msg_signature(existing_msg)
                if sig is not None:
                    existing_sigs.add(sig)

            unique_imported_msgs = []
            for msg in imported_msgs:
                if not isinstance(msg, dict):
                    continue
                if 'from_id' not in msg:
                    sender_val = msg.get('from', '')
                    if sender_val.startswith('!'):
                        msg['from_id'] = sender_val
                    else:
                        for nid, n in state.nodes.items():
                            if n.get('short_name') == sender_val or n.get('long_name') == sender_val or f"{n.get('long_name')} ({n.get('short_name')})" == sender_val:
                                msg['from_id'] = nid
                                break

                sig = _msg_signature(msg)
                if sig is None or sig in existing_sigs:
                    continue
                existing_sigs.add(sig)
                unique_imported_msgs.append(msg)

            if unique_imported_msgs:
                state.messages.extend(unique_imported_msgs)
                state.new_messages.extend(unique_imported_msgs)
                state.chat_force_scroll = True

        if "channel_messages" in data:
            for ch_id, msgs in data["channel_messages"].items():
                if isinstance(msgs, list):
                    existing = state.channel_messages.get(ch_id, deque(maxlen=100))
                    for msg in msgs:
                        if isinstance(msg, dict):
                            existing.append(msg)
                    state.channel_messages[ch_id] = existing

        if "mesh_stats" in data:
            try:
                mesh_stats.load_from_dict(data.get("mesh_stats"))
            except Exception:
                pass

        return imported_nodes_count, total_nodes_in_file

    def _extract_meshtastic_nodes_from_info_text(content: str) -> dict:
        if not isinstance(content, str):
            raise ValueError("Invalid content type")
        marker = "Nodes in mesh:"
        idx = content.find(marker)
        if idx < 0:
            raise ValueError("No 'Nodes in mesh:' section found")
        brace_idx = content.find("{", idx)
        if brace_idx < 0:
            raise ValueError("Malformed 'Nodes in mesh:' section")
        decoder = json.JSONDecoder()
        nodes_obj, _ = decoder.raw_decode(content[brace_idx:])
        if not isinstance(nodes_obj, dict):
            raise ValueError("Nodes section is not a JSON object")
        return nodes_obj

    def _node_from_meshtastic_cli(node_id: str, node_entry: dict) -> dict:
        if not isinstance(node_id, str):
            node_id = str(node_id)
        if not isinstance(node_entry, dict):
            node_entry = {}

        user = node_entry.get("user") if isinstance(node_entry.get("user"), dict) else {}
        pos = node_entry.get("position") if isinstance(node_entry.get("position"), dict) else {}
        metrics = node_entry.get("deviceMetrics") if isinstance(node_entry.get("deviceMetrics"), dict) else {}

        num = node_entry.get("num")
        if num is None:
            try:
                if node_id.startswith("!"):
                    num = int(node_id[1:], 16)
            except Exception:
                num = None

        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is None:
            try:
                lat_i = pos.get("latitudeI")
                if lat_i is not None:
                    lat = float(lat_i) * 1e-7
            except Exception:
                lat = None
        if lon is None:
            try:
                lon_i = pos.get("longitudeI")
                if lon_i is not None:
                    lon = float(lon_i) * 1e-7
            except Exception:
                lon = None

        last_seen_ts = None
        try:
            last_heard = node_entry.get("lastHeard")
            if last_heard is not None:
                last_seen_ts = float(last_heard)
        except Exception:
            last_seen_ts = None
        if last_seen_ts is None:
            last_seen_ts = time.time()

        last_seen_str = None
        try:
            last_seen_str = datetime.fromtimestamp(last_seen_ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            last_seen_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        hops = node_entry.get("hopsAway")
        hop_label = None
        try:
            if hops is not None:
                hops = int(hops)
                hop_label = "direct" if hops == 0 else str(hops)
        except Exception:
            hops = None
            hop_label = None

        is_unmessagable = False
        try:
            if "isUnmessagable" in user:
                is_unmessagable = bool(user.get("isUnmessagable"))
            elif "isUnmessageable" in user:
                is_unmessagable = bool(user.get("isUnmessageable"))
            elif "is_unmessagable" in user:
                is_unmessagable = bool(user.get("is_unmessagable"))
            elif "is_unmessageable" in user:
                is_unmessagable = bool(user.get("is_unmessageable"))
        except Exception:
            is_unmessagable = False

        return {
            "id": node_id,
            "num": num,
            "last_seen": last_seen_str,
            "last_seen_ts": last_seen_ts,
            "lat": lat,
            "lon": lon,
            "location_source": pos.get("locationSource") or pos.get("location_source") or "Unknown",
            "altitude": pos.get("altitude"),
            "short_name": user.get("shortName") or user.get("short_name") or "???",
            "long_name": user.get("longName") or user.get("long_name") or "Unknown",
            "hw_model": user.get("hwModel") or user.get("hw_model") or "Unknown",
            "role": user.get("role") or node_entry.get("role") or "Unknown",
            "public_key": user.get("publicKey") or user.get("public_key"),
            "macaddr": user.get("macaddr"),
            "is_unmessagable": is_unmessagable,
            "battery": metrics.get("batteryLevel") if isinstance(metrics.get("batteryLevel"), (int, float)) else None,
            "voltage": metrics.get("voltage") if isinstance(metrics.get("voltage"), (int, float)) else None,
            "snr": node_entry.get("snr") if isinstance(node_entry.get("snr"), (int, float)) else None,
            "rssi": node_entry.get("rssi") if isinstance(node_entry.get("rssi"), (int, float)) else None,
            "snr_indirect": None,
            "rssi_indirect": None,
            "hops": hops,
            "hop_label": hop_label,
            "temperature": None,
            "relative_humidity": None,
            "barometric_pressure": None,
            "channel_utilization": metrics.get("channelUtilization") if isinstance(metrics.get("channelUtilization"), (int, float)) else None,
            "air_util_tx": metrics.get("airUtilTx") if isinstance(metrics.get("airUtilTx"), (int, float)) else None,
            "uptime_seconds": metrics.get("uptimeSeconds") if isinstance(metrics.get("uptimeSeconds"), (int, float)) else None,
        }

    def _import_meshtastic_info_text(content: str):
        nodes_cli = _extract_meshtastic_nodes_from_info_text(content)
        nodes = {}
        for nid, entry in nodes_cli.items():
            if not isinstance(nid, str):
                nid = str(nid)
            if not nid.startswith("!"):
                try:
                    nid = f"!{int(nid):x}"
                except Exception:
                    pass
            nodes[nid] = _node_from_meshtastic_cli(nid, entry)
        return _import_data_from_dict({"nodes": nodes})

    # Import Dialog
    with ui.dialog() as import_dialog, ui.card().classes('w-96'):
        ui.label(translate("popup.importdata.title", "Import Data")).classes('text-lg font-bold mb-2')
        ui.label(translate("popup.importdata.help", "Select a JSON file to import nodes and messages.")).classes('text-sm text-gray-600 mb-4')
        
        async def handle_upload(e):
            try:
                content = None
                if hasattr(e, 'content'):
                    read_result = e.content.read()
                    if asyncio.iscoroutine(read_result):
                        read_result = await read_result
                    content = read_result.decode('utf-8')
                elif hasattr(e, 'files') and e.files:
                    read_result = e.files[0].content.read()
                    if asyncio.iscoroutine(read_result):
                        read_result = await read_result
                    content = read_result.decode('utf-8')
                elif hasattr(e, 'file') and hasattr(e.file, 'read'):
                    read_result = e.file.read()
                    if asyncio.iscoroutine(read_result):
                         read_result = await read_result
                    content = read_result.decode('utf-8')
                elif hasattr(e, 'file') and hasattr(e.file, 'file') and hasattr(e.file.file, 'read'):
                     read_result = e.file.file.read()
                     if asyncio.iscoroutine(read_result):
                         read_result = await read_result
                     content = read_result.decode('utf-8')
                
                if content is None:
                    # Fallback for debugging if we can't find it
                    raise ValueError(f"Could not extract content from upload event. Attributes: {dir(e)}")

                imported_nodes_count = 0
                total_nodes_in_file = 0
                try:
                    data = json.loads(content)
                    imported_nodes_count, total_nodes_in_file = _import_data_from_dict(data)
                except Exception:
                    imported_nodes_count, total_nodes_in_file = _import_meshtastic_info_text(content)
                    
                import_dialog.close()
                
                # Show Summary Dialog
                with ui.dialog() as summary_dialog, ui.card().classes('w-96'):
                    ui.label(translate("popup.importdata.success.title", "Import Summary")).classes('text-xl font-bold text-green-600 mb-4')
                    
                    with ui.column().classes('w-full gap-2'):
                         ui.label(translate("popup.importdata.success.nodesinfile", "Nodes in File: {nodes_count}").format(nodes_count=total_nodes_in_file)).classes('text-lg')
                         ui.label(translate("popup.importdata.success.nodesimported", "Nodes Imported: {nodes_imported_count}").format(nodes_imported_count=imported_nodes_count)).classes('text-lg font-bold')
                         ui.separator()
                         ui.label(translate("popup.importdata.success.totalnodesinapp", "Total Nodes in App: {total_nodes_in_app}").format(total_nodes_in_app=len(state.nodes))).classes('text-md text-gray-600')
                    
                    ui.button('OK', on_click=summary_dialog.close).classes('w-full mt-4 bg-green-600')
                summary_dialog.open()
                
            except Exception as ex:
                print(f"Import Error: {ex}")
                ui.notify(translate("popup.importdata.failed.importfailed", "Import Failed: {error}").format(error=ex), type='negative')

        # Custom Dropzone Area Container
        with ui.element('div').classes('w-full h-32 relative border-2 border-dashed border-blue-300 rounded-lg hover:bg-blue-50 transition-colors group flex items-center justify-center'):
             # Visuals (Centered)
             with ui.column().classes('items-center gap-0 pointer-events-none'):
                 ui.icon('upload_file', size='3em').classes('text-blue-400 group-hover:scale-110 transition-transform')
                 ui.label(translate("popup.importdata.body1", "Drop JSON File Here")).classes('text-blue-600 font-bold text-lg')
                 ui.label(translate("popup.importdata.body2", "or click to select")).classes('text-blue-400 text-sm')

             # Invisible Uploader Overlay
             # We position it absolutely to cover the parent, make it invisible (opacity-0)
             # This captures both clicks and drops.
             uploader = ui.upload(on_upload=handle_upload, auto_upload=True) \
                .props('accept=.json,.txt flat unbordered hide-upload-btn max-files=1') \
                .classes('absolute inset-0 w-full h-full opacity-0 z-10 cursor-pointer')
             
             # Manually trigger picker on click because q-uploader background isn't clickable by default
             uploader.on('click', lambda: uploader.run_method('pickFiles'))
        
        # Fallback for systems where overlay events fail (e.g. Linux GTK Webview)
        # A clear, standard button that sits outside the overlay logic
        with ui.expansion(translate("popup.importdata.fallback", "Trouble uploading? Click here for standard button"), icon='help_outline').classes('w-full text-sm text-gray-500'):
             ui.upload(on_upload=handle_upload, auto_upload=True, label=translate("popup.importdata.fallback.label", "Standard Upload")).props('accept=.json,.txt color=blue').classes('w-full')
        
        ui.button(translate("button.cancel", "Cancel"), on_click=import_dialog.close).classes('w-full mt-2')


    with ui.dialog() as autosave_dialog, ui.card().classes('w-96'):
        ui.label(translate("popup.autosave.title", "Autosave Settings")).classes('text-lg font-bold mb-2')
        ui.label(translate("popup.autosave.help", "Configure automatic export interval in seconds (0 disables).")).classes('text-sm text-gray-600 mb-4')
        ui.number(
            translate("popup.autosave.label.label", "Interval (seconds, 0 = off)"),
            value=state.autosave_interval_sec,
            on_change=on_autosave_interval_change
        ).props('dense').classes('w-full mb-4')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('OK', on_click=autosave_dialog.close).classes('bg-slate-200 text-slate-900')

    with ui.dialog() as about_dialog, ui.card().classes('w-96'):
        ui.label(translate("about.title", "About")).classes('text-lg font-bold mb-2')
        ui.label(f'{PROGRAM_NAME} - {PROGRAM_SHORT_DESC}').classes('text-md font-semibold')
        with ui.row().classes('w-full items-center gap-2'):
            ui.label(translate("about.version", "Version: {version}").format(version=VERSION)).classes('text-sm text-gray-600')
            about_update_status = ui.element('div').classes('text-sm')
        ui.label(translate("about.author", "Author: {author}").format(author=AUTHOR)).classes('text-sm text-gray-600 mb-1')
        current_year = datetime.now().year
        copyright_year = 2026
        copyright_year_label = f"{copyright_year}" if current_year <= copyright_year else f"{copyright_year}-{current_year}"
        ui.label(f'Copyright © {copyright_year_label} {AUTHOR}').classes('text-xs text-gray-600')
        ui.label(translate("about.license", "License: {license}").format(license=LICENSE)).classes('text-xs text-gray-600')
        ui.link(translate("about.view_license", "View License"), f'{GITHUB_URL}/blob/main/LICENSE', new_tab=True).classes('text-xs text-blue-500 mb-2')
        ui.separator().classes('my-2')
        ui.label(
            translate(
                "about.description",
                f"{PROGRAM_NAME} is a graphical tool to decode and analyze/debug/store Meshtastic packets.",
            ).format(program=PROGRAM_NAME)
        ).classes('text-sm text-gray-600 mb-2')
        ui.label(translate("about.no_warranty", "This program comes with ABSOLUTELY NO WARRANTY.")).classes('text-xs text-red-500 mb-1')
        ui.label(
            translate(
                "about.not_affiliated",
                "This software is not affiliated with Meshtastic; it is developed by an independent enthusiast.",
            )
        ).classes('text-xs text-gray-600 mb-2')
        ui.label(
            translate(
                "about.repo_help",
                "For bug reports, feature requests and help, please visit the official GitHub repository:",
            )
        ).classes('text-sm text-gray-600 mb-2')
        ui.link(GITHUB_URL, GITHUB_URL, new_tab=True).classes('text-blue-500 mb-2')
        with ui.row().classes('w-full justify-end mt-2'):
            ui.button(translate("button.close", "Close"), on_click=about_dialog.close).classes('bg-slate-200 text-slate-900')

    with ui.dialog().props('persistent') as update_dialog, ui.card().classes('w-[560px]'):
        update_popup_title = ui.label(translate("update.popup.title", "Update available")).classes('text-lg font-bold mb-2')
        update_popup_body = ui.label("").classes('text-sm text-gray-700 whitespace-pre-line mb-3')
        update_popup_link = ui.element('div').classes('mb-3')
        with ui.row().classes('w-full justify-end'):
            update_popup_ok = ui.button('OK').classes('bg-slate-200 text-slate-900')

    _update_popup_state = {"tag": None}

    def _ack_update_popup(_e=None):
        tag = _update_popup_state.get("tag")
        if tag:
            state.update_popup_ack_version = tag
        update_dialog.close()

    update_popup_ok.on_click(_ack_update_popup)

    def _set_about_update_status(is_update_available: bool, release_url: str | None):
        if not about_update_status:
            return
        if is_update_available:
            label = html.escape(translate("update.status.update", "Update"))
            url = html.escape(release_url or GITHUB_RELEASES_URL, quote=True)
            about_update_status._props['innerHTML'] = f'<a class="text-blue-600 underline font-semibold" href="{url}" target="_blank">{label}</a>'
            about_update_status.update()
        else:
            label = html.escape(translate("update.status.updated", "Updated"))
            about_update_status._props['innerHTML'] = f'<span class="text-green-600 font-semibold">{label}</span>'
            about_update_status.update()

    async def _check_for_updates(show_popup: bool):
        if getattr(state, "update_check_running", False):
            return
        if getattr(state, "update_check_done", False) and not show_popup:
            return
        state.update_check_running = True
        try:
            info = await asyncio.to_thread(_fetch_latest_github_release, 10.0)
            if not info:
                return
            latest_tag = info.get("tag").replace("v", "").replace("V", "")
            release_url = info.get("url") or GITHUB_RELEASES_URL
            is_update = _is_newer_version(VERSION, latest_tag)
            state.latest_version = latest_tag
            state.latest_release_url = release_url
            state.update_available = bool(is_update)
            state.update_check_done = True
            _set_about_update_status(is_update, release_url)
            if is_update and show_popup and getattr(state, "update_popup_ack_version", None) != latest_tag:
                _update_popup_state["tag"] = latest_tag
                update_popup_title.text = translate("update.popup.title", "Update available")
                update_popup_body.text = translate(
                    "update.popup.body",
                    "A new version is available.\nCurrent: {current}\nLatest: {latest}",
                ).format(current=VERSION, latest=latest_tag)
                link_label = html.escape(translate("update.popup.open_release", "Open release page"))
                url = html.escape(release_url, quote=True)
                update_popup_link._props['innerHTML'] = f'<a class="text-blue-600 underline" href="{url}" target="_blank">{link_label}</a>'
                update_popup_link.update()
                update_dialog.open()
        finally:
            state.update_check_running = False

    ui.timer(2.0, lambda: asyncio.create_task(_check_for_updates(show_popup=True)), once=True)

    # Main Layout
    with ui.row().classes('w-full h-[calc(100vh-80px)] no-wrap'):
        
        # Left Column: Navigation/Controls (Small)
        with ui.column().classes('w-16 bg-gray-100 p-2 items-center h-full'):
            
            with ui.button(icon='menu').props('flat round'):
                with ui.menu():
                    def toggle_connection():
                        if state.connected:
                            stop_connection()
                            ui.notify(translate("status.disconnected", "Disconnected"))
                        else:
                            state.connection_dialog_shown = True
                            connection_dialog.open()
                    
                    conn_menu_item = ui.menu_item(on_click=toggle_connection)
                    with conn_menu_item:
                        ui.icon('power_settings_new').classes('mr-2 mt-auto mb-auto')
                        conn_label = ui.label(translate("menu.connect", "Connect")).classes('mt-auto mb-auto')
                    
                    def update_menu_text():
                         new_text = translate("menu.disconnect", "Disconnect") if state.connected else translate("menu.connect", "Connect")
                         if conn_label.text != new_text:
                             conn_label.text = new_text

                    ui.timer(0.5, update_menu_text)

                    def open_tx_goal_dialog():
                        with ui.dialog() as dlg, ui.card().classes('w-110'):
                            ui.label(translate("tx_goal.title", "TX Mode (Goal)")).classes('text-lg font-bold mb-2')
                            ui.label(translate("tx_goal.milestone", "🚀 Next Milestone: Secure TX Implementation")).classes('text-sm mb-2')
                            ui.label(translate("tx_goal.why_title", "Why a goal for TX?")).classes('text-sm font-semibold mb-1')
                            ui.label(
                                translate(
                                    "tx_goal.why_body",
                                    'MeshStation is an open project developed in my spare time. Moving from an '
                                    '"Analyzer" to a "Transceiver" is a major leap that requires dedicated time, '
                                    'deep protocol study, and specific hardware for stress-testing.',
                                )
                            ).classes('text-sm mb-2')
                            ui.label(translate("tx_goal.doing_title", "Doing it the right way:")).classes('text-sm font-semibold mb-1')
                            ui.label(
                                translate(
                                    "tx_goal.doing_body",
                                    'We are not just "enabling" a button. To protect the mesh ecosystem and ensure '
                                    'reliability, the TX implementation will focus on:',
                                )
                            ).classes('text-sm mb-1')
                            ui.label(
                                translate(
                                    "tx_goal.bullet.collision",
                                    '- Collision Avoidance: Professional management of the Duty Cycle to respect '
                                    'regulatory limits and network airtime.',
                                )
                            ).classes('text-sm mb-1')
                            ui.label(
                                translate(
                                    "tx_goal.bullet.identity",
                                    '- Unique Identity: Automatic ID assignment based on MAC Address to prevent '
                                    'clones and network conflicts.',
                                )
                            ).classes('text-sm mb-1')
                            ui.label(
                                translate(
                                    "tx_goal.bullet.integrity",
                                    '- Network Integrity: Rigorous testing to ensure MeshStation remains a '
                                    '"good citizen" on the RF spectrum.',
                                )
                            ).classes('text-sm mb-2')
                            ui.label(translate("tx_goal.support_title", "Your support makes this possible.")).classes('text-sm font-semibold mb-1')
                            ui.label(
                                translate(
                                    "tx_goal.support_body",
                                    'Development funds will go directly towards dedicated testing hardware and will '
                                    'allow me to allocate more time away from my daily job to speed up the release.',
                                )
                            ).classes('text-sm mb-2')
                            ui.link(
                                translate("tx_goal.donation_link", "Link for donations"),
                                DONATION_URL,
                                new_tab=True,
                            ).classes('text-sm text-blue-500 mb-2')
                            ui.button(translate("button.close", "Close"), on_click=dlg.close).classes('w-full mt-2 bg-slate-200 text-slate-900')
                        dlg.open()

                    with ui.menu_item(on_click=open_tx_goal_dialog):
                        ui.icon('radio_button_checked').classes('mr-2 mt-auto mb-auto')
                        ui.label(translate("menu.tx_goal", "TX Mode (Goal)")).classes('mt-auto mb-auto')

                    def toggle_verbose():
                        state.verbose_logging = not state.verbose_logging
                        state_text = "ON" if state.verbose_logging else "OFF"
                        verbose_label.text = translate("menu.verbose", "Verbose Log: {state}").format(state=state_text)
                        ui.notify(verbose_label.text)
                        save_user_config()
                        
                    verbose_item = ui.menu_item(on_click=toggle_verbose)
                    with verbose_item:
                        ui.icon('bug_report').classes('mr-2 mt-auto mb-auto')
                        initial_state = "ON" if state.verbose_logging else "OFF"
                        verbose_label = ui.label(
                            translate("menu.verbose", "Verbose Log: {state}").format(state=initial_state)
                        ).classes('mt-auto mb-auto')

                    def _collect_export_data():
                        return {
                            "nodes": state.nodes,
                            "messages": list(state.messages),
                            "logs": list(state.logs),
                            "mesh_stats": mesh_stats.to_dict(),
                            "channel_messages": {k: list(v) for k, v in state.channel_messages.items()},
                        }

                    def open_support_dialog():
                        with ui.dialog() as dlg, ui.card().classes('w-110'):
                            ui.label(translate("support_dialog.title", "Support the Project")).classes('text-lg font-bold mb-2')
                            ui.label(
                                translate(
                                    "support_dialog.body",
                                    'If you enjoy MeshStation and want to support its development, you can help '
                                    'by contributing a donation using the official donation link.',
                                )
                            ).classes('text-sm mb-2')
                            ui.label(
                                translate(
                                    "support_dialog.tiers",
                                    'Donors in specific tiers will be listed in the Top Contributors section of the project.',
                                )
                            ).classes('text-sm mb-2')
                            ui.link(
                                translate("support_dialog.top_contributors", "View Top Contributors"),
                                SUPPORTERS_URL,
                                new_tab=True,
                            ).classes('text-sm text-blue-500 mb-2')
                            ui.link(
                                translate("support_dialog.donation_page", "Donation page"),
                                DONATION_URL,
                                new_tab=True,
                            ).classes('text-sm text-blue-500 mb-2')
                            ui.button(translate("button.close", "Close"), on_click=dlg.close).classes('w-full mt-2 bg-slate-200 text-slate-900')
                        dlg.open()

                    def export_data():
                        try:
                            data = _collect_export_data()
                            
                            # Set locale to system default to get local date format
                            try:
                                locale.setlocale(locale.LC_TIME, '')
                            except:
                                pass

                            # Get formatted date string based on system locale
                            # %c is "Locale's appropriate date and time representation"
                            timestamp = datetime.now().strftime("%c")
                            
                            # Sanitize for filename (replace invalid chars like : / \ with - or _)
                            # Remove spaces completely to avoid problems with file name
                            safe_timestamp = timestamp.replace(":", "-").replace("/", "-").replace("\\", "-").replace(" ", "_")
                            
                            filename = f"{PROGRAM_NAME}_{safe_timestamp}.json".replace(" ", "")
                            
                            # Save in the application directory
                            export_path = os.path.join(get_app_path(), filename)
                            
                            with open(export_path, 'w') as f:
                                json.dump(data, f, indent=4)
                            
                            # Popup confirmation
                            with ui.dialog() as saved_dialog, ui.card():
                                ui.label(translate("popup.exportdata.success.title", "Export Successful")).classes('text-lg font-bold text-green-500')
                                ui.label(translate("popup.exportdata.success.filename", "File saved: {filename}").format(filename=filename))
                                ui.label(translate("popup.exportdata.success.location", "Location: {location}").format(location=get_app_path()))
                                ui.button(translate("button.close", "Close"), on_click=saved_dialog.close).classes('w-full')
                            
                            saved_dialog.open()
                            
                        except Exception as e:
                            ui.notify(translate("popup.exportdata.success.exportfailed", "Export Failed: {error}").format(error=e), type='negative')
                    
                    def _autosave_tick():
                        if state.autosave_interval_sec is None or state.autosave_interval_sec <= 0:
                            return
                        now = time.time()
                        if state.autosave_last_ts and (now - state.autosave_last_ts) < state.autosave_interval_sec:
                            return
                        try:
                            data = _collect_export_data()
                            if not data.get("nodes") and not data.get("messages"):
                                return
                            autosave_path = get_autosave_path()
                            with open(autosave_path, 'w') as f:
                                json.dump(data, f, indent=4)
                            state.autosave_last_ts = now
                        except Exception as e:
                            log_to_console(f"Autosave error: {e}")
                    
                    ui.separator()

                    with ui.menu_item(on_click=export_data):
                        ui.icon('file_download').classes('mr-2 mt-auto mb-auto')
                        ui.label(translate("menu.export", "Export Data")).classes('mt-auto mb-auto')

                    with ui.menu_item(on_click=lambda: import_dialog.open()):
                        ui.icon('file_upload').classes('mr-2 mt-auto mb-auto')
                        ui.label(translate("menu.import", "Import Data")).classes('mt-auto mb-auto')

                    with ui.menu_item(on_click=lambda: autosave_dialog.open()):
                        ui.icon('schedule').classes('mr-2 mt-auto mb-auto')
                        ui.label(translate("menu.autosave", "Autosave/Autoexport")).classes('mt-auto mb-auto')

                    def open_about():
                        asyncio.create_task(_check_for_updates(show_popup=False))
                        about_dialog.open()

                    ui.separator()

                    with ui.menu_item(on_click=open_support_dialog):
                        ui.icon('volunteer_activism').classes('mr-2 mt-auto mb-auto')
                        ui.label(translate("menu.support", "Support the Project")).classes('mt-auto mb-auto')

                    with ui.menu_item(on_click=open_about):
                        ui.icon('info').classes('mr-2 mt-auto mb-auto')
                        ui.label(translate("menu.about", "About")).classes('mt-auto mb-auto')

            ui.tooltip('Menu')
            ui.separator()
            
    # Center: Dashboard (Map & Chat)
        with ui.splitter(value=60).classes('w-full h-[99%]') as splitter:
            
            with splitter.before:
                with ui.tabs().classes('w-full') as tabs:
                    map_tab = ui.tab(translate("ui.nodesmap", "Nodes Map"))
                    nodes_tab = ui.tab(translate("ui.nodeslist", "Nodes List"))
                    overview_tab = ui.tab(translate("ui.meshoverview", "Mesh Overview"))
                
                # Hidden Bridge for Map Interaction
                target_node_input = ui.input().classes('hidden node-target-input')
                
                def process_node_filter(e):
                    val = e.value
                    if val:
                        tabs.set_value(nodes_tab)
                        ui.run_javascript(f'''
                            if (window.mesh_grid_api) {{
                                if (typeof window.mesh_grid_api.setGridOption === 'function') {{
                                    window.mesh_grid_api.setGridOption('quickFilterText', "{val}");
                                }} else if (typeof window.mesh_grid_api.setQuickFilter === 'function') {{
                                    window.mesh_grid_api.setQuickFilter("{val}");
                                }}
                            }}
                        ''')
                        ui.notify(translate("notification.positive.filteringnode", "Filtering node: {val}").format(val=val))
                        target_node_input.value = None # Reset for next click
                        
                target_node_input.on_value_change(process_node_filter)

                with ui.tab_panels(tabs, value=map_tab).props('keep-alive').classes('w-full h-full'):
                    
                    # MAP PANEL
                    with ui.tab_panel(map_tab).classes('p-0 h-full'):
                        m = ui.leaflet(center=(41.9, 12.5), zoom=6).classes('w-full h-full')

                        tile_internet = has_tile_internet()
                        ui.run_javascript(
                            f"window.mesh_main_map_id = {json.dumps(m.id)}; window.mesh_tile_internet = {json.dumps(tile_internet)}; window.mesh_network_mode = {json.dumps(state.network_mode)};"
                        )
                        ui.run_javascript("""
                        (function() {
                            var el = getElement(window.mesh_main_map_id);
                            var map = el && el.map;
                            if (!map) return;
                            var savedLat = %s;
                            var savedLng = %s;
                            var savedZoom = %s;
                            if (savedLat !== null && savedLng !== null) {
                                map.setView([savedLat, savedLng], savedZoom !== null ? savedZoom : map.getZoom());
                                return;
                            }
                            try {
                                var lang = (navigator.language || navigator.userLanguage || 'en').toLowerCase();
                                var centers = {
                                    'it': [41.9, 12.5], 'de': [51.1, 10.4], 'fr': [46.2, 2.2],
                                    'es': [40.4, -3.7], 'pt': [39.5, -8.0], 'pl': [52.1, 19.4],
                                    'nl': [52.3, 5.3], 'sv': [62.0, 15.0], 'no': [60.5, 8.5],
                                    'da': [56.3, 9.5], 'fi': [61.9, 25.7], 'cs': [49.8, 15.5],
                                    'ro': [45.9, 24.9], 'hu': [47.2, 19.5], 'el': [39.1, 22.4],
                                    'uk': [49.0, 31.0], 'ru': [61.5, 90.0], 'zh': [35.9, 104.2],
                                    'ja': [36.2, 138.3], 'ko': [36.5, 127.9], 'ar': [26.8, 30.8],
                                    'en-us': [39.5, -98.4], 'en-gb': [54.4, -2.1], 'en-au': [-25.3, 133.8],
                                };
                                var key = lang.substring(0, 5);
                                var center = centers[key] || centers[lang.substring(0, 2)] || [20.0, 0.0];
                                map.setView(center, map.getZoom());
                            } catch(e) {}
                        })();
                        """ % (
                            json.dumps(state.map_center_lat),
                            json.dumps(state.map_center_lng),
                            json.dumps(getattr(state, 'map_zoom', None)),
                        ))
                        ui.run_javascript("try { if (window.meshApplyThemeToMapWhenReady) { window.meshApplyThemeToMapWhenReady(); } } catch (e) {}")

                        if not tile_internet:
                            offline_loading_dialog = ui.dialog().props('persistent').classes('mesh-offline-loading')
                            with offline_loading_dialog:
                                with ui.card().classes('w-96'):
                                    ui.icon('wifi_off').classes('text-4xl text-gray-500')
                                    ui.label(translate("popup.alert.offlinemaps.title", "Offline Maps Are Loading")).classes('text-lg font-bold text-red-500')
                                    ui.label(translate("popup.alert.offlinemaps.body", "No internet connection.\nLoading offline maps, please wait...")).classes('text-base font-semibold whitespace-pre-line')
                                    ui.label(translate("popup.alert.offlinemaps.help", "Offline maps are less detailed, while still retaining the most important details\nin a small file size for a worldwide map.")).classes('text-xs text-gray-500 whitespace-pre-line')

                            _offline_refresh = {'fn': None}
                            _last_view_payload = {'bounds': None, 'zoom': None}
                            _view_task = {'handle': None}
                            from nicegui import background_tasks

                            async def _debounced_refresh():
                                await asyncio.sleep(0.02)
                                b = _last_view_payload.get('bounds')
                                z = _last_view_payload.get('zoom')
                                fn = _offline_refresh.get('fn')
                                if fn and b and z is not None:
                                    fn(b, z)

                            _poll_view_running = {'value': False}

                            async def _poll_view():
                                if _poll_view_running['value']:
                                    return
                                _poll_view_running['value'] = True
                                try:
                                    if not _offline_refresh.get('fn'):
                                        return
                                    try:
                                        with m:
                                            payload = await ui.run_javascript("""
                                                (() => {
                                                    try {
                                                        const el = getElement(%s);
                                                        const map = el && el.map;
                                                        if (!map) return null;
                                                        if (map._animatingZoom || map._zooming) {
                                                            return {animating: true};
                                                        }
                                                        const b = map.getBounds();
                                                        return {
                                                            animating: false,
                                                            zoom: map.getZoom(),
                                                            bounds: {south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast()},
                                                        };
                                                    } catch (e) {
                                                        return null;
                                                    }
                                                })()
                                            """ % json.dumps(m.id), timeout=1.0)
                                    except Exception:
                                        return
                                    if not isinstance(payload, dict):
                                        return
                                    if payload.get('animating'):
                                        return
                                    b = payload.get('bounds') or {}
                                    z = payload.get('zoom')
                                    if not b or z is None:
                                        return

                                    if _last_view_payload.get('bounds') == b and _last_view_payload.get('zoom') == z:
                                        return

                                    _last_view_payload['bounds'] = b
                                    _last_view_payload['zoom'] = z

                                    try:
                                        h = _view_task.get('handle')
                                        if h and not h.done():
                                            h.cancel()
                                    except Exception:
                                        pass
                                    _view_task['handle'] = asyncio.create_task(_debounced_refresh())
                                finally:
                                    _poll_view_running['value'] = False

                            ui.timer(0.25, lambda: background_tasks.create(_poll_view()))

                            async def _load_offline_map():
                                try:
                                    with m:
                                        offline_loading_dialog.open()
                                    await m.client.connected()
                                    while not m.is_initialized:
                                        await asyncio.sleep(0.05)

                                    topo = await asyncio.to_thread(get_offline_topology)
                                    if not topo:
                                        return

                                    def _build_offline_layers(topo_obj: dict):
                                        detected_names = _detect_topo_object_names(topo_obj)
                                        countries_name = _pick_topo_object_name(topo_obj, [
                                            'ne_10m_admin_0_countries',
                                            'Admin-0 countries',
                                            'Admin-0 Countries',
                                            'admin_0_countries',
                                            'countries',
                                        ]) or detected_names.get('countries')

                                        admin1_name = _pick_topo_object_name(topo_obj, [
                                            'ne_10m_admin_1_states_provinces',
                                            'Admin-1 states provinces',
                                            'Admin-1 States Provinces',
                                            'admin_1_states_provinces',
                                            'admin1',
                                            'states_provinces',
                                        ]) or detected_names.get('admin1')

                                        regions_name = _pick_topo_object_name(topo_obj, [
                                            'ne_10m_admin_1_regions',
                                            'admin_1_regions',
                                            'regions',
                                        ]) or detected_names.get('regions')

                                        places_name = _pick_topo_object_name(topo_obj, [
                                            'ne_10m_populated_places',
                                            'Populated places',
                                            'Populated Places',
                                            'populated_places',
                                            'places',
                                        ]) or detected_names.get('places')

                                        countries_geo = _topology_object_to_feature_collection(topo_obj, countries_name) if countries_name else None
                                        admin1_geo = _topology_object_to_feature_collection(topo_obj, admin1_name) if admin1_name else None
                                        regions_geo = _topology_object_to_feature_collection(topo_obj, regions_name) if regions_name else None
                                        cities_geo = _topology_object_to_feature_collection(topo_obj, places_name) if places_name else None

                                        if countries_geo:
                                            _ensure_feature_indexes(countries_geo)
                                        if admin1_geo:
                                            _ensure_feature_indexes(admin1_geo)
                                        if regions_geo:
                                            _ensure_feature_indexes(regions_geo)
                                        if cities_geo:
                                            _ensure_feature_indexes(cities_geo)

                                        admin1_has_province = False
                                        if admin1_geo:
                                            for f in (admin1_geo.get('features') or []):
                                                p = f.get('properties') or {}
                                                tv = (p.get('type') or p.get('TYPE') or '')
                                                if isinstance(tv, str) and tv.strip().lower() == 'province':
                                                    admin1_has_province = True
                                                    break

                                        return countries_geo, admin1_geo, regions_geo, cities_geo, admin1_has_province

                                    countries_geo, admin1_geo, regions_geo, cities_geo, admin1_has_province = await asyncio.to_thread(_build_offline_layers, topo)

                                    with m:
                                        ui.run_javascript("""
                                            try {
                                                const dark = document.documentElement.classList.contains('mesh-dark');
                                                const col = dark ? '#0f1a2f' : '#aad3df';
                                                document.querySelectorAll('.leaflet-container').forEach(c => { c.style.backgroundColor = col; });
                                            } catch (e) {}
                                        """)
                                        if countries_geo:
                                            m.generic_layer(
                                                name='geoJSON',
                                                args=[countries_geo, {
                                                    'style': {
                                                        'color': '#000000',
                                                        'weight': 1.5,
                                                        'fillColor': '#EFF2DE',
                                                        'fillOpacity': 1.0,
                                                    },
                                                }],
                                            )

                                        if regions_geo:
                                            m.generic_layer(
                                                name='geoJSON',
                                                args=[regions_geo, {
                                                    'style': {
                                                        'color': '#000000',
                                                        'weight': 1.4,
                                                        'fillOpacity': 0.0,
                                                    },
                                                }],
                                            )

                                        admin1_layer = m.generic_layer(
                                            name='geoJSON',
                                            args=[{'type': 'FeatureCollection', 'features': []}, {
                                                'style': {
                                                    'color': '#bdbdbd',
                                                    'weight': 0.6,
                                                    'fillOpacity': 0.0,
                                                },
                                            }],
                                        )
                                        admin1_layer.run_method('setStyle', {'opacity': 0.0, 'fillOpacity': 0.0})
                                        ui.run_javascript("try { if (window.meshGetTheme && window.meshSetTheme) { window.meshSetTheme(window.meshGetTheme()); } } catch (e) {}")

                                    def _in_bounds(lat: float, lon: float, b: dict) -> bool:
                                        return (
                                            lat is not None and lon is not None and
                                            b['south'] <= lat <= b['north'] and
                                            b['west'] <= lon <= b['east']
                                        )

                                    def _feature_centroid_latlon(f: dict):
                                        c = (f or {}).get('_mesh_centroid')
                                        if not c:
                                            geom = (f or {}).get('geometry') or {}
                                            c = _feature_polygon_centroid(geom)
                                        if not c:
                                            return None
                                        lon, lat = c
                                        return (lat, lon)

                                    def _name_for_feature(f: dict) -> str | None:
                                        return _extract_feature_name_en((f or {}).get('properties') or {}) or _extract_feature_name((f or {}).get('properties') or {})

                                    def _city_importance(feat: dict) -> tuple:
                                        p = feat.get('properties') or {}
                                        pop = (
                                            p.get('POP_MAX') or
                                            p.get('POP2020') or
                                            p.get('POP2015') or
                                            p.get('POP2000') or
                                            0
                                        )
                                        sr = p.get('SCALERANK') if p.get('SCALERANK') is not None else 99
                                        lr = p.get('LABELRANK') if p.get('LABELRANK') is not None else 99
                                        featurecla = (p.get('FEATURECLA') or '')
                                        if not isinstance(featurecla, str):
                                            featurecla = ''
                                        is_capital = (
                                            p.get('ADM0CAP') == 1 or
                                            p.get('ADM1CAP') == 1 or
                                            ('capital' in featurecla.lower())
                                        )
                                        is_mega = (p.get('MEGACITY') == 1)
                                        return (0 if is_mega else 1, 0 if is_capital else 1, -int(pop or 0), sr, lr)

                                    def refresh_labels_and_admin1(bounds: dict, zoom_value: float):
                                        try:
                                            z = float(zoom_value)
                                        except:
                                            return

                                        labels = []
                                        city_name_set = set()

                                        if countries_geo:
                                            if z <= 4:
                                                for f in (countries_geo.get('features') or []):
                                                    name = _name_for_feature(f)
                                                    c = _feature_centroid_latlon(f)
                                                    if not name or not c:
                                                        continue
                                                    lat, lon = c
                                                    if _in_bounds(lat, lon, bounds):
                                                        labels.append({'lat': lat, 'lon': lon, 'text': name, 'kind': 'country'})

                                        if regions_geo:
                                            if 5 <= z <= 7:
                                                candidates = []
                                                for f in (regions_geo.get('features') or []):
                                                    name = _name_for_feature(f)
                                                    c = _feature_centroid_latlon(f)
                                                    if not name or not c:
                                                        continue
                                                    lat, lon = c
                                                    if _in_bounds(lat, lon, bounds):
                                                        fb = f.get('_mesh_bbox') or {}
                                                        try:
                                                            area = abs(float(fb.get('east', 0.0)) - float(fb.get('west', 0.0))) * abs(float(fb.get('north', 0.0)) - float(fb.get('south', 0.0)))
                                                        except Exception:
                                                            area = 0.0
                                                        candidates.append((area, {'lat': lat, 'lon': lon, 'text': name, 'kind': 'region'}))

                                                candidates.sort(key=lambda t: t[0], reverse=True)
                                                for _, lab in candidates[:80]:
                                                    labels.append(lab)

                                        if cities_geo:
                                            if z >= 8:
                                                if z < 9:
                                                    city_cap = 80
                                                    min_pop = 150000
                                                elif z < 10:
                                                    city_cap = 140
                                                    min_pop = 50000
                                                else:
                                                    city_cap = 250
                                                    min_pop = 0

                                                visible = []
                                                for f in (cities_geo.get('features') or []):
                                                    geom = f.get('geometry') or {}
                                                    if geom.get('type') != 'Point':
                                                        continue
                                                    lon, lat = geom.get('coordinates') or [None, None]
                                                    if lat is None or lon is None:
                                                        continue
                                                    if _in_bounds(lat, lon, bounds):
                                                        p = f.get('properties') or {}
                                                        featurecla = (p.get('FEATURECLA') or '')
                                                        if not isinstance(featurecla, str):
                                                            featurecla = ''
                                                        is_capital = (
                                                            p.get('ADM0CAP') == 1 or
                                                            p.get('ADM1CAP') == 1 or
                                                            ('capital' in featurecla.lower())
                                                        )
                                                        pop = (
                                                            p.get('POP_MAX') or
                                                            p.get('POP2020') or
                                                            p.get('POP2015') or
                                                            p.get('POP2000') or
                                                            0
                                                        )
                                                        if not is_capital and int(pop or 0) < min_pop:
                                                            continue
                                                        visible.append(f)

                                                visible.sort(key=_city_importance)
                                                visible = visible[:city_cap]

                                                for f in visible:
                                                    p = f.get('properties') or {}
                                                    name = p.get('name_en') or p.get('NAME_EN') or p.get('NAME') or p.get('name')
                                                    geom = f.get('geometry') or {}
                                                    lon, lat = geom.get('coordinates') or [None, None]
                                                    if not name or lat is None or lon is None:
                                                        continue
                                                    try:
                                                        city_name_set.add(str(name).strip().lower())
                                                    except Exception:
                                                        pass
                                                    labels.append({'lat': float(lat), 'lon': float(lon), 'text': str(name), 'kind': 'city'})

                                        if admin1_geo:
                                            if 6 <= z <= 10:
                                                candidates = []
                                                for f in (admin1_geo.get('features') or []):
                                                    p = f.get('properties') or {}
                                                    tv = (p.get('type') or p.get('TYPE') or '')
                                                    if isinstance(tv, str):
                                                        tv = tv.strip().lower()
                                                    else:
                                                        tv = ''
                                                    if tv != 'province':
                                                        continue
                                                    name = _extract_feature_name_en(p) or p.get('name_en') or p.get('NAME_EN')
                                                    if not name:
                                                        continue
                                                    try:
                                                        if str(name).strip().lower() in city_name_set:
                                                            continue
                                                    except Exception:
                                                        pass
                                                    c = _feature_centroid_latlon(f)
                                                    if not c:
                                                        continue
                                                    lat, lon = c
                                                    if _in_bounds(lat, lon, bounds):
                                                        fb = f.get('_mesh_bbox') or {}
                                                        try:
                                                            area = abs(float(fb.get('east', 0.0)) - float(fb.get('west', 0.0))) * abs(float(fb.get('north', 0.0)) - float(fb.get('south', 0.0)))
                                                        except Exception:
                                                            area = 0.0
                                                        candidates.append((area, {'lat': lat, 'lon': lon, 'text': str(name), 'kind': 'province'}))

                                                candidates.sort(key=lambda t: t[0], reverse=True)
                                                for _, lab in candidates[:120]:
                                                    labels.append(lab)

                                        try:
                                            with m:
                                                ui.run_javascript("""
                                                    try {
                                                        const el = getElement(%s);
                                                        const map = el && el.map;
                                                        if (window.meshOfflineLabels) {
                                                            window.meshOfflineLabels.set(map, %s, %s);
                                                        }
                                                    } catch (e) {}
                                                """ % (json.dumps(m.id), json.dumps(labels, ensure_ascii=False), json.dumps(z)))
                                        except Exception:
                                            pass

                                        if admin1_geo:
                                            if z >= 6:
                                                bounded_features = []
                                                for f in (admin1_geo.get('features') or []):
                                                    if admin1_has_province:
                                                        p = f.get('properties') or {}
                                                        tv = (p.get('type') or p.get('TYPE') or '')
                                                        if not (isinstance(tv, str) and tv.strip().lower() == 'province'):
                                                            continue
                                                    fb = f.get('_mesh_bbox')
                                                    if fb and _bbox_intersects(bounds, fb):
                                                        bounded_features.append(f)
                                                fc = {'type': 'FeatureCollection', 'features': bounded_features[:500]}
                                                try:
                                                    with m:
                                                        admin1_layer.run_method('clearLayers')
                                                        admin1_layer.run_method('addData', fc)
                                                        w = 0.5
                                                        if z >= 7:
                                                            w = 0.65
                                                        if z >= 9:
                                                            w = 0.85
                                                        admin1_layer.run_method('setStyle', {'color': '#bdbdbd', 'opacity': 1.0, 'weight': w, 'fillOpacity': 0.0})
                                                except Exception:
                                                    with m:
                                                        admin1_layer.run_method('setStyle', {'opacity': 0.0, 'fillOpacity': 0.0})
                                            else:
                                                try:
                                                    with m:
                                                        admin1_layer.run_method('clearLayers')
                                                except Exception:
                                                    pass
                                                with m:
                                                    admin1_layer.run_method('setStyle', {'opacity': 0.0, 'fillOpacity': 0.0})

                                    _offline_refresh['fn'] = refresh_labels_and_admin1
                                    b = _last_view_payload.get('bounds') or {'south': -90, 'west': -180, 'north': 90, 'east': 180}
                                    z = _last_view_payload.get('zoom')
                                    if z is None:
                                        z = m.zoom
                                    refresh_labels_and_admin1(b, z)
                                    await asyncio.sleep(0.05)
                                except Exception as e:
                                    try:
                                        log_to_console(f"Offline map error: {e}")
                                    except Exception:
                                        pass
                                finally:
                                    try:
                                        with m:
                                            offline_loading_dialog.close()
                                    except Exception:
                                        pass

                            background_tasks.create(_load_offline_map())
                        
                        map_markers_ready = {'value': False}
                        
                        def format_uptime(seconds):
                            try:
                                s_val = int(seconds)
                            except:
                                return "0s"
                                
                            d = s_val // 86400
                            h = (s_val % 86400) // 3600
                            m = (s_val % 3600) // 60
                            s = s_val % 60
                            
                            parts = []
                            if d > 0: parts.append(f"{d}d")
                            if h > 0: parts.append(f"{h}h")
                            if m > 0: parts.append(f"{m}m")
                            if s > 0 or not parts: parts.append(f"{s}s")
                            
                            return ", ".join(parts)

                        _update_map_running = {'value': False}
                        # Popup cache: avoid recalculation if node has not changed
                        _node_popup_cache = {}  # nid -> (last_seen_ts, popup_html)

                        async def update_map():
                            # Guard: Avoid overlaps if js takes > 1s
                            if _update_map_running['value']:
                                return
                            _update_map_running['value'] = True
                            try:
                                if (not state.nodes_updated) and map_markers_ready.get('value'):
                                    return

                                nodes_payload = []
                                for nid, n in list(state.nodes.items()):
                                    if n['lat'] and n['lon']:
                                        try:
                                            lat = float(n['lat'])
                                            lon = float(n['lon'])
                                        except Exception:
                                            continue

                                        label_raw = n.get('short_name')
                                        if not isinstance(label_raw, str):
                                            label_raw = ""
                                        label_raw = label_raw.strip()
                                        if not label_raw or label_raw == "???":
                                            label = str(nid)[-4:]
                                        else:
                                            label = label_raw[:4]

                                        # Popup cache: recalculate only if last_seen_ts has changed
                                        last_seen_ts = n.get("last_seen_ts")
                                        cached = _node_popup_cache.get(nid)
                                        if cached and cached[0] == last_seen_ts:
                                            popup_content = cached[1]
                                        else:
                                            # Popup construction
                                            name_display = n['long_name']
                                            short_display = n['short_name'] if n['short_name'] != "???" else ""

                                            popup_content = f"<div style='cursor:pointer' onclick='window.goToNode(\"{nid}\")'>"
                                            popup_content += f"<b style='font-size:16px; margin-bottom: 8px; display: block;'>{name_display}</b>"

                                            if state.network_mode == "meshcore":
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>fingerprint</i> {translate('ui.public_key', 'Public key')}: <span style='font-family:monospace; font-size:10px; word-break:break-all;'>{nid}</span><br>"
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>label</i> {translate('ui.short_id', 'Short ID')}: {n['short_name']}<br>"
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>category</i> {translate('ui.type', 'Type')}: {n['role']}<br>"
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>history</i> {translate('ui.freshness', 'Freshness')}: {n['last_seen']}<br>"

                                                # Radio Params for MeshCore
                                                popup_content += "<div style='margin-top:4px; padding-top:4px; border-top:1px solid #ddd;'>"
                                                popup_content += f"<b>{translate('ui.radio_parameters', 'Radio parameters')}:</b><br>"
                                                popup_content += f"{translate('ui.frequency', 'Frequency')}: {state.meshcore_custom_freq}MHz<br>"
                                                popup_content += f"{translate('ui.bandwidth', 'Bandwidth')}: {state.meshcore_custom_bw}kHz<br>"
                                                popup_content += f"{translate('ui.coding_rate', 'Coding rate')}: {state.meshcore_custom_cr}<br>"
                                                popup_content += f"{translate('ui.spreading_factor', 'Spreading factor')}: {state.meshcore_custom_sf}</div>"
                                            else:
                                                if short_display:
                                                    popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>label</i> Short Name: {short_display}<br>"

                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>fingerprint</i> ID: {nid}</div>"
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>memory</i> {translate('ui.model', 'Model')}: {n['hw_model']}<br>"
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>admin_panel_settings</i> {translate('ui.role', 'Role')}: {n['role']}<br>"

                                            if n.get('temperature') is not None:
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>thermostat</i> {n['temperature']:.1f}°C<br>"
                                            if n.get('barometric_pressure') is not None:
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>speed</i> {n['barometric_pressure']:.1f} hPa<br>"
                                            if n.get('relative_humidity') is not None:
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>water_drop</i> {n['relative_humidity']:.1f}%<br>"
                                            if n.get('battery') is not None:
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>battery_std</i> {n['battery']}%<br>"
                                            if n.get('channel_utilization') is not None:
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>bar_chart</i> Util: {n['channel_utilization']:.1f}%<br>"
                                            if n.get('altitude') is not None:
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>terrain</i> Alt: {n['altitude']} m<br>"
                                            if n.get('uptime_seconds') is not None:
                                                up_str = format_uptime(n['uptime_seconds'])
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>schedule</i> Up: {up_str}<br>"

                                            hop_label = n.get('hop_label')
                                            if hop_label is not None:
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>alt_route</i> Hops: {hop_label}<br>"

                                            snr_val = n.get('snr')
                                            rssi_val = n.get('rssi')
                                            snr_indirect = n.get('snr_indirect')
                                            hops_val = n.get('hops')

                                            if hops_val == 0:
                                                if snr_val is not None:
                                                    try:
                                                        snr_float = float(snr_val)
                                                        min_snr, max_snr = -20.0, 10.0
                                                        snr_norm = max(0.0, min(1.0, (snr_float - min_snr) / (max_snr - min_snr)))
                                                        pos_percent = int(snr_norm * 100)
                                                        popup_content += (
                                                            "<div style='margin-top:4px;'>"
                                                            "<div style='display:flex;align-items:center;gap:4px;'>"
                                                            "<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>signal_cellular_alt</i>"
                                                            "<div style='position:relative;width:120px;height:10px;"
                                                            "background:linear-gradient(to right, #ef4444, #facc15, #22c55e);"
                                                            "border-radius:999px;'>"
                                                            f"<div style='position:absolute;left:{pos_percent}%;top:50%;"
                                                            "transform:translate(-50%, -50%);width:12px;height:12px;"
                                                            "border-radius:999px;background:#111827;border:2px solid white;'></div>"
                                                            "</div></div>"
                                                            f"<div class='mesh-muted' style='font-size:11px;margin-top:2px;'>└ SNR: {snr_float:.1f} dB</div>"
                                                            "</div>"
                                                        )
                                                    except Exception:
                                                        pass
                                                if rssi_val is not None:
                                                    try:
                                                        rssi_float = float(rssi_val)
                                                        popup_content += (
                                                            f"<div class='mesh-muted' style='font-size:11px;margin-top:2px;'>└ RSSI: {rssi_float:.1f} dB</div>"
                                                        )
                                                    except Exception:
                                                        pass
                                            else:
                                                if snr_indirect is not None:
                                                    try:
                                                        snr_indirect_float = float(snr_indirect)
                                                        popup_content += (
                                                            "<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>waves</i> "
                                                            f"RX SNR (indirect): {snr_indirect_float:.1f} dB<br>"
                                                        )
                                                    except Exception:
                                                        pass

                                            preset_name = n.get('preset')
                                            if preset_name:
                                                short = MESHTASTIC_MODEM_PRESETS.get(preset_name, {}).get('channel_name', preset_name)
                                                col = PRESET_COLORS.get(preset_name, '#64748b')
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>settings_input_antenna</i> Preset: <span style='background:{col};color:white;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:700;'>{short}</span><br>"

                                            popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>access_time</i> Last Seen: {n['last_seen']}<br>"

                                            if n.get('location_source'):
                                                popup_content += f"<i class='material-icons' style='font-size:16px; vertical-align:text-bottom;'>my_location</i> Loc Source: {n['location_source']}<br>"

                                            # Save to cache
                                            _node_popup_cache[nid] = (last_seen_ts, popup_content)

                                        nodes_payload.append({
                                            "id": nid,
                                            "lat": lat,
                                            "lon": lon,
                                            "last_seen_ts": last_seen_ts,
                                            "marker_label": label.upper(),
                                            "popup": popup_content,
                                            "hops": n.get("hops"),
                                            "role": n.get("role"),
                                            "role_id": n.get("role_id"),
                                            "relay_capable": n.get("relay_capable", False),
                                        })

                                if nodes_payload:
                                    with m:
                                        await ui.run_javascript(
                                            "try { window.meshUpsertNodesOnMap(%s, %s); } catch (e) {}"
                                            % (json.dumps(m.id), json.dumps(nodes_payload, ensure_ascii=False, default=str))
                                        )
                                    map_markers_ready['value'] = True

                                # MeshCore Path Drawing
                                if state.network_mode == "meshcore":
                                    paths_payload = []
                                    for nid, n in list(state.nodes.items()):
                                        last_path = n.get("last_path")
                                        if last_path and n.get("lat") and n.get("lon"):
                                            coords = [[float(n["lat"]), float(n["lon"])]]
                                            # Trace path through repeaters
                                            for hop_hash in last_path:
                                                # Try to find repeater by hash
                                                found_rep = False
                                                for rnid, rn in state.nodes.items():
                                                    if rnid.startswith(hop_hash) and rn.get("lat") and rn.get("lon"):
                                                        coords.append([float(rn["lat"]), float(rn["lon"])])
                                                        found_rep = True
                                                        break
                                                if not found_rep:
                                                    # If repeater location is unknown, we stop drawing this path
                                                    break
                                            if len(coords) > 1:
                                                paths_payload.append({"coords": coords})

                                    if paths_payload:
                                        with m:
                                            await ui.run_javascript(
                                                "try { window.meshDrawPaths(%s, %s); } catch (e) {}"
                                                % (json.dumps(m.id), json.dumps(paths_payload))
                                            )

                                state.nodes_updated = False

                            except Exception as e:
                                log_to_console(f"[MAP UPDATE ERROR] {e}")
                            finally:
                                _update_map_running['value'] = False

                    # NODES LIST PANEL
                    with ui.tab_panel(nodes_tab).classes('h-full p-0 flex flex-col'):
                        # Header with Count and Reset Filter Button
                        with ui.row().classes('w-full items-center justify-between p-2 bg-gray-50 border-b'):
                             node_count_label = ui.label('Total Nodes: 0').classes('font-bold text-gray-700')
                             
                             def reset_filters():
                                 ui.run_javascript(f'''
                                    if (window.mesh_grid_api) {{
                                        // Clear Quick Filter
                                        if (typeof window.mesh_grid_api.setGridOption === 'function') {{
                                            window.mesh_grid_api.setGridOption('quickFilterText', "");
                                        }} else if (typeof window.mesh_grid_api.setQuickFilter === 'function') {{
                                            window.mesh_grid_api.setQuickFilter("");
                                        }}
                                        
                                        // Clear Column Filters
                                        window.mesh_grid_api.setFilterModel(null);
                                        
                                        // Refresh cells to update row numbers
                                        window.mesh_grid_api.refreshCells({{columns: ['rowNum'], force: true}});
                                    }}
                                 ''')
                                 ui.notify(translate("notification.positive.filtersreset", "Filters Reset"))

                             ui.button(translate("ui.resetfilters", "Reset Filters"), on_click=reset_filters, icon='filter_alt_off').props('dense flat color=red')

                        # Safe initial data load
                        initial_rows = []
                        try:
                            initial_rows = [n.copy() for n in state.nodes.values()]
                        except:
                            pass

                        nodes_grid = ui.aggrid({
                            'suppressColumnVirtualisation': True,
                            'suppressRowVirtualisation': False,
                            'alwaysShowVerticalScroll': True,
                            'defaultColDef': {
                                'resizable': True,
                                'sortable': True,
                                'filter': True,
                                'minWidth': 100,
                            },
                            'columnDefs': [
                                {'headerName': '#', 'colId': 'rowNum', 'valueGetter': 'node.rowIndex + 1', 'width': 65, 'minWidth': 65, 'sortable': False, 'filter': False, 'pinned': 'left'},
                                {'headerName': 'Name', 'field': 'short_name', 'width': 100, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': 'Long Name', 'field': 'long_name', 'width': 180, 'minWidth': 180, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': 'ID', 'field': 'id', 'width': 150, 'minWidth': 150, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': 'MAC', 'field': 'macaddr', 'width': 160, 'minWidth': 160, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': translate('ui.public_key', 'Public Key'), 'field': 'public_key', 'width': 240, 'minWidth': 240, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': 'Unmessagable', 'field': 'is_unmessagable', 'width': 120, ':valueFormatter': '(p) => (p.value === true ? \"true\" : \"false\")'},
                                {'headerName': translate('ui.model', 'Model'), 'field': 'hw_model', 'width': 160, 'minWidth': 160, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': translate('ui.role', 'Role'), 'field': 'role', 'width': 140, 'minWidth': 140, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': translate('ui.relay_capable', 'Relay Capable'), 'field': 'relay_capable', 'width': 130, ':valueFormatter': '(p) => (p.value === true ? \"true\" : \"false\")'},
                                {
                                    'headerName': 'Hops',
                                    'field': 'hops',
                                    'width': 80,
                                    ':comparator': '(valueA, valueB, nodeA, nodeB, isInverted) => { const isNullA = valueA === null || valueA === undefined; const isNullB = valueB === null || valueB === undefined; if (isNullA && isNullB) return 0; if (isNullA && !isNullB) { return isInverted ? -1 : 1; } if (!isNullA && isNullB) { return isInverted ? 1 : -1; } const a = Number(valueA); const b = Number(valueB); if (Number.isNaN(a) && Number.isNaN(b)) return 0; if (Number.isNaN(a) && !Number.isNaN(b)) return isInverted ? -1 : 1; if (!Number.isNaN(a) && Number.isNaN(b)) return isInverted ? 1 : -1; if (a === b) return 0; return a < b ? -1 : 1; }'
                                },
                                {'headerName': 'Preset', 'field': 'preset', 'width': 120, 'minWidth': 100, ':cellRenderer': 'window.meshPresetCellRenderer'},
                                {'headerName': 'SNR (dB)', 'field': 'snr', 'width': 100},
                                {'headerName': 'RSSI (rel dB)', 'field': 'rssi', 'width': 110},
                                {'headerName': 'Last Seen', 'field': 'last_seen', 'width': 180, 'minWidth': 180, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': 'Lat', 'field': 'lat', 'width': 120, 'minWidth': 120, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': 'Lon', 'field': 'lon', 'width': 120, 'minWidth': 120, ':cellRenderer': 'window.meshCopyCellRenderer'},
                                {'headerName': 'Alt (m)', 'field': 'altitude', 'width': 65, 'minWidth': 65},
                                {'headerName': 'Loc Source', 'field': 'location_source', 'width': 110},
                                {'headerName': 'SNR Indirect (dB)', 'field': 'snr_indirect', 'width': 130},
                                {'headerName': 'RSSI Indirect (rel dB)', 'field': 'rssi_indirect', 'width': 150},
                                {'headerName': 'Batt', 'field': 'battery', 'width': 100},
                                {'headerName': 'Volt', 'field': 'voltage', 'width': 100},
                                {'headerName': 'Temp', 'field': 'temperature', 'width': 100},
                                {'headerName': 'Hum', 'field': 'relative_humidity', 'width': 100},
                                {'headerName': 'Press', 'field': 'barometric_pressure', 'width': 100},
                                {'headerName': 'Ch Util', 'field': 'channel_utilization', 'width': 100},
                                {'headerName': 'Air Util', 'field': 'air_util_tx', 'width': 100},
                                {'headerName': 'Uptime', 'field': 'uptime_seconds', 'width': 140, 'minWidth': 140, ':cellRenderer': 'window.meshCopyCellRenderer'},
                            ],
                            'rowData': initial_rows,
                            ':getRowId': '(params) => params.data.id',
                            ':rowClassRules': '{ "mesh-row-clickable": (p) => !!(p && p.data && p.data.lat && p.data.lon) }',
                            ':onGridReady': '(params) => { window.mesh_grid_api = params.api; }',
                        }).classes('flex-grow w-full')
                        
                        # Handle Grid Ready event to force sync when tab is opened/refreshed
                        def handle_grid_ready(e):
                            state.nodes_list_force_refresh = True
                            
                        nodes_grid.on('gridReady', handle_grid_ready)
                        
                        # We must force refresh the 'rowNum' column when sort/filter changes
                        nodes_grid.on('sortChanged', lambda: nodes_grid.run_grid_method('refreshCells', {'columns': ['rowNum'], 'force': True}))
                        nodes_grid.on('filterChanged', lambda: nodes_grid.run_grid_method('refreshCells', {'columns': ['rowNum'], 'force': True}))
                        
                        # Handle Row Click -> Pan to Map
                        def on_row_click(e):
                            row = e.args.get('data', {})
                            lat = row.get('lat')
                            lon = row.get('lon')
                            nid = row.get('id')
                            
                            if lat and lon:
                                tabs.set_value(map_tab) # Switch tab
                                m.set_center((lat, lon)) # Center map
                                try:
                                    with m:
                                        # Delay popup open to allow tab switch and map render to complete
                                        ui.run_javascript(
                                            "setTimeout(function(){ try { window.meshOpenNodePopup(%s, %s); } catch (e) {} }, 300);"
                                            % (json.dumps(m.id), json.dumps(nid))
                                        )
                                except Exception:
                                    pass
                                     
                        nodes_grid.on('rowClicked', on_row_click, args=['data'])
                        
                        # Initial sync flag for this session
                        first_sync = True

                        def update_grid():
                            nonlocal first_sync
                            
                            # Always update count label
                            node_count_label.text = f"{translate('ui.totalnodes', 'Total Nodes')}: {len(state.nodes)}"
                            
                            try:
                                to_process = []
                                force = state.nodes_list_force_refresh
                                
                                # If it's the first run for this client OR a force refresh is requested
                                if first_sync or force:
                                    try:
                                        to_process = [n.copy() for n in state.nodes.values()]
                                    except RuntimeError:
                                        return # Dict changed size, skip frame
                                        
                                    state.nodes_list_force_refresh = False # Reset global flag
                                    first_sync = False
                                    
                                    # Clear dirty nodes since we are syncing everything
                                    with state.lock:
                                        state.dirty_nodes.clear()
                                        
                                else:
                                    # Normal Delta Update
                                    with state.lock:
                                        if not state.dirty_nodes:
                                            return
                                        dirty_ids = state.dirty_nodes.copy()
                                        state.dirty_nodes.clear()
                                    
                                    for nid in dirty_ids:
                                        if nid in state.nodes:
                                            to_process.append(state.nodes[nid].copy())
                                
                                if to_process:
                                    # Send to client for robust Upsert
                                    # Use json.dumps to ensure proper serialization
                                    # We use run_javascript to execute the safe Upsert logic on the client
                                    ui.run_javascript(f'upsertNodeData({nodes_grid.id}, {json.dumps(to_process, default=str)})')
                                    
                            except Exception as e:
                                print(f"Grid Update Error: {e}")
                            
                    # Overview/stats Tab
                    with ui.tab_panel(overview_tab).classes('p-3 h-full overflow-auto'):
                        ui.label(translate("mesh_overview.title", "Mesh Overview")).classes('text-xl font-bold mb-2')

                        def _fmt_num(x, digits: int = 0):
                            if x is None:
                                return "-"
                            try:
                                if digits <= 0:
                                    return f"{int(round(float(x)))}"
                                return f"{float(x):.{digits}f}"
                            except Exception:
                                return str(x)

                        def _sparkline_svg(values: list[int], width: int = 860, height: int = 120, pad: int = 8) -> str:
                            if not values:
                                values = [0]
                            vals = [int(v) for v in values[-120:]]
                            vmin = min(vals)
                            vmax = max(vals)
                            if vmax <= vmin:
                                vmax = vmin + 1
                            w = max(100, int(width))
                            h = max(60, int(height))
                            n = len(vals)
                            if n < 2:
                                n = 2
                                vals = [vals[0], vals[0]]
                            x_step = (w - 2 * pad) / (n - 1)
                            pts = []
                            for i, v in enumerate(vals):
                                x = pad + i * x_step
                                y = pad + (h - 2 * pad) * (1.0 - ((v - vmin) / (vmax - vmin)))
                                pts.append(f"{x:.1f},{y:.1f}")
                            poly = " ".join(pts)
                            return f"""
                                <svg width="100%" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
                                  <rect x="0" y="0" width="{w}" height="{h}" rx="10" fill="rgba(148,163,184,0.10)"/>
                                  <polyline points="{poly}" fill="none" stroke="rgba(96,165,250,0.95)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
                                  <text x="{pad}" y="{h - pad}" font-size="12" fill="rgba(148,163,184,0.95)">{html.escape(translate("mesh_overview.traffic.graph_label", "Packets/min (rolling)"))}</text>
                                </svg>
                            """

                        with ui.row().classes('w-full gap-3 mb-3 flex-wrap'):
                            with ui.card().classes('p-3 w-full sm:w-[calc(50%-0.75rem)] lg:w-[calc(25%-0.75rem)]'):
                                ui.label(translate("mesh_overview.kpi.total_packets", "Total packets")).classes('text-sm text-gray-500')
                                kpi_total = ui.label("-").classes('text-2xl font-bold').props('id=mesh-kpi-total')
                            with ui.card().classes('p-3 w-full sm:w-[calc(50%-0.75rem)] lg:w-[calc(25%-0.75rem)]'):
                                ui.label(translate("mesh_overview.kpi.ppm", "Packets/min")).classes('text-sm text-gray-500')
                                kpi_ppm = ui.label("-").classes('text-2xl font-bold').props('id=mesh-kpi-ppm')
                            with ui.card().classes('p-3 w-full sm:w-[calc(50%-0.75rem)] lg:w-[calc(25%-0.75rem)]'):
                                ui.label(translate("mesh_overview.kpi.active_5m", "Active nodes (5m)")).classes('text-sm text-gray-500')
                                kpi_active_5m = ui.label("-").classes('text-2xl font-bold').props('id=mesh-kpi-active-5m')
                            with ui.card().classes('p-3 w-full sm:w-[calc(50%-0.75rem)] lg:w-[calc(25%-0.75rem)]'):
                                ui.label(translate("mesh_overview.kpi.error_rate", "Global error rate")).classes('text-sm text-gray-500')
                                kpi_err = ui.label("-").classes('text-2xl font-bold').props('id=mesh-kpi-err')
                        with ui.row().classes('w-full gap-3 mb-2 flex-nowrap'):
                            with ui.card().classes('p-2 sm:p-3 flex-1 min-w-0 overflow-hidden mesh-kpi-card'):
                                with ui.row().classes('w-full items-center gap-1 flex-nowrap'):
                                    ui.label(translate("mesh_overview.kpi.mesh_traffic", "Mesh Traffic")).classes('flex-1 basis-0 min-w-0 whitespace-nowrap overflow-hidden text-ellipsis mesh-kpi-label text-gray-500')
                                    ui.icon("help_outline").classes('text-sky-800 p-[2px] mesh-kpi-icon cursor-help select-none')
                                    ui.tooltip(translate("mesh_overview.kpi.mesh_traffic.tooltip", "0–100 score based on mesh traffic congestion.\nHigher score = less congestion.\nLabels: Excellent / Good / Fair / Poor.")).classes('whitespace-pre-line')
                                with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
                                    mesh_traffic_value = ui.label("-").classes('mesh-kpi-value font-bold whitespace-nowrap').props('id=mesh-traffic-value')
                                    mesh_traffic_badge = ui.badge("-").classes('text-white mesh-kpi-badge whitespace-nowrap').props('id=mesh-traffic-badge')

                            with ui.card().classes('p-2 sm:p-3 flex-1 min-w-0 overflow-hidden mesh-kpi-card'):
                                with ui.row().classes('w-full items-center gap-1 flex-nowrap'):
                                    ui.label(translate("mesh_overview.kpi.packet_integrity", "Packet Integrity")).classes('flex-1 basis-0 min-w-0 whitespace-nowrap overflow-hidden text-ellipsis mesh-kpi-label text-gray-500')
                                    ui.icon("help_outline").classes('text-sky-800 p-[2px] mesh-kpi-icon cursor-help select-none')
                                    ui.tooltip(translate("mesh_overview.kpi.packet_integrity.tooltip", "0–100 score based on packet validity.\nComputed from CRC OK / CRC Fail / Invalid Protobuf.\nLabels: Excellent / Good / Fair / Poor.")).classes('whitespace-pre-line')
                                with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
                                    packet_integrity_value = ui.label("-").classes('mesh-kpi-value font-bold whitespace-nowrap').props('id=mesh-integrity-value')
                                    packet_integrity_badge = ui.badge("-").classes('text-white mesh-kpi-badge whitespace-nowrap').props('id=mesh-integrity-badge')

                            with ui.card().classes('p-2 sm:p-3 flex-1 min-w-0 overflow-hidden mesh-kpi-card'):
                                with ui.row().classes('w-full items-center gap-1 flex-nowrap'):
                                    ui.label(translate("mesh_overview.kpi.mesh_signal", "Mesh Signal (RF)")).classes('flex-1 basis-0 min-w-0 whitespace-nowrap overflow-hidden text-ellipsis mesh-kpi-label text-gray-500')
                                    ui.icon("help_outline").classes('text-sky-800 p-[2px] mesh-kpi-icon cursor-help select-none')
                                    ui.tooltip(translate("mesh_overview.kpi.mesh_signal.tooltip", "0–100 score based on RF signal quality.\nComputed from average SNR and RSSI (direct + indirect packets).\nLabels: Excellent / Good / Fair / Poor.")).classes('whitespace-pre-line')
                                with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
                                    mesh_signal_value = ui.label("-").classes('mesh-kpi-value font-bold whitespace-nowrap').props('id=mesh-signal-value')
                                    mesh_signal_badge = ui.badge("-").classes('text-white mesh-kpi-badge whitespace-nowrap').props('id=mesh-signal-badge')

                            with ui.card().classes('p-2 sm:p-3 flex-1 min-w-0 overflow-hidden mesh-kpi-card'):
                                with ui.row().classes('w-full items-center gap-1 flex-nowrap'):
                                    ui.label(translate("mesh_overview.kpi.mesh_health", "Mesh Health (Global)")).classes('flex-1 basis-0 min-w-0 whitespace-nowrap overflow-hidden text-ellipsis mesh-kpi-label text-gray-500')
                                    ui.icon("help_outline").classes('text-sky-800 p-[2px] mesh-kpi-icon cursor-help select-none')
                                    ui.tooltip(translate("mesh_overview.kpi.mesh_health.tooltip", "0–100 score computed as the arithmetic mean of Mesh Traffic, Packet Integrity and Mesh Signal.\nLabels: Stable / Intermittent / Unstable / Critical.")).classes('whitespace-pre-line')
                                with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
                                    mesh_health_value = ui.label("-").classes('mesh-kpi-value font-bold whitespace-nowrap').props('id=mesh-health-value')
                                    mesh_health_badge = ui.badge("-").classes('text-white mesh-kpi-badge whitespace-nowrap').props('id=mesh-health-badge')

                        ui.label(translate("mesh_overview.quality.note", "Note: 1–3 hours of listening are recommended for a more stable overview.")).classes('text-xs text-gray-500 mb-3')

                        traffic_graph = ui.element('div').classes('w-full mb-3').props('id=mesh-traffic-graph')

                        with ui.row().classes('w-full gap-3 mb-3 flex-wrap'):
                            with ui.card().classes('p-3 w-full md:w-[calc(50%-0.75rem)]'):
                                ui.label(translate("mesh_overview.section.integrity", "Packet Integrity")).classes('text-lg font-semibold mb-2')
                                integrity_crc_ok = ui.label("-").classes('text-sm').props('id=mesh-integrity-crc-ok')
                                integrity_crc_fail = ui.label("-").classes('text-sm').props('id=mesh-integrity-crc-fail')
                                integrity_dec_ok = ui.label("-").classes('text-sm').props('id=mesh-integrity-dec-ok')
                                integrity_dec_fail = ui.label("-").classes('text-sm').props('id=mesh-integrity-dec-fail')
                                integrity_pb = ui.label("-").classes('text-sm').props('id=mesh-integrity-pb')
                                integrity_port = ui.label("-").classes('text-sm').props('id=mesh-integrity-port')

                            with ui.card().classes('p-3 w-full md:w-[calc(50%-0.75rem)]'):
                                ui.label(translate("mesh_overview.section.rf", "RF Quality (recent)")).classes('text-lg font-semibold mb-2')
                                rf_rssi = ui.label("-").classes('text-sm').props('id=mesh-rf-rssi')
                                rf_snr = ui.label("-").classes('text-sm').props('id=mesh-rf-snr')
                                rf_direct = ui.label("-").classes('text-sm').props('id=mesh-rf-direct')
                                rf_multihop = ui.label("-").classes('text-sm').props('id=mesh-rf-multihop')
                                rf_hopavg = ui.label("-").classes('text-sm pb-9').props('id=mesh-rf-hopavg')

                        top_node_state = {"id": None}

                        def _filter_top_node():
                            nid = top_node_state.get("id")
                            if nid:
                                target_node_input.value = str(nid)

                        with ui.row().classes('w-full gap-3 mb-3 flex-wrap'):
                            with ui.card().classes('p-3 w-full'):
                                ui.label(translate("mesh_overview.section.activity", "Mesh Activity")).classes('text-lg font-semibold mb-2')
                                act_active_10m = ui.label("-").classes('text-sm').props('id=mesh-act-active-10m')
                                act_new_hour = ui.label("-").classes('text-sm').props('id=mesh-act-new-hour')
                                act_cu = ui.label("-").classes('text-sm').props('id=mesh-act-cu')
                                act_private_msgs = ui.label("-").classes('text-sm').props('id=mesh-act-private')
                                ui.separator().classes('my-2')
                                ui.label(translate("mesh_overview.section.single_node_activity", "Single node activity")).classes('text-sm font-semibold text-gray-600')
                                with ui.row().classes('w-full items-center gap-1'):
                                    act_top_node_prefix = ui.label("-").classes('text-sm').props('id=mesh-act-top-prefix')
                                    act_top_node_id = ui.label("-").classes('text-sm').props('id=mesh-act-top-id')
                                    act_top_node_cnt = ui.label("").classes('text-sm').props('id=mesh-act-top-cnt')
                                act_top_node_id.on('click', lambda _e: _filter_top_node())
                                act_au = ui.label("-").classes('text-sm').props('id=mesh-act-au')

                        with ui.row().classes('w-full justify-end gap-2'):
                            with ui.dialog() as reset_stats_dialog:
                                with ui.card().classes('w-[520px]'):
                                    ui.label(
                                        translate("mesh_overview.reset_confirm.title", "Reset mesh statistics?")
                                    ).classes('text-lg font-bold text-red-600 mb-2')
                                    ui.label(
                                        translate("mesh_overview.reset_confirm.warning", "This will reset all Mesh Overview statistics.")
                                    ).classes('text-sm text-red-600 font-semibold whitespace-pre-line')
                                    ui.label(
                                        translate(
                                            "mesh_overview.reset_confirm.detail",
                                            "Nodes and node information will NOT be deleted.\nDo you want to proceed?",
                                        )
                                    ).classes('text-sm text-gray-700 whitespace-pre-line mb-3')

                                    with ui.row().classes('w-full justify-end gap-2'):
                                        ui.button(
                                            translate("button.cancel", "Cancel"),
                                            on_click=reset_stats_dialog.close,
                                        ).classes('bg-slate-200 text-slate-900')

                                        def _do_reset_stats():
                                            mesh_stats.reset()
                                            reset_stats_dialog.close()
                                            ui.notify(translate("mesh_overview.notification.reset", "Mesh stats reset"), type='positive')

                                        ui.button(
                                            translate("button.yes", "Yes"),
                                            on_click=_do_reset_stats,
                                        ).classes('bg-red-600 text-white')

                            def _ask_reset_stats():
                                reset_stats_dialog.open()

                            def _export_stats_json():
                                try:
                                    try:
                                        locale.setlocale(locale.LC_TIME, '')
                                    except Exception:
                                        pass
                                    timestamp = datetime.now().strftime("%c")
                                    safe_timestamp = timestamp.replace(":", "-").replace("/", "-").replace("\\", "-").replace(" ", "_")
                                    filename = f"{PROGRAM_NAME}_MeshOverview_{safe_timestamp}.json".replace(" ", "")
                                    export_path = os.path.join(get_app_path(), filename)
                                    with open(export_path, 'w') as f:
                                        json.dump(mesh_stats.to_dict(), f, indent=4)
                                    with ui.dialog() as saved_dialog, ui.card():
                                        ui.label(translate("mesh_overview.export.success.title", "Export Successful")).classes('text-lg font-bold text-green-500')
                                        ui.label(translate("mesh_overview.export.success.filename", "File saved: {filename}").format(filename=filename))
                                        ui.label(translate("mesh_overview.export.success.location", "Location: {location}").format(location=get_app_path()))
                                        ui.label(
                                            translate(
                                                "mesh_overview.export.note_autosave",
                                                "Note: Mesh Overview data is also automatically saved and included in the normal Export Data.",
                                            )
                                        ).classes('text-sm text-gray-600')
                                        ui.button(translate("button.close", "Close"), on_click=saved_dialog.close).classes('w-full')
                                    saved_dialog.open()
                                except Exception as e:
                                    ui.notify(translate("mesh_overview.export.failed", "Export Failed: {error}").format(error=e), type='negative')

                            ui.button(translate("mesh_overview.button.reset", "Reset Stats"), on_click=_ask_reset_stats).classes('bg-slate-200 text-slate-900')
                            ui.button(translate("mesh_overview.button.export", "Export JSON"), on_click=_export_stats_json).classes('bg-blue-600 text-white')

                        _overview_last_snap = {'hash': None}
                        def _update_mesh_overview():
                            snap = mesh_stats.snapshot()
                            series = mesh_stats.sample_packets_per_minute()

                            # Build a single payload and update everything client-side in one JS call
                            # to avoid dozens of separate WebSocket DOM patches per second, which
                            # trigger ResizeObserver loops in AG Grid / Quasar on Linux/pywebview.
                            def _level_color(col):
                                return {
                                    'green':  '!bg-green-600 !text-white',
                                    'yellow': '!bg-yellow-600 !text-white',
                                    'orange': '!bg-orange-600 !text-white',
                                    'red':    '!bg-red-600 !text-white',
                                }.get(str(col or ''), '')

                            _aes_b64 = (getattr(state, 'aes_key_b64', '') or '').strip()
                            _df = int(snap.get('decrypt_fail') or 0)
                            _do = int(snap.get('decrypt_ok') or 0)
                            _den = _do + _df
                            _pct = (float(_df) / float(_den) * 100.0) if _den > 0 else 0.0
                            _show_private = _aes_b64 == 'AQ=='

                            _nid = snap.get('most_active_node')

                            badge_classes = 'bg-primary bg-blue-600 bg-sky-600 bg-green-600 bg-yellow-600 bg-orange-600 bg-red-600 !bg-green-600 !bg-yellow-600 !bg-orange-600 !bg-red-600'

                            payload = {
                                'svg': _sparkline_svg(series),
                                'kpi_total': _fmt_num(snap.get('total_packets')),
                                'kpi_ppm': _fmt_num(snap.get('packets_per_minute')),
                                'kpi_active_5m': _fmt_num(snap.get('active_nodes_5m')),
                                'kpi_err': f"{_fmt_num(snap.get('global_error_rate_pct'), 1)}%",
                                'integrity_crc_ok':   translate('mesh_overview.integrity.crc_ok',   'CRC OK: {v}').format(v=_fmt_num(snap.get('crc_ok'))),
                                'integrity_crc_fail': translate('mesh_overview.integrity.crc_fail',  'CRC Fail: {v}').format(v=_fmt_num(snap.get('crc_fail'))),
                                'integrity_dec_ok':   translate('mesh_overview.integrity.decrypt_ok','Decrypt OK: {v}').format(v=_fmt_num(snap.get('decrypt_ok'))),
                                'integrity_dec_fail': translate('mesh_overview.integrity.decrypt_fail','Decrypt Fail: {v}').format(v=_fmt_num(snap.get('decrypt_fail'))),
                                'integrity_pb':       translate('mesh_overview.integrity.invalid_protobuf','Invalid protobuf: {v}').format(v=_fmt_num(snap.get('invalid_protobuf'))),
                                'integrity_port':     translate('mesh_overview.integrity.unknown_portnum','Unknown portnum: {v}').format(v=_fmt_num(snap.get('unknown_portnum'))),
                                'rf_rssi':    translate('mesh_overview.rf.rssi_avg',   'Avg RSSI: {v}').format(v=_fmt_num(snap.get('rssi_avg'), 1)),
                                'rf_snr':     translate('mesh_overview.rf.snr_avg',    'Avg SNR: {v}').format(v=_fmt_num(snap.get('snr_avg'), 1)),
                                'rf_direct':  translate('mesh_overview.rf.direct_pct', 'Direct packets: {v}%').format(v=_fmt_num(snap.get('direct_ratio_pct'), 1)),
                                'rf_multihop':translate('mesh_overview.rf.multihop_pct','Multi-hop packets: {v}%').format(v=_fmt_num(snap.get('multihop_ratio_pct'), 1)),
                                'rf_hopavg':  translate('mesh_overview.rf.hop_avg',    'Avg hops: {v}').format(v=_fmt_num(snap.get('hop_avg'), 2)),
                                'act_active_10m': translate('mesh_overview.activity.active_10m','Active nodes (10m): {v}').format(v=_fmt_num(snap.get('active_nodes_10m'))),
                                'act_new_hour':   translate('mesh_overview.activity.new_nodes_hour','New nodes/hour: {v}').format(v=_fmt_num(snap.get('new_nodes_last_hour'))),
                                'act_top_node_prefix': translate('mesh_overview.activity.top_node.prefix','Most active:'),
                                'act_top_node_id':  str(_nid or '-'),
                                'act_top_node_cnt': translate('mesh_overview.activity.top_node.count','({cnt})').format(cnt=_fmt_num(snap.get('most_active_node_packets'))) if _nid else '',
                                'act_top_node_clickable': bool(_nid),
                                'act_cu':  translate('mesh_overview.activity.channel_util_avg','Avg channel_utilization: {v}').format(v=_fmt_num(snap.get('channel_utilization_avg'), 1)),
                                'act_au':  translate('mesh_overview.activity.air_util_tx_max','Peak Node Transmission: {v}%').format(v=_fmt_num(snap.get('air_util_tx_max'), 1)),
                                'show_private': _show_private,
                                'act_private_msgs': translate('mesh_overview.activity.private_messages','Private messages/Channels (est.): {v} ({pct}%)').format(v=_fmt_num(_df), pct=_fmt_num(_pct, 1)) if _show_private else '',
                                'mesh_traffic_value': _fmt_num(snap.get('mesh_traffic_score')),
                                'mesh_traffic_badge': translate(f"mesh_overview.level.{snap.get('mesh_traffic_level') or ''}", (str(snap.get('mesh_traffic_level') or '') or '-').title()),
                                'mesh_traffic_color': _level_color(snap.get('mesh_traffic_color')),
                                'packet_integrity_value': _fmt_num(snap.get('packet_integrity_score')),
                                'packet_integrity_badge': translate(f"mesh_overview.level.{snap.get('packet_integrity_level') or ''}", (str(snap.get('packet_integrity_level') or '') or '-').title()),
                                'packet_integrity_color': _level_color(snap.get('packet_integrity_color')),
                                'mesh_signal_value': _fmt_num(snap.get('mesh_signal_score')),
                                'mesh_signal_badge': translate(f"mesh_overview.level.{snap.get('mesh_signal_level') or ''}", (str(snap.get('mesh_signal_level') or '') or '-').title()),
                                'mesh_signal_color': _level_color(snap.get('mesh_signal_color')),
                                'mesh_health_value': _fmt_num(snap.get('mesh_health_score')),
                                'mesh_health_badge': translate(f"mesh_overview.health.{snap.get('mesh_health_level') or ''}", (str(snap.get('mesh_health_level') or '') or '-').title()),
                                'mesh_health_color': _level_color(snap.get('mesh_health_color')),
                                'badge_remove_classes': badge_classes,
                                'top_node_id_ref': str(top_node_state.get('id') or ''),
                            }
                            top_node_state['id'] = _nid

                            ui.run_javascript(f'''
                            (function(d) {{
                                // SVG graph
                                var tg = document.getElementById('mesh-traffic-graph');
                                if (tg) tg.innerHTML = d.svg;

                                // KPI labels
                                var els = {{
                                    'mesh-kpi-total':       d.kpi_total,
                                    'mesh-kpi-ppm':         d.kpi_ppm,
                                    'mesh-kpi-active-5m':   d.kpi_active_5m,
                                    'mesh-kpi-err':         d.kpi_err,
                                    'mesh-integrity-crc-ok':   d.integrity_crc_ok,
                                    'mesh-integrity-crc-fail': d.integrity_crc_fail,
                                    'mesh-integrity-dec-ok':   d.integrity_dec_ok,
                                    'mesh-integrity-dec-fail': d.integrity_dec_fail,
                                    'mesh-integrity-pb':       d.integrity_pb,
                                    'mesh-integrity-port':     d.integrity_port,
                                    'mesh-rf-rssi':    d.rf_rssi,
                                    'mesh-rf-snr':     d.rf_snr,
                                    'mesh-rf-direct':  d.rf_direct,
                                    'mesh-rf-multihop':d.rf_multihop,
                                    'mesh-rf-hopavg':  d.rf_hopavg,
                                    'mesh-act-active-10m': d.act_active_10m,
                                    'mesh-act-new-hour':   d.act_new_hour,
                                    'mesh-act-top-prefix': d.act_top_node_prefix,
                                    'mesh-act-top-id':     d.act_top_node_id,
                                    'mesh-act-top-cnt':    d.act_top_node_cnt,
                                    'mesh-act-cu':         d.act_cu,
                                    'mesh-act-au':         d.act_au,
                                    'mesh-act-private':    d.act_private_msgs,
                                    'mesh-traffic-value':       d.mesh_traffic_value,
                                    'mesh-traffic-badge':       d.mesh_traffic_badge,
                                    'mesh-integrity-value':     d.packet_integrity_value,
                                    'mesh-integrity-badge':     d.packet_integrity_badge,
                                    'mesh-signal-value':        d.mesh_signal_value,
                                    'mesh-signal-badge':        d.mesh_signal_badge,
                                    'mesh-health-value':        d.mesh_health_value,
                                    'mesh-health-badge':        d.mesh_health_badge,
                                }};
                                for (var id in els) {{
                                    var el = document.getElementById(id);
                                    if (el && el.textContent !== els[id]) el.textContent = els[id];
                                }}

                                // Badge colors
                                var badgeRemove = d.badge_remove_classes.split(' ');
                                [
                                    ['mesh-traffic-badge',    d.mesh_traffic_color],
                                    ['mesh-integrity-badge',  d.packet_integrity_color],
                                    ['mesh-signal-badge',     d.mesh_signal_color],
                                    ['mesh-health-badge',     d.mesh_health_color],
                                ].forEach(function(pair) {{
                                    var el = document.getElementById(pair[0]);
                                    if (!el) return;
                                    el.classList.remove.apply(el.classList, badgeRemove);
                                    if (pair[1]) pair[1].split(' ').forEach(function(c) {{ if (c) el.classList.add(c); }});
                                }});

                                // Top node clickable state
                                var topEl = document.getElementById('mesh-act-top-id');
                                if (topEl) {{
                                    if (d.act_top_node_clickable) {{
                                        topEl.classList.add('text-blue-600', 'underline', 'cursor-pointer');
                                        topEl.classList.remove('text-gray-600', 'cursor-default');
                                    }} else {{
                                        topEl.classList.add('text-gray-600', 'cursor-default');
                                        topEl.classList.remove('text-blue-600', 'underline', 'cursor-pointer');
                                    }}
                                }}

                                // Private messages visibility
                                var pmEl = document.getElementById('mesh-act-private');
                                if (pmEl) {{
                                    pmEl.style.display = d.show_private ? '' : 'none';
                                }}
                            }})({json.dumps(payload)});
                            ''')

                        ui.timer(1.0, _update_mesh_overview)
        
            with splitter.after:
                with ui.column().classes('h-full w-full no-wrap'):                    
                    # Top: Chat
                    with ui.row().classes('w-full items-center justify-between p-2'):
                        ui.label(translate("ui.chatmessages", "Chat Messages")).classes('font-bold mt-1 mb-1')
                        chat_resume_btn = ui.button(translate("button.resumeautoscroll", "Resume Auto-Scroll"), icon='arrow_downward', on_click=lambda: enable_chat_scroll()).props('dense color=blue').classes('hidden')

                    # Channel tabs
                    with ui.element('div').classes('channel-tabs-wrapper').style('margin-bottom: -16px; z-index: 10;') as _tabs_wrapper:
                        _arrow_left  = ui.element('div').classes('channel-tabs-arrow left').style('font-size:18px;').on('click', lambda: ui.run_javascript("(function(){var r=document.querySelector('.channel-tabs-row');if(r)r.scrollLeft-=120;})()"))
                        with _arrow_left:
                            ui.label('‹')
                        channel_tabs_row = ui.row().classes('channel-tabs-row w-full items-center gap-0 border border-b flex-nowrap px-1 flex-shrink-0 rounded-t').style('position: relative; z-index: 10;')
                        _arrow_right = ui.element('div').classes('channel-tabs-arrow right').style('font-size:18px;').on('click', lambda: ui.run_javascript("(function(){var r=document.querySelector('.channel-tabs-row');if(r)r.scrollLeft+=120;})()"))
                        with _arrow_right:
                            ui.label('›')

                    ui.run_javascript('''
                    (function() {
                        function initTabsScroll() {
                            var row = document.querySelector('.channel-tabs-row');
                            var wrapper = document.querySelector('.channel-tabs-wrapper');
                            if (!row || !wrapper) { setTimeout(initTabsScroll, 300); return; }
                            var arrowL = wrapper.querySelector('.channel-tabs-arrow.left');
                            var arrowR = wrapper.querySelector('.channel-tabs-arrow.right');
                            function update() {
                                var canL = row.scrollLeft > 4;
                                var canR = row.scrollLeft + row.clientWidth < row.scrollWidth - 4;
                                arrowL.classList.toggle('visible', canL);
                                arrowR.classList.toggle('visible', canR);
                            }
                            row.addEventListener('scroll', update, {passive: true});
                            row.addEventListener('wheel', function(e) {
                                if (e.deltaY !== 0) { e.preventDefault(); row.scrollLeft += e.deltaY * 0.8; }
                            }, {passive: false});
                            new MutationObserver(function() { requestAnimationFrame(update); }).observe(row, {childList: true, subtree: true});
                            update();
                        }
                        initTabsScroll();
                    })();
                    ''')

                    channel_tab_refs = {}   # channel_id -> {'btn': ui.button, 'dot': ui.badge}
                    _ch_drag_state = {'dragging': None}
                    # Chat container
                    chat_scroll = ui.scroll_area().classes('w-full flex-grow p-2 bg-slate-50 border border-t-0 rounded-b rounded-t-none')
                    with chat_scroll:
                        chat_container = ui.column().classes('w-full')
                    
                    # Chat Scroll State
                    chat_scroll_state = {'auto': True, 'suppress_until': 0}

                    def handle_chat_scroll(e):
                        if time.time() < chat_scroll_state['suppress_until']: return
                        
                        # Pixel-based logic
                        if 'verticalPosition' in e.args and 'verticalSize' in e.args and 'verticalContainerSize' in e.args:
                            v_pos = e.args['verticalPosition']
                            v_size = e.args['verticalSize']
                            v_container = e.args['verticalContainerSize']
                            
                            dist_from_bottom = v_size - v_container - v_pos
                            
                            # If user scrolls up > 20px from bottom, disable auto-scroll
                            if dist_from_bottom > 20:
                                chat_scroll_state['auto'] = False
                                chat_resume_btn.classes(remove='hidden')
                            # If user scrolls back to very bottom, re-enable
                            elif dist_from_bottom < 5:
                                chat_scroll_state['auto'] = True
                                chat_resume_btn.classes(add='hidden')

                    chat_scroll.on('scroll', handle_chat_scroll, args=['verticalPosition', 'verticalSize', 'verticalContainerSize'])

                    def enable_chat_scroll():
                        chat_scroll_state['auto'] = True
                        chat_resume_btn.classes(add='hidden')
                        chat_scroll_state['suppress_until'] = time.time() + 0.5
                        chat_scroll.scroll_to(percent=1.0)

                    def get_sender_display_name(msg):
                        # Use cached ID if available to resolve latest name
                        s_id = msg.get('from_id')
                        if s_id and s_id in state.nodes:
                            n = state.nodes[s_id]
                            s_name = n.get('short_name', '???')
                            l_name = n.get('long_name', 'Unknown')
                            
                            has_short = s_name and s_name != "???"
                            has_long = l_name and l_name != "Unknown"
                            
                            if has_long and has_short:
                                return f"{l_name} ({s_name})"
                            elif has_short:
                                return s_name
                            elif has_long:
                                return l_name
                        
                        # Fallback to stored static name
                        return msg['from']

                    def chat_name_click(msg):
                        s_id = msg.get('from_id')
                        if not s_id and msg.get('from', '').startswith('!'):
                            s_id = msg.get('from')
                            
                        if s_id:
                            # Switch to Nodes Tab
                            tabs.set_value(nodes_tab)
                            
                            # Try multiple methods to set quick filter (supporting different AG Grid versions)
                            # v31+ uses setGridOption('quickFilterText', val)
                            # Older uses setQuickFilter(val)
                            # Even if we use specific version, is good keep it for backward/future compatibility or different versions
                            ui.run_javascript(f'''
                                if (window.mesh_grid_api) {{
                                    if (typeof window.mesh_grid_api.setGridOption === 'function') {{
                                        window.mesh_grid_api.setGridOption('quickFilterText', "{s_id}");
                                    }} else if (typeof window.mesh_grid_api.setQuickFilter === 'function') {{
                                        window.mesh_grid_api.setQuickFilter("{s_id}");
                                    }} else {{
                                        console.warn("No quick filter method found on api");
                                    }}
                                }}
                            ''')
                            ui.notify(translate("notification.positive.filterednodesby", "Filtered nodes by: {s_id}").format(s_id=s_id))
                    # Extra channels functions
                    def _get_all_channel_ids():
                        ids = ['default'] + [ch['id'] for ch in state.extra_channels 
                                            if ch['id'] in [x for x in state.channels_order] or True]
                        # order ids respecting channels_order
                        ordered = ['default']
                        for cid in state.channels_order:
                            if any(ch['id'] == cid for ch in state.extra_channels):
                                ordered.append(cid)
                        # add any remaining channels not in order
                        for ch in state.extra_channels:
                            if ch['id'] not in ordered:
                                ordered.append(ch['id'])
                        return ordered

                    def _get_channel_label(ch_id):
                        if ch_id == 'default':
                            # use default channel name if set
                            name = getattr(state, 'direct_channel_name', '') or getattr(state, 'external_channel_name', '')
                            return name if name else 'Default'
                        for ch in state.extra_channels:
                            if ch['id'] == ch_id:
                                return ch.get('label') or ch.get('name', ch_id)
                        return ch_id

                    def rebuild_channel_tabs():
                        channel_tabs_row.clear()
                        channel_tab_refs.clear()
                        with channel_tabs_row:
                            for ch_id in _get_all_channel_ids():
                                _render_channel_tab(ch_id)
                            # Add "+ Add Channel" button
                            ui.button('+ ' + translate('channel.add.title', 'Add Channel'), on_click=open_add_channel_dialog).props('flat dense').classes('ml-1 text-blue-500 text-xs whitespace-nowrap')

                    def _render_channel_tab(ch_id):
                        label = _get_channel_label(ch_id)
                        is_active = (state.active_channel_id == ch_id)
                        
                        with ui.element('div').classes('relative flex items-center'):
                            # Unread dot
                            cnt = int(state.channel_unread_count.get(ch_id, 0) or 0)
                            badge_text = '99+' if cnt > 99 else (str(cnt) if cnt > 0 else '')
                            dot = ui.badge(badge_text).props('floating color=red top-1').classes(
                                '' if cnt > 0 else 'hidden'
                            )
                            
                            btn = ui.button(label).props('flat dense no-caps').classes(
                                'channel-tab-btn px-3 py-1 rounded-none border-b-2 ' +
                                ('border-blue-500 text-blue-600 font-bold' if is_active else 'border-transparent text-gray-600')
                            )
                            btn.on('click', lambda cid=ch_id: switch_channel(cid))
                            
                            # Right-click context menu (only for non-default channels)
                            if ch_id != 'default':
                                with btn:
                                    with ui.context_menu():
                                        ui.menu_item(
                                            translate('channel.ctx.edit', 'Edit'),
                                            on_click=lambda cid=ch_id: open_edit_channel_dialog(cid)
                                        )
                                        ui.menu_item(
                                            translate('channel.ctx.move_left', 'Move Left'),
                                            on_click=lambda cid=ch_id: move_channel(cid, -1)
                                        )
                                        ui.menu_item(
                                            translate('channel.ctx.move_right', 'Move Right'),
                                            on_click=lambda cid=ch_id: move_channel(cid, 1)
                                        )
                                        ui.separator()
                                        ui.menu_item(
                                            translate('channel.ctx.delete', 'Delete'),
                                            on_click=lambda cid=ch_id: confirm_delete_channel(cid)
                                        ).classes('text-red-500')
                            
                            channel_tab_refs[ch_id] = {'dot': dot, 'btn': btn}

                    def switch_channel(ch_id):
                        state.active_channel_id = ch_id
                        state.channel_unread[ch_id] = False
                        state.channel_unread_count[ch_id] = 0
                        state.chat_force_refresh = True
                        rebuild_channel_tabs()

                    def move_channel(ch_id, direction):
                        order = _get_all_channel_ids()
                        non_default = [x for x in order if x != 'default']
                        if ch_id not in non_default:
                            return
                        idx = non_default.index(ch_id)
                        new_idx = idx + direction
                        if 0 <= new_idx < len(non_default):
                            non_default[idx], non_default[new_idx] = non_default[new_idx], non_default[idx]
                        state.channels_order = non_default
                        save_user_config()
                        rebuild_channel_tabs()

                    def confirm_delete_channel(ch_id):
                        with ui.dialog() as dlg, ui.card().classes('w-96'):
                            ui.label(translate('channel.delete.title', 'Delete Channel')).classes('text-lg font-bold mb-2')
                            ui.label(translate('channel.delete.body', 'Are you sure you want to delete this channel?')).classes('text-sm mb-4')
                            with ui.row().classes('w-full justify-end gap-2'):
                                ui.button(translate('button.cancel', 'Cancel'), on_click=dlg.close).classes('bg-slate-200 text-slate-900')
                                def do_delete():
                                    state.extra_channels = [ch for ch in state.extra_channels if ch['id'] != ch_id]
                                    if ch_id in state.channels_order:
                                        state.channels_order.remove(ch_id)
                                    state.channel_messages.pop(ch_id, None)
                                    state.channel_unread.pop(ch_id, None)
                                    state.channel_unread_count.pop(ch_id, None)
                                    if state.active_channel_id == ch_id:
                                        state.active_channel_id = 'default'
                                        state.chat_force_refresh = True
                                    save_user_config()
                                    dlg.close()
                                    rebuild_channel_tabs()
                                ui.button(translate('button.yes', 'Yes'), on_click=do_delete).classes('bg-red-600 text-white')
                        dlg.open()
                    def open_add_channel_dialog(edit_id=None):
                        existing = None
                        if edit_id:
                            for ch in state.extra_channels:
                                if ch['id'] == edit_id:
                                    existing = ch
                                    break
                        
                        with ui.dialog() as dlg, ui.card().classes('w-96'):
                            title = translate('channel.edit.title', 'Edit Channel') if existing else translate('channel.add.title', 'Add Channel')
                            ui.label(title).classes('text-lg font-bold mb-2')
                            
                            name_input = ui.input(
                                translate('channel.add.name', 'Channel Name'),
                                value=existing.get('name', '') if existing else ''
                            ).classes('w-full mb-1')
                            ui.label(translate('channel.add.name.hint', 'Used to match incoming packets (djb2 hash of name)')).classes('text-xs text-gray-400 mb-2')
                            
                            label_input = ui.input(
                                translate('channel.add.label', 'Display Label (optional, defaults to name)'),
                                value=existing.get('label', '') if existing else ''
                            ).classes('w-full mb-1')
                            
                            key_input = ui.input(
                                translate('channel.add.key', 'AES Key (Base64)'),
                                value=existing.get('key_b64', 'AQ==') if existing else 'AQ=='
                            ).classes('w-full mb-1')
                            ui.label(translate('panel.connection.settings.internal.label.aes_key.hint',
                                "Key size is auto-detected. 'AQ==' = Meshtastic default.")).classes('text-xs text-gray-400 mb-2')
                            
                            def save_channel():
                                import secrets as _sec
                                name = name_input.value.strip()
                                if not name:
                                    ui.notify(translate('channel.add.error.noname', 'Channel name is required'), color='negative')
                                    return
                                lbl = label_input.value.strip() or name
                                key = key_input.value.strip() or 'AQ=='
                                
                                if existing:
                                    existing['name'] = name
                                    existing['label'] = lbl
                                    existing['key_b64'] = key
                                else:
                                    new_id = _sec.token_urlsafe(8)
                                    ch = {'id': new_id, 'name': name, 'label': lbl, 'key_b64': key}
                                    state.extra_channels.append(ch)
                                    if new_id not in state.channels_order:
                                        state.channels_order.append(new_id)
                                
                                save_user_config()
                                dlg.close()
                                rebuild_channel_tabs()
                            
                            with ui.row().classes('w-full justify-end gap-2'):
                                ui.button(translate('button.cancel', 'Cancel'), on_click=dlg.close).classes('bg-slate-200 text-slate-900')
                                ui.button(translate('button.save', 'Save'), on_click=save_channel).classes('bg-blue-600 text-white')
                        
                        dlg.open()

                    def open_edit_channel_dialog(ch_id):
                        open_add_channel_dialog(edit_id=ch_id)

                    # Virtual window state (for client)
                    _chat_window = {
                        'offset': 0,           # from which index of state.messages is the view
                        'dom_count': 0,        # how many elements are in the DOM now
                    }

                    def _render_single_message(msg):
                        """Render a single message — shared function to avoid duplication."""
                        sent = msg['is_me']
                        name = get_sender_display_name(msg)
                        time_str = msg.get('time', '')
                        date_str = msg.get('date', '')

                        meta = ""
                        if time_str and date_str:
                            meta = f"{time_str} | {date_str}"
                        elif time_str:
                            meta = time_str
                        elif date_str:
                            meta = date_str

                        text_escaped = html.escape(msg.get('text', ''))
                        body_html = text_escaped
                        preset_tag = ""
                        msg_preset = msg.get('preset')
                        if msg_preset:
                            preset_colors = {
                                'LONG_FAST':'#22c55e','MEDIUM_FAST':'#3b82f6','LONG_SLOW':'#a855f7',
                                'MEDIUM_SLOW':'#f59e0b','SHORT_FAST':'#ef4444','SHORT_SLOW':'#f97316',
                                'SHORT_TURBO':'#ec4899','LONG_TURBO':'#06b6d4','LONG_MODERATE':'#84cc16',
                                'VERY_LONG_SLOW':'#64748b'
                            }
                            short_names = {
                                'LONG_FAST':'LongFast','MEDIUM_FAST':'MedFast','LONG_SLOW':'LongSlow',
                                'MEDIUM_SLOW':'MedSlow','SHORT_FAST':'ShortFast','SHORT_SLOW':'ShortSlow',
                                'SHORT_TURBO':'ShrtTurbo','LONG_TURBO':'LngTurbo','LONG_MODERATE':'LngMod',
                                'VERY_LONG_SLOW':'VLongSlow'
                            }
                            col = preset_colors.get(msg_preset, '#64748b')
                            label = short_names.get(msg_preset, msg_preset)
                            preset_tag = f" <span style='background:{col};color:white;padding:1px 6px;border-radius:999px;font-size:10px;font-weight:700;'>{label}</span>"

                        if meta:
                            body_html = f"{body_html}<br><span class='mesh-chat-meta'>{meta}{preset_tag}</span>"
                        elif preset_tag:
                            body_html = f"{body_html}<br><span class='mesh-chat-meta'>{preset_tag}</span>"

                        is_dark = state.theme == 'dark'
                        bg_col   = ('blue-6'  if sent else 'blue-10') if is_dark else ('green-9' if sent else 'green-6')
                        text_col = 'white' if is_dark else 'gray'

                        cm = ui.chat_message(sent=sent, stamp='')
                        cm.props(f'bg-color={bg_col} text-color={text_col}')
                        with cm.add_slot('name'):
                            ui.label(name).classes('text-xs font-bold text-gray-600 cursor-pointer hover:text-blue-600 hover:underline').on('click', lambda m=msg: chat_name_click(m))
                        with cm:
                            el = ui.element('div')
                            el._props['innerHTML'] = body_html
                            el.update()

                    # "Load more" banner, created once, dynamically shown/hidden
                    with chat_container:
                        load_more_banner = ui.row().classes('w-full justify-center py-2 hidden')
                        with load_more_banner:
                            def _load_older_messages():
                                active_id = state.active_channel_id
                                if active_id == 'default':
                                    all_msgs = list(state.messages)
                                else:
                                    all_msgs = list(state.channel_messages.get(active_id, deque()))

                                current_offset = _chat_window['offset']
                                load_count = min(_CHAT_LOAD_STEP, current_offset)
                                if load_count <= 0:
                                    return

                                new_offset = current_offset - load_count
                                _chat_window['offset'] = new_offset
                                msgs_to_add = all_msgs[new_offset:new_offset + load_count]

                                for i, msg in enumerate(msgs_to_add):
                                    with chat_container:
                                        _render_single_message(msg)
                                    new_el = list(chat_container)[-1]
                                    new_el.move(chat_container, target_index=1 + i)

                                _chat_window['dom_count'] += load_count

                                if new_offset <= 0:
                                    load_more_banner.classes(add='hidden')

                            ui.button(
                                '⬆ ' + translate('chat.load_older', 'Load older messages'),
                                on_click=_load_older_messages
                            ).props('flat dense').classes('text-blue-500 text-xs')
                            ui.label(
                                translate('chat.stored_in_memory', '(all messages kept in memory)')
                            ).classes('text-xs text-gray-400 ml-2')

                    def _do_force_refresh(all_msgs):
                        """Clears the message DOM and reloads the latest _CHAT_DOM_WINDOW, preserving the banner."""
                        children = list(chat_container)
                        for child in children:
                            if child is not load_more_banner:
                                try:
                                    child.delete()
                                except Exception:
                                    pass
                        _chat_window['dom_count'] = 0

                        total = len(all_msgs)
                        start = max(0, total - _CHAT_DOM_WINDOW)
                        _chat_window['offset'] = start

                        with chat_container:
                            for msg in all_msgs[start:]:
                                _render_single_message(msg)
                                _chat_window['dom_count'] += 1

                        if start > 0:
                            load_more_banner.classes(remove='hidden')
                        else:
                            load_more_banner.classes(add='hidden')

                        state.chat_force_refresh = False
                        state.new_messages.clear()

                        if chat_scroll_state['auto']:
                            chat_scroll_state['suppress_until'] = time.time() + 0.5
                            ui.timer(0.1, lambda: chat_scroll.scroll_to(percent=1.0), once=True)


                    def update_chat():
                        active_id = state.active_channel_id

                        if active_id == 'default':
                            all_msgs = list(state.messages)
                            new_msgs_source = state.new_messages[:]
                            state.new_messages.clear()
                        else:
                            all_msgs = list(state.channel_messages.get(active_id, deque()))
                            new_msgs_source = []  # extra channels: update only via force_refresh

                        if state.chat_force_scroll:
                            chat_scroll_state['auto'] = True
                            chat_resume_btn.classes(add='hidden')
                            state.chat_force_scroll = False

                        # Force refresh: applies to ALL channels (default and extra)
                        if state.chat_force_refresh:
                            _do_force_refresh(all_msgs)
                            return  # new_msgs_source is ignored: we have already rendered everything

                        # Update channel badges
                        for cid, refs in channel_tab_refs.items():
                            dot = refs.get('dot')
                            if dot:
                                cnt = int(state.channel_unread_count.get(cid, 0) or 0)
                                dot.text = ('99+' if cnt > 99 else (str(cnt) if cnt > 0 else ''))
                                if cnt > 0:
                                    dot.classes(remove='hidden')
                                else:
                                    dot.classes(add='hidden')

                        if not new_msgs_source:
                            return

                        # Add new messages to the end
                        while new_msgs_source:
                            msg = new_msgs_source.pop(0)
                            with chat_container:
                                _render_single_message(msg)
                            _chat_window['dom_count'] += 1

                        # Remove old messages from the DOM if exceeds the threshold
                        # Data remains intact in state.messages / state.channel_messages
                        while _chat_window['dom_count'] > _CHAT_DOM_WINDOW + _CHAT_LOAD_STEP:
                            children = list(chat_container)
                            # children[0] = banner, children[1] = oldest message in the DOM
                            if len(children) > 1:
                                children[1].delete()
                                _chat_window['dom_count'] -= 1
                                _chat_window['offset'] += 1
                                load_more_banner.classes(remove='hidden')

                        if chat_scroll_state['auto']:
                            chat_scroll_state['suppress_until'] = time.time() + 0.5
                            ui.timer(0.1, lambda: chat_scroll.scroll_to(percent=1.0), once=True)

                    # Bottom: Console log
                    ui.separator()
                    with ui.row().classes('w-full items-center justify-between p-2 mb-1'):
                        ui.label(translate("ui.consolelog", "Console Log")).classes('font-bold')
                        
                        # Back to scroll button
                        log_scroll_btn = ui.button(
                            translate("button.resumeautoscroll", "Resume Auto-Scroll"), 
                            icon='arrow_downward', 
                            on_click=lambda: ui.run_javascript(f'const sc = document.getElementById("c{log_view.id}")?.querySelector(".q-scrollarea__container"); if(sc) sc.scrollTop = sc.scrollHeight;')
                        ).props('dense color=green').classes('hidden transition-opacity')
                
                    log_view = ui.log(max_lines=500).classes('h-1/3 w-full bg-black text-green-500 p-2 font-mono text-xs overflow-auto')
                    
                    ui.run_javascript(f'''
                        (function tryAttach(attempts) {{
                            const root = document.getElementById("c{log_view.id}");
                            if (!root) {{
                                if (attempts > 0) setTimeout(() => tryAttach(attempts - 1), 300);
                                return;
                            }}
                            const scrollEl = root.querySelector(".q-scrollarea__container");
                            const btnRoot = document.getElementById("c{log_scroll_btn.id}");
                            if (!scrollEl || !btnRoot) {{
                                if (attempts > 0) setTimeout(() => tryAttach(attempts - 1), 300);
                                else console.warn("log scroll: elements not found after all attempts");
                                return;
                            }}
                            scrollEl.addEventListener('scroll', function() {{
                                const diff = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight;
                                if (diff > 80) {{
                                    btnRoot.classList.remove('hidden');
                                }} else {{
                                    btnRoot.classList.add('hidden');
                                }}
                            }}, {{ passive: true }});
                        }})(15);
                    ''')

                    def _log_needs_spacer(msg: str) -> bool:
                        low = msg.lower()
                        no_spacer_keywords = ["crc valid", "crc invalid"]
                        return not any(k in low for k in no_spacer_keywords)

                    for l in state.logs:
                        if _log_needs_spacer(l):
                            log_view.push("\u200b")
                        log_view.push(l)

                    def update_log():
                        if not state.new_logs: return
                        batch = state.new_logs[:]
                        state.new_logs.clear()
                        for l in batch:
                            if _log_needs_spacer(l):
                                log_view.push('\u200b')
                            log_view.push(l)


    def _check_autosave_on_start():
        autosave_path = get_autosave_path()
        if not os.path.isfile(autosave_path):
            return

        with ui.dialog() as dlg, ui.card().classes('w-96'):
            ui.label(translate("popup.autosave.found.title", "Autosave Found")).classes('text-lg font-bold mb-2')
            ui.label(translate("popup.autosave.found.body", "Previous autosave data has been found. Do you want to load it?")).classes('text-sm text-gray-600 mb-4')

            def do_load():
                try:
                    with open(autosave_path, 'r') as f:
                        data = json.load(f)
                    imported_nodes_count, total_nodes_in_file = _import_data_from_dict(data)
                    dlg.close()
                    with ui.dialog() as summary_dialog, ui.card().classes('w-96'):
                        ui.label(translate("popup.autosave.import.summary.title", "Autosave Import Summary")).classes('text-xl font-bold text-green-600 mb-4')
                        with ui.column().classes('w-full gap-2'):
                            ui.label(translate("popup.importdata.success.nodesinfile", "Nodes in File: {nodes_count}").format(nodes_count=total_nodes_in_file)).classes('text-lg')  
                            ui.label(translate("popup.importdata.success.nodesimported", "Nodes Imported: {nodes_imported_count}").format(nodes_imported_count=imported_nodes_count)).classes('text-lg font-bold')
                            ui.separator()
                            ui.label(translate("popup.importdata.success.totalnodesinapp", "Total Nodes in App: {total_nodes_in_app}").format(total_nodes_in_app=len(state.nodes))).classes('text-md text-gray-600')
                        ui.button('OK', on_click=summary_dialog.close).classes('w-full mt-4 bg-green-600')
                    summary_dialog.open()
                except Exception as e:
                    dlg.close()
                    ui.notify(translate("notification.error.autosaveimportfailed", "Autosave import failed: {error}").format(error=e), type='negative')

            def skip_load():
                dlg.close()
                try:
                    base_dir = os.path.dirname(autosave_path)
                    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    base_name = os.path.basename(autosave_path)
                    new_name = f"backup_data_{ts}_{base_name}"
                    new_path = os.path.join(base_dir, new_name)
                    os.rename(autosave_path, new_path)
                    with ui.dialog() as info_dlg, ui.card().classes('w-96'):
                        ui.label(translate("popup.autosave.archived.title", "Autosave Archived")).classes('text-lg font-bold mb-2')
                        ui.label(translate("popup.autosave.archived.body", "Previous autosave file has been renamed to:")).classes('text-sm text-gray-600')
                        ui.label(new_path).classes('text-sm font-mono break-all')
                        ui.separator().classes('my-2')
                        ui.label(translate("popup.autosave.archived.help1", "If you want to keep working with this data, import this backup file using the Import Data function.")).classes('text-sm text-gray-600')
                        ui.label(translate("popup.autosave.archived.help2", "Imported data will be merged with the current session, and future autosaves will include both new data and the imported backup.")).classes('text-sm text-gray-600')
                        ui.button('OK', on_click=info_dlg.close).classes('w-full mt-2 bg-slate-200 text-slate-900')
                    info_dlg.open()
                except Exception as e:
                    ui.notify(translate("notification.error.autosaverenamefailed", "Autosave rename failed: {error}").format(error=e), type='negative')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button(translate("button.no", "No"), on_click=skip_load).classes('bg-slate-200 text-slate-900')
                ui.button(translate("button.yes", "Yes"), on_click=do_load).classes('bg-blue-600 text-white')

        dlg.open()

    ui.run_javascript("""
    (function() {
        var el = getElement(window.mesh_main_map_id);
        var map = el && el.map;
        if (!map) return;
        map.on('moveend', function() {
            try {
                var c = map.getCenter();
                fetch('/set_map_center', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({lat: c.lat, lng: c.lng, zoom: map.getZoom()}),
                    keepalive: true
                });
            } catch(e) {}
        });
    })();
    """)

    rebuild_channel_tabs()
    def _safe_timer(fn):
        if asyncio.iscoroutinefunction(fn):
            async def wrapper():
                try:
                    await fn()
                except Exception as e:
                    log_to_console(f"[TIMER ERROR] {fn.__name__}: {e}")
        else:
            def wrapper():
                try:
                    fn()
                except Exception as e:
                    log_to_console(f"[TIMER ERROR] {fn.__name__}: {e}")
        wrapper.__name__ = fn.__name__  # preserve log name
        return wrapper

    ui.timer(1.2, _safe_timer(update_map))
    ui.timer(0.2, _safe_timer(update_grid))
    ui.timer(0.5, _safe_timer(update_chat))
    ui.timer(0.2, _safe_timer(update_log))
    ui.timer(1.0, _safe_timer(_autosave_tick))
    ui.timer(0.2, _safe_timer(_rtlsdr_error_ui_tick))

    def manual_gc_cleanup():
        # Debug function to manually trigger garbage collection
        import gc
        print("DEBUG: MANUAL GC CLEANUP", flush=True)
        gc.collect()
    if DEBUGGING:
        ui.timer(30.0, manual_gc_cleanup)

    _check_autosave_on_start()
    if not state.connection_dialog_shown:
        state.connection_dialog_shown = True
        connection_dialog.open()

def open_chrome_app(url: str):
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome_path):
        show_fatal_error(
            "Google Chrome not found",
            f"{PROGRAM_NAME} requires Google Chrome to run on macOS (to avoid Apple WebKit nightmare).\n\n"
            "Please install Chrome from https://www.google.com/chrome and relaunch the application."
        )
        sys.exit(1)

    return subprocess.Popen([
        chrome_path,
        "--app=" + url,
        "--disable-features=Translate,TranslateUI",
        "--disable-translate",
        "--disable-session-crashed-bubble",
        "--no-first-run",
        "--disable-gpu",
    ])

def find_free_port(start_port: int = 8000, max_tries: int = 100) -> int:
    for port in range(start_port, start_port + max_tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', port))
            s.close()
            return port
        except OSError:
            s.close()
            continue
    raise RuntimeError("No free port found")

def _detect_window_size():
    base_w, base_h = 1200, 720
    try:
        root = tk.Tk()
        root.withdraw()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.destroy()
        w = min(base_w, max(800, sw - 80))
        h = min(base_h, max(600, sh - 80))
        return w, h
    except Exception:
        return base_w, base_h

if __name__ in {"__main__", "__mp_main__"}:
    if sys.platform.startswith("linux"):
        try:
            multiprocessing.set_start_method("spawn", force=True)
        except RuntimeError:
            pass
    # Mandatory for PyInstaller on macOS/Linux to prevent infinite spawn loop
    multiprocessing.freeze_support()

    # Suppress NiceGui logging and others logs in distribution to avoid confusion, keep it only in debug mode.
    import logging as _logging
    if not DEBUGGING:
        _logging.getLogger("nicegui").setLevel(_logging.ERROR)
        _logging.getLogger("uvicorn").setLevel(_logging.ERROR)
        _logging.getLogger("uvicorn.access").setLevel(_logging.ERROR)
        _logging.getLogger("uvicorn.error").setLevel(_logging.ERROR)
        ncgwmex = False            
    else:
        ncgwmex = True

    core.sio.max_http_buffer_size = 128 * 1024 * 1024 # set max websocket buffer size to 128MB to manage offline map and large imports

    # === GLOBAL ERROR HANDLER ===
    import sys as _sys
    import threading as _threading
    import traceback as _tb

    def _fatal(exc_type, exc_value, exc_tb):
        msg = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
        try:
            show_fatal_error(f"{PROGRAM_NAME} — Unhandled Exception", msg)
        except Exception:
            pass

    _sys.excepthook = lambda t, v, tb: _fatal(t, v, tb)

    def _thread_fatal(args):
        _fatal(args.exc_type, args.exc_value, args.exc_traceback)
    _threading.excepthook = _thread_fatal
    # === ASYNCIO UNHANDLED ERRORS ===
    def _asyncio_exception_handler(loop, context):
        msg = context.get("exception", context.get("message", "unknown"))
        log_to_console(f"[ASYNCIO UNHANDLED] {msg}")
        import traceback as _tb
        exc = context.get("exception")
        if exc:
            full_msg = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        else:
            full_msg = f"Unhandled async error:\n{msg}"
        print(f"[ASYNCIO UNHANDLED]\n{full_msg}", file=sys.stderr)
        threading.Thread(
            target=show_fatal_error,
            args=(f"{PROGRAM_NAME} — Async Error", full_msg),
            daemon=True
        ).start()

    async def _set_asyncio_handler():
        global MAIN_LOOP
        loop = asyncio.get_running_loop()
        MAIN_LOOP = loop
        loop.set_exception_handler(_asyncio_exception_handler)
        if DEBUGGING:
            print("DEBUG: asyncio handler installed", flush=True)

    app.on_startup(_set_asyncio_handler)
    # === GLOBAL ERROR HANDLER END ===

    if DEBUGGING:
        import faulthandler
        faulthandler.enable()
        print("DEBUG: MAIN STARTED", flush=True)
    
    try:
        if not check_native_runtime_deps():
            sys.exit(1)
        
        if multiprocessing.current_process().name == 'MainProcess':
            if _NOGPU_REQUESTED:
                print(f"[{PROGRAM_NAME}] Software rendering mode active (--nogpu). GPU/hardware acceleration is disabled.", flush=True)
            print(f"{PROGRAM_NAME} started.", flush=True)
            start_tk_splash()
            
        ensure_app_icon_file()
        system = platform.system()

        if system == "Darwin":
            try:
                port = find_free_port(8000)
            except RuntimeError as e:
                show_fatal_error(
                    f"{PROGRAM_NAME} — Network Error",
                    f"Could not find a free network port to start the application:\n\n{e}\n\n"
                    "Try closing other applications and restarting."
                )
                sys.exit(1)
            
            try:
                import certifi
                import ssl
                os.environ.setdefault('SSL_CERT_FILE', certifi.where())
                os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
            except ImportError:
                pass
            
            def on_startup():
                import time
                time.sleep(0.5)
                
                url = f"http://127.0.0.1:{port}"
                open_chrome_app(url)
            
            app.on_startup(on_startup)

            def _handle_sigterm(signum, frame):
                _shutdown_cleanup()

            try:
                signal.signal(signal.SIGTERM, _handle_sigterm)
            except Exception:
                pass

            ui.run(
                title=f'{PROGRAM_NAME} v{VERSION} By {AUTHOR}',
                favicon=get_resource_path('app_icon.svg'),
                host='127.0.0.1',
                port=port,
                reload=False,
                show=False,
                show_welcome_message=ncgwmex
            )

            if DEBUGGING:
                print("DEBUG: ui.run() returned", flush=True)

        else:
            # Windows / Linux: use native pywebview
            # Enable dev tools if configured (must be done before ui.run implicitly or explicitly)
            if SHOW_DEV_TOOLS:
                # Enable debug mode for pywebview using NiceGUI's native configuration
                # This is the correct way to pass arguments to webview.start()
                app.native.start_args['debug'] = True
                
                # Also try setting the env var as a backup, though start_args should prevail
                os.environ['pywebview_debug'] = 'true'
                
                print("Dev Tools Enabled: Use Right-Click -> Inspect or F12 (if supported)")

            if system == "Linux":
                os.environ['LANG'] = 'C.UTF-8'
                os.environ['LC_ALL'] = 'C.UTF-8'

                os.environ["GSETTINGS_BACKEND"] = "memory"
                os.environ["QT_QPA_PLATFORMTHEME"] = "fusion"
                os.environ["QT_VIDEO_BACKEND"] = "dummy"
                os.environ["TK_SILENCE_DEPRECATION"] = "1"
                os.environ["PYWEBVIEW_GUI"] = "qt"

                # 2. mute qt video driver logging
                os.environ['QT_LOGGING_RULES'] = '*.debug=false;qt.qpa.*=false'
                os.environ['MESA_LOG_LEVEL'] = '0'
                # Check if running without a display (e.g. SSH without X forwarding)
                has_display = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
                if not has_display:
                    show_fatal_error(
                        f"{PROGRAM_NAME} - No Display Available",
                        f"{PROGRAM_NAME} is a desktop application and requires a graphical environment.\n\n"
                        "It looks like you are running it without a display (e.g. via SSH without X forwarding).\n\n"
                        f"Please run {PROGRAM_NAME} directly from your desktop environment.\n"
                        "If you need remote access, use a remote desktop solution (VNC, RDP, X forwarding)."
                    )
                    sys.exit(1)
                # Pre-check: verify OpenGL/EGL actually works at runtime
                if multiprocessing.current_process().name == 'MainProcess':
                    def _check_opengl_functional() -> bool:
                        """
                        Heuristic check for GPU rendering capabilities on Linux systems.
                        Attempts to verify hardware access, runtime probes, and library availability.
                        """
                        import ctypes.util
                        env = os.environ.copy()

                        # 1. Hardware-level check: Verify if the Direct Rendering Infrastructure (DRI) exists.
                        # The presence of /dev/dri usually indicates that the kernel-level graphics drivers are active.
                        if os.path.exists('/dev/dri'):
                            if DEBUGGING:
                                print("DEBUG: /dev/dri found, kernel-level drivers present", flush=True)

                        # 2. Functional Probe: Execute common CLI tools to verify a working OpenGL/EGL context.
                        # Checking the process return code (0) to confirm successful execution.
                        for cmd in [["glxinfo", "-B"], ["eglinfo"]]:
                            try:
                                # Use a short timeout to prevent blocking the main thread if the driver hangs.
                                r = subprocess.run(cmd, capture_output=True, timeout=3, env=env)
                                if r.returncode == 0:
                                    if DEBUGGING:
                                        print(f"DEBUG: {cmd[0]} probe successful, hardware acceleration likely available", flush=True)
                                    return True
                            except (FileNotFoundError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                                # Move to the next probe if the tool is missing or fails.
                                continue

                        # 3. Dynamic Linker Search: Use find_library to locate OpenGL or EGL runtimes.
                        target_libs = ['GL', 'GLESv2', 'EGL']
                        for lib_name in target_libs:
                            try:
                                lib_path = ctypes.util.find_library(lib_name)
                                if lib_path:
                                    # Attempt to load the library to verify compatibility with the current architecture.
                                    ctypes.CDLL(lib_path)
                                    if DEBUGGING:
                                        print(f"DEBUG: Successfully loaded {lib_name} via {lib_path}", flush=True)
                                    return True
                            except Exception as e:
                                if DEBUGGING:
                                    print(f"DEBUG: {lib_name} found at {lib_path} but failed to load: {e}", flush=True)

                        # 4. Path Fallback: Check standard locations for proprietary drivers (e.g., NVIDIA) 
                        # and Debian/Fedora common paths where find_library might occasionally fail in isolated environments.
                        fallback_paths = [
                            '/usr/lib/x86_64-linux-gnu/libGL.so.1',
                            '/usr/lib64/libGL.so.1',
                            '/usr/lib/libGL.so.1',
                            '/usr/lib/x86_64-linux-gnu/libEGL.so.1',
                            '/usr/lib64/libEGL.so.1'
                        ]
                        for path in fallback_paths:
                            if os.path.exists(path):
                                try:
                                    ctypes.CDLL(path)
                                    if DEBUGGING:
                                        print(f"DEBUG: Found and loaded fallback library at {path}", flush=True)
                                    return True
                                except Exception:
                                    continue

                        # No functional GPU rendering path was verified.
                        return False

                    if not _check_opengl_functional():
                        show_fatal_error(
                            f"{PROGRAM_NAME} - Graphics Libraries Not Found",
                            f"{PROGRAM_NAME} could not find OpenGL or EGL libraries on this system.\n\n"
                            "The application will attempt to start using software rendering.\n"
                            "If it fails to open, install the required graphics libraries:\n\n"
                            "  Fedora/RHEL:   sudo dnf install mesa-libGL mesa-libEGL\n"
                            "  Ubuntu/Debian: sudo apt install libgl1 libegl1\n"
                            "  Arch:          sudo pacman -S mesa\n\n"
                            "The application will now continue in software rendering mode.\n\n"
                            "If you are in a VM, try enabling 3D acceleration, however it is an issue with GPU access/3D acceleration."
                        )
                        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
                            os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '') +
                            ' --disable-gpu --disable-gpu-rasterization --disable-gpu-compositing --no-sandbox'
                        ).strip()
                        os.environ['QT_OPENGL'] = 'software'
                        os.environ['LIBGL_ALWAYS_SOFTWARE'] = '1'
                        os.environ['MESA_GL_VERSION_OVERRIDE'] = '3.3'
                        os.environ['MESA_GLSL_VERSION_OVERRIDE'] = '330'
                        os.environ['QT_XCB_GL_INTEGRATION'] = 'none'
                        os.environ['QSG_RENDER_LOOP'] = 'basic'
                        os.environ['QT_QUICK_BACKEND'] = 'software'
                try:
                    app.native.start_args['gui'] = 'qt'


                    # Fix DPI mismatch Qt/Chromium on X11 (root cause of ResizeObserver loops)
                    _existing = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
                    if '--force-device-scale-factor' not in _existing:
                        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
                            _existing + ' --force-device-scale-factor=1 --disable-features=LayoutNG,CalculateNativeWinOcclusion'
                        ).strip()
                except Exception:
                    pass

            # Use bundled icon (SVG, supported as NiceGUI favicon)
            if system == "Linux" and getattr(sys, 'frozen', False):
                icon_path_for_favicon = get_resource_path('app_icon.png')
            else:
                icon_path_for_favicon = get_resource_path('app_icon.svg')

            # Native mode arguments for better compatibility
            # macOS often needs specific flags to avoid crashes (e.g. reload=False is crucial)
            # Linux GTK can also be picky.
            win_w, win_h = _detect_window_size()
            ui.run(
                title=f'{PROGRAM_NAME} - {PROGRAM_SHORT_DESC} v{VERSION} By {AUTHOR}', 
                favicon=icon_path_for_favicon, 
                native=True, 
                host='127.0.0.1',
                reload=False, # Important for stability in native mode
                window_size=(win_w, win_h),
                show_welcome_message=ncgwmex,
                storage_secret='meshstation_secret' # Adding a secret often helps with pywebview storage init
            )
    except KeyboardInterrupt:
        if DEBUGGING:
            print("DEBUG: KeyboardInterrupt in MAIN", flush=True)
        else:
            pass # Suppress KeyboardInterrupt traceback on exit
    except SystemExit:
        if DEBUGGING:
            print("DEBUG: SystemExit in MAIN", flush=True)
        raise  # keep sys.exit() without intercepting it
    except Exception as e:
        # Log other unexpected errors but don't show traceback if it's just a shutdown thing
        if DEBUGGING:
            import traceback
            print("DEBUG: UNHANDLED EXCEPTION IN MAIN", flush=True)
            traceback.print_exc()
        
        show_fatal_error(
            f"{PROGRAM_NAME} — Unexpected Error",
            f"The application encountered an unexpected error and cannot continue:\n\n{e}"
        )
        sys.exit(1)
