import argparse
import queue
import signal
import sys
import time

from .tcp_server import TCPFrameServer
from .pdu_sink import PDUSink

# Flowgraph
from .flowgraphs.rx_lora_base_engine import build_top_block


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="meshtastic-radio-engine")
    ap.add_argument("--host", default="127.0.0.1", help="Bind address for TCP output")
    ap.add_argument("--port", type=int, default=20002, help="Bind port for TCP output")

    # Radio parameters (GUI can override)
    ap.add_argument("--center-freq", type=int, default=869_525_000, help="Center frequency (Hz)")
    ap.add_argument("--samp-rate", type=int, default=1_000_000, help="Sample rate (sps)")
    ap.add_argument("--lora-bw", type=int, default=250_000, help="LoRa bandwidth (Hz)")
    ap.add_argument("--sf", type=int, default=9, help="Spreading factor")

    ap.add_argument("--gain", type=float, default=30.0, help="RF gain")
    ap.add_argument("--ppm", type=float, default=0.0, help="Frequency correction (ppm)")
    ap.add_argument("--if-gain", type=int, default=20, help="IF gain")
    ap.add_argument("--bb-gain", type=int, default=20, help="Baseband gain")

    ap.add_argument("--device-args", default="", help="Osmocom source args (e.g. 'rtl=0')")
    ap.add_argument("--bias-tee", action="store_true", default=False, help="Enable bias-T / antenna power")
    ap.add_argument("--payload-wait-ms", type=int, default=None, help="Override aggregator payload_wait_ms")
    ap.add_argument("--metrics-ttl-ms", type=int, default=None, help="Override aggregator metrics_ttl_ms")
    ap.add_argument("--preset-id", type=int, default=0, help="Preset ID byte embedded in each frame (0=unset)")
    ap.add_argument("--extra-demod-configs", default=None, help="JSON list of extra demod chains, e.g. '[{\"sf\":9,\"bw\":250000,...}]'")

    ap.add_argument("--sync-word", type=int, default=None, help="Override LoRa sync word")
    ap.add_argument("--preamble-len", type=int, default=None, help="Override LoRa preamble length")

    args = ap.parse_args(argv)

    q = queue.Queue(maxsize=4000)
    server = TCPFrameServer(args.host, args.port, q)
    server.start()

    extra_demod_configs = None
    if args.extra_demod_configs:
        try:
            import json as _json
            extra_demod_configs = _json.loads(args.extra_demod_configs)
        except Exception as e:
            print(f"[ENGINE] Failed to parse extra-demod-configs: {e}", flush=True)

    tb = build_top_block(
        center_freq=args.center_freq,
        samp_rate=args.samp_rate,
        lora_bw=args.lora_bw,
        sf=args.sf,
        gain=args.gain,
        ppm=args.ppm,
        if_gain=args.if_gain,
        bb_gain=args.bb_gain,
        device_args=args.device_args,
        bias_tee=args.bias_tee,
        extra_demod_configs=extra_demod_configs,
        sync_word=args.sync_word,
        preamble_len=args.preamble_len,
    )

    # Optional: tweak aggregator timings from CLI
    if args.payload_wait_ms is not None:
        try:
            tb.aggregator.payload_wait_ms = int(args.payload_wait_ms)
        except Exception:
            pass
    if args.metrics_ttl_ms is not None:
        try:
            tb.aggregator.metrics_ttl_ms = int(args.metrics_ttl_ms)
        except Exception:
            pass
    try:
        tb.aggregator.preset_id = int(args.preset_id)
    except Exception:
        pass

    sink = PDUSink(q)
    tb.msg_connect(tb.aggregator, "out", sink, "in")
    for chain in getattr(tb, '_extra_chains', []):
        try:
            tb.msg_connect(chain['aggregator'], "out", sink, "in")
        except Exception as e:
            print(f"[ENGINE] Failed to connect extra chain: {e}", flush=True)

    stop = {"flag": False}

    def _sig(_signo, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    tb.start()
    try:
        while not stop["flag"]:
            time.sleep(0.2)
    finally:
        try:
            tb.stop()
            tb.wait()
        except Exception:
            pass
        server.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
