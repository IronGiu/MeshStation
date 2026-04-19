import os
import secrets

# --- Project Info ---
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

# --- Runtime ---
SHUTDOWN_TOKEN = secrets.token_urlsafe(24)

# --- Chat Constants ---
CHAT_DOM_WINDOW = 30      # messages visible in the DOM
CHAT_LOAD_STEP  = 20      # how many are loaded by pressing "Load more"

# --- Meshtastic Regions ---
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
