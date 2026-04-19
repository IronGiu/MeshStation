import time
import base64
from datetime import datetime
from collections import deque
from meshtastic import mesh_pb2, admin_pb2, telemetry_pb2, config_pb2

from core.state import state
from core.stats import mesh_stats
from core.constants import MESHTASTIC_MODEM_PRESETS, PRESET_ID_MAP
from utils.helpers import msb2lsb, _meshtastic_channel_hash, _i16_from_be
from utils.i18n import translate
from processing.nodes import update_node
from processing.extract import dataExtractor
from processing.decrypt import dataDecryptor

def decodeProtobuf(packetData, sourceID, destID, cryptplainprefix, *, 
                   count_invalid: bool = True, preset_name: str | None = None, 
                   channel_hash: int | None = None, forced_channel_id: str | None = None, 
                   packet_id: bytes | None = None, log_to_console_callback=None):
    try:
        data = mesh_pb2.Data()
        data.ParseFromString(packetData)
    except Exception as e:
        if count_invalid:
            mesh_stats.on_invalid_protobuf()
        return translate("log.invalid_protobuf", "INVALID PROTOBUF: {e}").format(e=e)

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
            default_ch_name = MESHTASTIC_MODEM_PRESETS.get(
                getattr(state, 'direct_preset', 'MEDIUM_FAST'), {}
            ).get('channel_name', 'LongFast')
        
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
                        state.channel_unread_count[ch_id] = state.channel_unread_count.get(ch_id, 0) + 1
                    routed = True
                    break

        if not routed and channel_hash is not None:
            # Note: _get_extra_channel_keys needs careful handling to avoid circular imports
            # We'll expect state.extra_channels to be populated
            for ch in state.extra_channels:
                # We need to re-calculate hash or store it
                # For now let's use a simplified check or expect hash in channel dict
                if ch.get("hash") == channel_hash:
                    ch_id = ch.get("id")
                    if ch_id:
                        if ch_id not in state.channel_messages:
                            state.channel_messages[ch_id] = deque(maxlen=100)
                        state.channel_messages[ch_id].append(msg_obj)
                        if state.active_channel_id != ch_id:
                            state.channel_unread[ch_id] = True
                            state.channel_unread_count[ch_id] = state.channel_unread_count.get(ch_id, 0) + 1
                        routed = True
                        break

        if not routed:
            state.messages.append(msg_obj)
            state.new_messages.append(msg_obj)
            if state.active_channel_id != 'default':
                state.channel_unread['default'] = True
                state.channel_unread_count['default'] = state.channel_unread_count.get('default', 0) + 1

        log_msg = translate("log.text_msg", "{prefix} TEXT MSG from {from_id}: {text}").format(
            prefix=cryptplainprefix, from_id=sourceID, text=text
        )
        update_node(sourceID, log_to_console_callback=log_to_console_callback)
        
        if state.db:
            state.db.save_packet({
                "ts": time.time(),
                "from_id": sourceID,
                "to_id": destID,
                "type": "TEXT",
                "text": text,
                "channel_id": forced_channel_id or "default"
            })
        
    elif data.portnum == 3: # POSITION_APP
        pos = mesh_pb2.Position()
        try:
            pos.ParseFromString(data.payload)
        except Exception as e:
            if log_to_console_callback:
                log_to_console_callback(f"POSITION parse error from {sourceID}: {e}")
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
            loc_source = pos.DESCRIPTOR.fields_by_name['location_source'].enum_type.values_by_number[val].name
        except Exception:
            loc_source = f"Enum_{pos.location_source}"

        kwargs = {"lat": lat, "lon": lon, "location_source": loc_source}
        if altitude_m is not None:
            kwargs["altitude"] = altitude_m
        update_node(sourceID, log_to_console_callback=log_to_console_callback, **kwargs)
        log_msg = translate("log.position", "{prefix} POSITION from {from_id}: {lat}, {lon} ({source})").format(
            prefix=cryptplainprefix, from_id=sourceID, lat=lat, lon=lon, source=loc_source
        )
        
    elif data.portnum == 4: # NODEINFO_APP
        info = mesh_pb2.User()
        try:
            info.ParseFromString(data.payload)
        except Exception as e:
            if log_to_console_callback:
                log_to_console_callback(f"NODEINFO parse error from {sourceID}: {e}")
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
            hw_model_name = f"Model_{info.hw_model}"

        public_key = None
        try:
            pk = getattr(info, 'public_key', None)
            if isinstance(pk, (bytes, bytearray)) and pk:
                public_key = base64.b64encode(bytes(pk)).decode('ascii')
            elif isinstance(pk, str) and pk:
                public_key = pk
        except Exception:
            pass

        macaddr = None
        try:
            mac_val = getattr(info, 'macaddr', None)
            if isinstance(mac_val, (bytes, bytearray)) and len(mac_val) == 6:
                macaddr = ":".join(f"{b:02x}" for b in mac_val)
            elif isinstance(mac_val, int) and mac_val:
                mac_bytes = mac_val.to_bytes(6, byteorder="big", signed=False)
                macaddr = ":".join(f"{b:02x}" for b in mac_bytes)
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

        update_node(sourceID, log_to_console_callback=log_to_console_callback, **nodeinfo_kwargs)
        log_msg = translate("log.nodeinfo", "{prefix} NODEINFO from {from_id}: {short} ({long})").format(
            prefix=cryptplainprefix, from_id=sourceID, short=info.short_name, long=info.long_name
        )
        
    elif data.portnum == 67: # TELEMETRY_APP
        tel = telemetry_pb2.Telemetry()
        try:
            tel.ParseFromString(data.payload)
        except Exception as e:
            if log_to_console_callback:
                log_to_console_callback(f"TELEMETRY parse error from {sourceID}: {e}")
            return ""
        decoded_obj = tel
        metrics = {}
        
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
            
        update_node(sourceID, log_to_console_callback=log_to_console_callback, **metrics)
        mesh_stats.on_telemetry(sourceID, metrics)
        log_msg = translate("log.telemetry", "{prefix} TELEMETRY from {from_id}").format(
            prefix=cryptplainprefix, from_id=sourceID
        )
        
    elif data.portnum == 70: # TRACEROUTE
        route = mesh_pb2.RouteDiscovery()
        try:
            route.ParseFromString(data.payload)
        except Exception as e:
            if log_to_console_callback:
                log_to_console_callback(f"TRACEROUTE parse error from {sourceID}: {e}")
            return ""
        decoded_obj = route
        log_msg = translate("log.traceroute", "{prefix} TRACEROUTE from {from_id}").format(
            prefix=cryptplainprefix, from_id=sourceID
        )
        update_node(sourceID, log_to_console_callback=log_to_console_callback)
        
    else:
        log_msg = translate("log.app_packet", "{prefix} APP Packet ({port}) from {from_id}").format(
            prefix=cryptplainprefix, port=data.portnum, from_id=sourceID
        )
        update_node(sourceID, log_to_console_callback=log_to_console_callback)

    if state.verbose_logging and decoded_obj:
        try:
            log_msg += f"\n{decoded_obj}"
        except:
            pass

    return log_msg

