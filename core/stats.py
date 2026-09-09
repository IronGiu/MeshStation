import time
import threading
from collections import deque

def _fmt_num(x, digits: int = 0):
    if x is None:
        return "-"
    try:
        if digits <= 0:
            return f"{int(round(float(x)))}"
        return f"{float(x):.{digits}f}"
    except Exception:
        return str(x)

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

# Global instance
mesh_stats = MeshStatsManager()
