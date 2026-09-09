import base64

def msb2lsb(msb):
    if not msb:
        return "00000000"
    if isinstance(msb, str):
        try:
            msb = bytes.fromhex(msb)
        except ValueError:
            return "00000000"
    if len(msb) < 4:
        msb = msb.rjust(4, b'\x00')
    return msb[::-1].hex()

def _xor_hash_bytes(data: bytes) -> int:
    h = 0
    for b in data:
        h ^= b
    return h

def _meshtastic_channel_hash(channel_name: str, key_bytes: bytes) -> int:
    if not channel_name:
        return 0
    name_bytes = channel_name.encode('utf-8')
    h = 0x811c9dc5 # FNV offset basis
    for b in name_bytes:
        h = (h * 0x01000193) & 0xffffffff
    
    if key_bytes:
        for b in key_bytes:
            h ^= b
            h = (h * 0x01000193) & 0xffffffff
    
    # Meshtastic uses a simpler XOR sum for the final byte in some versions, 
    # but the official hash is more like this or a XOR of the name + key.
    # Re-checking the code: L1479 in MeshStation.py uses a custom _xor_hash_bytes
    # and L1485 uses it. Let's replicate EXACTLY what was there.
    return _xor_hash_bytes(name_bytes) ^ (_xor_hash_bytes(key_bytes) if key_bytes else 0)

def hexStringToBinary(hexString):
    try:
        return bytes.fromhex(hexString)
    except ValueError:
        return b''

def _i16_from_be(b0, b1):
    val = (b0 << 8) | b1
    if val & 0x8000:
        val -= 0x10000
    return val

def parseAESKey(key_b64):
    try:
        if not key_b64:
            return None
        decoded = base64.b64decode(key_b64)
        if len(decoded) in (16, 32):
            return decoded
        # Meshtastic default "AQ==" is just 0x01, but in MeshStation it means "use default"
        return decoded
    except Exception:
        return None

def _djb2_hash(s: str) -> int:
    h = 5381
    for char in s:
        h = ((h << 5) + h) + ord(char)
    return h & 0xFFFFFFFF

def meshtastic_calc_freq(region_key: str, preset_key: str, frequency_slot: int = 0, channel_name: str | None = None) -> dict:
    import math
    from core.constants import MESHTASTIC_REGIONS, MESHTASTIC_MODEM_PRESETS
    from utils.i18n import translate

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
    band_width = freq_end - freq_start
    slot_width = spacing + bw_mhz
    if slot_width <= 0:
        return {"valid": False, "error": "Invalid slot width (spacing + bw <= 0)"}

    num_slots = int(math.floor(band_width / slot_width))
    if num_slots < 1:
        narerrtext = translate("panel.connection.settings.internal.info.band_too_narrow", "Band too narrow for preset: only {band_width}MHz available, need {slot_width}MHz per slot, preset not compatible with this region.").format(band_width=f"{band_width:.3f}", slot_width=f"{slot_width:.3f}")
        return {"valid": False, "error": narerrtext}
    
    ch_name = channel_name if channel_name else preset["channel_name"]
    if frequency_slot != 0:
        channel_num_0 = (frequency_slot - 1) % num_slots
    else:
        channel_num_0 = _djb2_hash(ch_name) % num_slots
    
    freq_mhz = freq_start + (bw_mhz / 2.0) + channel_num_0 * slot_width
    
    return {
        "valid": True,
        "error": None,
        "center_freq_mhz": freq_mhz,
        "center_freq_hz": int(round(freq_mhz * 1_000_000)),
        "num_slots": num_slots,
        "slot_used": channel_num_0 + 1,
        "bw_khz": bw_khz,
        "sf": sf,
        "cr": cr,
        "channel_name": ch_name,
    }