def parse_framed_stream_bytes(rx_buf: bytearray, log_to_console_callback=None):
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

        if ftype == 0x03:
            try:
                if len(body) < 2 + 1 + 4:
                    continue

                payload_len = (body[0] << 8) | body[1]
                if len(body) < 2 + payload_len + 1 + 4:
                    continue

                payload = body[2:2 + payload_len]
                flags_off = 2 + payload_len
                flags = body[flags_off]
                snr10 = _i16_from_be(body[flags_off + 1], body[flags_off + 2])
                rssi10 = _i16_from_be(body[flags_off + 3], body[flags_off + 4])

                has_metrics = (flags & 0x01) != 0
                snr_val = (snr10 / 10.0) if has_metrics else None
                rssi_val = (rssi10 / 10.0) if has_metrics else None
                
                preset_id_off = flags_off + 5
                frame_preset_id = body[preset_id_off] if len(body) > preset_id_off else 0
                frame_preset_name = PRESET_ID_MAP.get(frame_preset_id)

                extracted = dataExtractor(payload.hex())

                hops_val = None
                hop_label = None
                try:
                    flags_bytes = extracted.get('flags', b'')
                    if flags_bytes:
                        fb = flags_bytes[0]
                        hop_limit = fb & 0x07
                        hop_start = (fb >> 5) & 0x07
                        hops_val = hop_start - hop_limit
                        if hops_val < 0: hops_val = 0
                        hop_label = "direct" if hops_val == 0 else str(hops_val)
                except Exception:
                    pass

                if mesh_stats.consume_crc_invalid_packet(extracted.get("sender"), extracted.get("packetID")):
                    mesh_stats.on_packet_received(None, hops_val, snr_val, rssi_val)
                    continue

                mesh_stats.on_frame_ok()

                ch_hash_byte = extracted.get('channelHash', None)
                channel_hash_int = ch_hash_byte[0] if isinstance(ch_hash_byte, (bytes, bytearray)) and ch_hash_byte else None

                s_id = msb2lsb(extracted['sender'].hex())
                d_id = msb2lsb(extracted['dest'].hex())
                s_id_fmt = f"!{int(s_id, 16):x}"
                d_id_fmt = f"!{int(d_id, 16):x}"

                mesh_stats.on_packet_received(s_id_fmt, hops_val, snr_val, rssi_val)
                
                info = None
                decrypted_ok = False
                plaintext_ok = False

                candidates = []
                if channel_hash_int is not None:
                    for ch in state.extra_channels:
                        if ch.get("hash") == channel_hash_int and ch.get("key"):
                             candidates.append((ch["key"], ch.get("id")))
                
                if state.aes_key_bytes:
                    candidates.append((state.aes_key_bytes, None))

                for key_bytes, forced_id in candidates:
                    try:
                        decrypted = dataDecryptor(extracted, key_bytes)
                        prefix = translate("log.prefix.decrypted", "[DECRYPTED]")
                        info = decodeProtobuf(
                            decrypted, s_id_fmt, d_id_fmt, prefix,
                            count_invalid=False, preset_name=frame_preset_name,
                            channel_hash=channel_hash_int, forced_channel_id=forced_id,
                            packet_id=extracted.get('packetID'),
                            log_to_console_callback=log_to_console_callback
                        )
                        if info and not str(info).startswith("INVALID PROTOBUF"):
                            decrypted_ok = True
                            break
                    except Exception:
                        continue

                if not decrypted_ok:
                    try:
                        raw_data = extracted['data']
                        prefix = translate("log.prefix.unencrypted", "[UNENCRYPTED]")
                        info = decodeProtobuf(
                            raw_data, s_id_fmt, d_id_fmt, prefix,
                            count_invalid=False, preset_name=frame_preset_name,
                            channel_hash=channel_hash_int,
                            packet_id=extracted.get('packetID'),
                            log_to_console_callback=log_to_console_callback
                        )
                        if info and not str(info).startswith("INVALID PROTOBUF"):
                            plaintext_ok = True
                        else:
                            info = None
                    except Exception:
                        info = None

                if decrypted_ok:
                    mesh_stats.on_decrypt_ok()
                elif not plaintext_ok:
                    mesh_stats.on_decrypt_fail()
                
                if info:
                    if log_to_console_callback:
                        preset_tag = f" -- Preset: {frame_preset_name}" if frame_preset_name else ""
                        log_to_console_callback(f"{info}{preset_tag}")
                    
                    if hops_val is not None or hop_label is not None:
                        update_node(s_id_fmt, log_to_console_callback=log_to_console_callback, hops=hops_val, hop_label=hop_label)
                    
                    if has_metrics:
                        if hops_val == 0:
                            update_node(s_id_fmt, log_to_console_callback=log_to_console_callback, snr=snr_val, rssi=rssi_val)
                        else:
                            update_node(s_id_fmt, log_to_console_callback=log_to_console_callback, snr_indirect=snr_val, rssi_indirect=rssi_val)
                    update_node(s_id_fmt, log_to_console_callback=log_to_console_callback, preset=frame_preset_name)

            except Exception as e:
                mesh_stats.on_frame_fail()
                if log_to_console_callback:
                    log_to_console_callback(f"Parse Error (0x03): {e}")
            continue
