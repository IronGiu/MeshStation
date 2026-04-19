import json
import os
import re
from core.state import state
from core.constants import MESHTASTIC_REGIONS, MESHTASTIC_MODEM_PRESETS, PROGRAM_NAME
from utils.paths import get_config_path

def load_user_config(log_to_console_callback=None, show_fatal_error_callback=None, i18n_module=None):
    try:
        path = get_config_path()
        if not os.path.isfile(path):
            return
        with open(path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        if show_fatal_error_callback:
            show_fatal_error_callback(
                f"{PROGRAM_NAME} — Config File Error",
                f"The configuration file is corrupted and will be ignored:\n{get_config_path()}\n\n"
                f"Error: {e}\n\n"
                "Default settings will be used.\n"
                "You can delete/backup the config file and restart to reset all settings."
            )
        return
    except Exception as e:
        if log_to_console_callback:
            log_to_console_callback(f"Config load error: {e}")
        return
    
    s = state
    v = data.get("direct_region")
    if isinstance(v, str):
        _old_region_map = {"US_915": "US"}
        tv = _old_region_map.get(v, v)
        if tv in MESHTASTIC_REGIONS:
            s.direct_region = tv
            
    v = data.get("direct_preset")
    if isinstance(v, str):
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
        try: s.direct_frequency_slot = int(v)
        except Exception: pass
        
    v = data.get("direct_channel_name")
    if isinstance(v, str): s.direct_channel_name = v
    
    v = data.get("direct_ppm")
    if v is not None:
        try: s.direct_ppm = int(v)
        except Exception: pass
        
    v = data.get("direct_gain")
    if v is not None:
        try: s.direct_gain = int(v)
        except Exception: pass
        
    v = data.get("direct_device_args")
    if isinstance(v, str):
        tv = v.strip()
        known_drivers = r"\b(rtl|hackrf|bladerf|airspy|airspyhf|uhd|soapy|miri|redpitaya|file|rtl_tcp)\s*="
        if re.search(known_drivers, tv, flags=re.IGNORECASE):
            s.direct_device_args = tv
        elif tv == "":
            s.direct_device_args = ""
        else:
            s.direct_device_args = tv
            
    v = data.get("direct_bias_tee")
    if isinstance(v, bool): s.direct_bias_tee = v
    
    v = data.get("direct_port")
    if isinstance(v, str): s.direct_port = v
    
    v = data.get("direct_key_b64")
    if isinstance(v, str): s.direct_key_b64 = v
    
    v = data.get("external_ip")
    if isinstance(v, str): s.external_ip = v
    
    v = data.get("external_port")
    if isinstance(v, str): s.external_port = v
    
    v = data.get("external_key_b64")
    if isinstance(v, str): s.external_key_b64 = v
    
    v = data.get("autosave_interval_sec")
    if v is not None:
        try: s.autosave_interval_sec = int(v)
        except Exception: pass
        
    v = data.get("verbose_logging")
    if isinstance(v, bool): s.verbose_logging = v
    
    v = data.get("theme")
    if isinstance(v, str):
        tv = v.strip().lower()
        if tv in ("auto", "dark", "light"):
            s.theme = "light" if tv == "auto" else tv
            
    v = data.get("language")
    if isinstance(v, str) and i18n_module:
        i18n_module.set_current_language(v)
        # Handle user_language_from_config flag if needed
        
    v = data.get("map_center_lat")
    if v is not None:
        try: s.map_center_lat = float(v)
        except Exception: pass
        
    v = data.get("map_center_lng")
    if v is not None:
        try: s.map_center_lng = float(v)
        except Exception: pass
        
    v = data.get("map_zoom")
    if v is not None:
        try: s.map_zoom = int(v)
        except Exception: pass
        
    v = data.get("extra_channels")
    if isinstance(v, list):
        s.extra_channels = [
            ch for ch in v
            if isinstance(ch, dict) and 'id' in ch and 'name' in ch and 'key_b64' in ch
        ]
        
    v = data.get("channels_order")
    if isinstance(v, list):
        s.channels_order = [x for x in v if isinstance(x, str)]

def save_user_config(log_to_console_callback=None, current_language="en"):
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
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        if log_to_console_callback:
            log_to_console_callback(f"Config save error: {e}")
