#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: rx_lora_base_engine
# Author: IronGiu (Base offered by: Josh Conway )
# Description: This flow is a base for meshtastic engine of MeshStation by irongiu , thus only needing a RTL-SDR.
# GNU Radio version: 3.10.12.0

from gnuradio import blocks
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import gr, pdu
import gnuradio.lora_sdr as lora_sdr
import numpy as np
import osmosdr
import time
from . import rx_lora_base_engine_epy_block_0 as epy_block_0  # embedded python block
from . import rx_lora_base_engine_epy_block_1 as epy_block_1  # embedded python block
from . import rx_lora_base_engine_epy_block_2 as epy_block_2  # embedded python block
import threading
import os
import io




class rx_lora_base_engine(gr.top_block):

    def __init__(
        self,
        center_freq=869525000,
        samp_rate=1000000,
        lora_bw=250000,
        sf=9,
        gain=30,
        ppm=69,
        if_gain=20,
        bb_gain=20,
        preamble_length=17,
        payload_length=237,
        impl_head=False,
        has_crc=True,
        sync_word=None,
        preamble_len=None,
        device_args="",
        bias_tee=False,
        extra_demod_configs=None,
    ):
        gr.top_block.__init__(self, "rx_lora_base_engine", catch_exceptions=True)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Functions
        ##################################################

        def _detect_device_from_args(device_args: str) -> str:
            # Identify SDR type from osmosdr args string only
            args = (device_args or "").lower()
            if "hackrf" in args:
                return "hackrf"
            if "bladerf" in args:
                return "bladerf"
            if "airspyhf" in args:
                return "airspyhf"
            if "airspy" in args:
                return "airspy"
            if "uhd" in args:
                return "uhd"
            if "rtl" in args:
                return "rtl"
            return "unknown"  # empty string = auto, detect later from hardware

        def _apply_bias_tee_args(device_args: str, enable: bool, dev: str):
            args = device_args or ""
            if not enable:
                return args

            if dev in ["rtl", "hackrf"]:
                if "bias=" not in args:
                    args = (args + "," if args else "") + "bias=1"
            elif dev == "bladerf":
                if "biastee=" not in args:
                    args = args + ",biastee=1"
            return args

        ##################################################
        # Variables
        ##################################################
        samp_rate = int(samp_rate)
        self.samp_rate = samp_rate
        lora_bw = int(lora_bw)

        # Sync word processing: MeshCore often uses 0x12 (18)
        if sync_word is None:
            sync_word = [0, 0] # default for meshtastic
        elif isinstance(sync_word, int):
            sync_word = [0x10, sync_word] # gr-lora style

        sync_word = [int(x) for x in sync_word]

        if preamble_len is None:
            preamble_length = 17 # default for meshtastic
        else:
            preamble_length = int(preamble_len)

        dc_shift = 0
        dc_iq_mode = 0
        # Pre-detect from args (may be "unknown" if args is empty = auto mode)
        detected_dev = _detect_device_from_args(device_args)
        self.sync_word = sync_word
        self.soft_decoding = soft_decoding = True
        sf = int(sf)
        preamble_length = int(preamble_length)
        ppm = int(ppm)
        self.payload_length = payload_length
        self.impl_head = impl_head
        self.has_crc = has_crc
        self.gain = gain
        self.cr_48 = cr_48 = 8
        self.cr_47 = cr_47 = 3
        self.cr_46 = cr_46 = 2
        self.cr_45 = cr_45 = 1
        self.cr_44 = cr_44 = 0
        center_freq = int(center_freq)
        self.center_freq = center_freq
        self.bandpass250k = bandpass250k = firdes.complex_band_pass(1.0, samp_rate, -lora_bw/2, lora_bw/2, lora_bw/10, window.WIN_HAMMING, 6.76)

        ##################################################
        # Blocks
        ##################################################

        # Redirect low-level stdout fd to capture osmosdr C++ output
        # osmosdr doesn't offer (or I haven't found) a way to see the devices it has detected, 
        # so we'll have to do that via what it prints to stdout when we start it.
        _stdout_fd = sys.stderr.fileno()  # <-- era sys.stdout.fileno()
        _saved_fd = os.dup(_stdout_fd)
        _pipe_r, _pipe_w = os.pipe()
        os.dup2(_pipe_w, _stdout_fd)
        os.close(_pipe_w)

        _captured_chunks = []

        def _reader():
            while True:
                try:
                    chunk = os.read(_pipe_r, 4096)
                    if not chunk:
                        break
                    _captured_chunks.append(chunk)
                except OSError:
                    break

        _reader_thread = threading.Thread(target=_reader, daemon=True)
        _reader_thread.start()

        _osmo_init_error = None
        try:
            self.rtlsdr_source_0 = osmosdr.source(
                args="numchan=" + str(1) + " " + str(device_args)
            )
        except Exception as e:
            _osmo_init_error = e
        finally:
            # Restore stderr ALWAYS, even if osmosdr threw
            os.dup2(_saved_fd, _stdout_fd)
            os.close(_saved_fd)
            os.close(_pipe_r)
            _reader_thread.join(timeout=2.0)

        _osmo_text = b"".join(_captured_chunks).decode("utf-8", errors="replace")
        # Re-print so it still shows in engine log
        sys.stderr.write(_osmo_text)  # <-- era sys.stdout.write
        sys.stderr.flush()  

        if _osmo_init_error is not None:
            raise RuntimeError(
                f"SDR device not found or driver not available: {_osmo_init_error}"
            ) from _osmo_init_error

        _osmo_lower = _osmo_text.lower()
        if detected_dev == "unknown":
            # Only look at lines that describe the actual opened device,
            # ignore the "built-in source types" line which lists all drivers
            relevant_lines = [
                l for l in _osmo_lower.splitlines()
                if not l.startswith("built-in source types")
                and not l.startswith("gr-osmosdr")
                and not l.startswith("[info]")
                and not l.startswith("[debug]")
                and not l.startswith("[warning]")
            ]
            relevant_text = "\n".join(relevant_lines)

            if "hackrf" in relevant_text:
                detected_dev = "hackrf"
            elif "bladerf" in relevant_text:
                detected_dev = "bladerf"
            elif "airspyhf" in relevant_text:
                detected_dev = "airspyhf"
            elif "airspy" in relevant_text:
                detected_dev = "airspy"
            elif "uhd" in relevant_text or "usrp" in relevant_text:
                detected_dev = "uhd"
            elif "rtl" in relevant_text or "rafael" in relevant_text or "r820" in relevant_text:
                detected_dev = "rtl"

        # Apply HackRF-specific settings now that we know the real device

        # --- HackRF-specific workarounds ---
        # HackRF is a direct-conversion (zero-IF) receiver with two quirks
        # that break LoRa decoding when the flowgraph is tuned for RTL-SDR:
        #
        # 1. Minimum sample rate: HackRF hardware ignores requests below
        #    2 Msps and silently clocks at 2 Msps regardless. GNU Radio
        #    then believes it is operating at the requested (lower) rate,
        #    so every FFT window and chip-timing calculation is wrong,
        #    causing 100 % CRC failure. Fix: enforce 2 Msps minimum.
        #
        # 2. DC spike: direct-conversion mixers produce a strong DC
        #    component at the exact tuned frequency. LoRa chirps sweep
        #    through 0 Hz on every symbol, so the spike corrupts the FFT
        #    bin at the centre of each chirp. Fix: offset-tune the hardware
        #    500 kHz above the target and compensate in the freq_xlating
        #    filter so the DC spike falls outside the LoRa passband.
        is_hackrf = detected_dev == "hackrf"
        if is_hackrf:
            samp_rate = max(samp_rate, 2_000_000)
            dc_shift = 500_000

        # Apply bias tee to args
        device_args = _apply_bias_tee_args(device_args, bias_tee, detected_dev)

        print(f"Device args: {device_args}", flush=True)
        print(f"Detected SDR: {detected_dev}", flush=True)

        driver = detected_dev if detected_dev else "unknown"
        print("Driver:", driver, flush=True)

        dev_name = None
        for attr in ("get_hardware_key", "get_device_name", "get_hw_name", "get_device_info", "get_dev_info"):
            if not hasattr(self.rtlsdr_source_0, attr):
                continue
            for call_args in ((), (0,)):
                try:
                    res = getattr(self.rtlsdr_source_0, attr)(*call_args)
                except Exception:
                    continue
                if res is None:
                    continue
                dev_name = str(res).strip() or None
                if dev_name:
                    break
            if dev_name:
                break
        if dev_name:
            print("Device:", dev_name, flush=True)
        self.rtlsdr_source_0.set_time_source('external', 0)
        self.rtlsdr_source_0.set_time_unknown_pps(osmosdr.time_spec_t())
        self.rtlsdr_source_0.set_sample_rate(samp_rate)
        self.rtlsdr_source_0.set_center_freq(center_freq + dc_shift, 0)
        self.rtlsdr_source_0.set_freq_corr(ppm, 0)
        # HackRF benefits from automatic software DC/IQ correction;
        # leave RTL-SDR at its original settings (mode 0).
        if is_hackrf:
            dc_iq_mode = 2
        self.rtlsdr_source_0.set_dc_offset_mode(dc_iq_mode, 0)
        self.rtlsdr_source_0.set_iq_balance_mode(dc_iq_mode, 0)
        self.rtlsdr_source_0.set_gain_mode(False, 0)
        self.rtlsdr_source_0.set_gain(gain, 0)
        self.rtlsdr_source_0.set_if_gain(int(if_gain), 0)
        self.rtlsdr_source_0.set_bb_gain(int(bb_gain), 0)
        self.rtlsdr_source_0.set_bandwidth(0, 0)
        # Bias-t Fallback only for unknown SDR
        if bias_tee and detected_dev == "unknown":
            try:
                self.rtlsdr_source_0.set_antenna("bias-tee", 0)
                print("Bias tee enabled via antenna fallback", flush=True)
            except Exception as e:
                print(f"Bias tee fallback failed: {e}", flush=True)

            try:
                self.rtlsdr_source_0.set_biasT(True)
                print("Bias tee enabled via API fallback", flush=True)
            except Exception:
                pass
        self.pdu_tagged_stream_to_pdu_0 = pdu.tagged_stream_to_pdu(gr.types.byte_t, 'packet_len')
        self.lora_sdr_header_decoder_0_1 = lora_sdr.header_decoder(impl_head, cr_45, payload_length, has_crc, 2, False)
        self.lora_sdr_hamming_dec_0_1 = lora_sdr.hamming_dec(soft_decoding)
        self.lora_sdr_gray_mapping_0_1 = lora_sdr.gray_mapping( soft_decoding)
        self.lora_sdr_frame_sync_0_1 = lora_sdr.frame_sync(center_freq, lora_bw, sf, impl_head, sync_word, 4,preamble_length)
        self.lora_sdr_fft_demod_0_1 = lora_sdr.fft_demod( soft_decoding, True)
        self.lora_sdr_dewhitening_0_1 = lora_sdr.dewhitening()
        self.lora_sdr_deinterleaver_0_1 = lora_sdr.deinterleaver( soft_decoding)
        self.lora_sdr_crc_verif_0_1 = lora_sdr.crc_verif( 2, False)
        self.freq_xlating_fir_filter_xxx_0 = filter.freq_xlating_fir_filter_ccc((max(1, int(samp_rate/(lora_bw * 4)))), bandpass250k, -dc_shift, samp_rate)
        self.freq_xlating_fir_filter_xxx_0.set_min_output_buffer(17000)
        self.epy_block_2 = epy_block_2.blk()
        self.epy_block_1 = epy_block_1.blk()
        self.aggregator = self.epy_block_1  # expose for engine
        self.epy_block_0 = epy_block_0.blk()
        self.blocks_moving_average_xx_0 = blocks.moving_average_ff(1024, (1.0 / 1024), 4000, 1)
        self.blocks_complex_to_mag_squared_0 = blocks.complex_to_mag_squared(1)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.epy_block_0, 'out'), (self.epy_block_1, 'metrics'))
        self.msg_connect((self.lora_sdr_crc_verif_0_1, 'msg'), (self.epy_block_0, 'payload_done'))
        self.msg_connect((self.lora_sdr_header_decoder_0_1, 'frame_info'), (self.epy_block_0, 'frame_info'))
        self.msg_connect((self.lora_sdr_header_decoder_0_1, 'frame_info'), (self.lora_sdr_frame_sync_0_1, 'frame_info'))
        self.msg_connect((self.pdu_tagged_stream_to_pdu_0, 'pdus'), (self.epy_block_1, 'payload'))
        self.connect((self.blocks_complex_to_mag_squared_0, 0), (self.blocks_moving_average_xx_0, 0))
        self.connect((self.blocks_moving_average_xx_0, 0), (self.epy_block_0, 0))
        self.connect((self.epy_block_2, 0), (self.pdu_tagged_stream_to_pdu_0, 0))
        self.connect((self.freq_xlating_fir_filter_xxx_0, 0), (self.blocks_complex_to_mag_squared_0, 0))
        self.connect((self.freq_xlating_fir_filter_xxx_0, 0), (self.lora_sdr_frame_sync_0_1, 0))
        self.connect((self.lora_sdr_crc_verif_0_1, 0), (self.epy_block_2, 0))
        self.connect((self.lora_sdr_deinterleaver_0_1, 0), (self.lora_sdr_hamming_dec_0_1, 0))
        self.connect((self.lora_sdr_dewhitening_0_1, 0), (self.lora_sdr_crc_verif_0_1, 0))
        self.connect((self.lora_sdr_fft_demod_0_1, 0), (self.lora_sdr_gray_mapping_0_1, 0))
        self.connect((self.lora_sdr_frame_sync_0_1, 0), (self.lora_sdr_fft_demod_0_1, 0))
        self.connect((self.lora_sdr_gray_mapping_0_1, 0), (self.lora_sdr_deinterleaver_0_1, 0))
        self.connect((self.lora_sdr_hamming_dec_0_1, 0), (self.lora_sdr_header_decoder_0_1, 0))
        self.connect((self.lora_sdr_header_decoder_0_1, 0), (self.lora_sdr_dewhitening_0_1, 0))
        self.connect((self.rtlsdr_source_0, 0), (self.freq_xlating_fir_filter_xxx_0, 0))
        ##################################################
        # Extra demodulator chains (multi-preset ALL mode)
        ##################################################
        self._extra_chains = []
        for cfg in (extra_demod_configs or []):
            self._add_demod_chain(cfg)

    def _add_demod_chain(self, cfg: dict):
        """Add a secondary LoRa demodulator chain sharing the same SDR source."""
        import gnuradio.lora_sdr as lora_sdr_extra
        from gnuradio import pdu as pdu_extra

        chain_sf    = int(cfg['sf'])
        chain_bw    = int(cfg['bw'])
        chain_freq  = int(cfg['center_freq'])
        chain_preset_id = int(cfg.get('preset_id', 0))
        chain_sync_word = cfg.get('sync_word')
        if chain_sync_word is None:
            chain_sync_word = self.sync_word
        elif isinstance(chain_sync_word, int):
            chain_sync_word = [0x10, chain_sync_word]

        chain_preamble_len = cfg.get('preamble_len')
        if chain_preamble_len is None:
            chain_preamble_len = self.preamble_length

        # BW-specific bandpass filter taps
        chain_taps = firdes.complex_band_pass(
            1.0, self.samp_rate, -chain_bw / 2, chain_bw / 2,
            chain_bw / 10, window.WIN_HAMMING, 6.76
        )
        decimation = max(1, int(self.samp_rate / (chain_bw * 4)))

        freq_offset = chain_freq - self.center_freq
        xlat = filter.freq_xlating_fir_filter_ccc(decimation, chain_taps, chain_freq - self.center_freq, self.samp_rate)
        xlat.set_min_output_buffer(17000)

        frame_sync   = lora_sdr_extra.frame_sync(chain_freq, chain_bw, chain_sf, self.impl_head, list(chain_sync_word), 4, chain_preamble_len)
        fft_demod    = lora_sdr_extra.fft_demod(self.soft_decoding, True)
        gray_map     = lora_sdr_extra.gray_mapping(self.soft_decoding)
        deinterleave = lora_sdr_extra.deinterleaver(self.soft_decoding)
        hamming      = lora_sdr_extra.hamming_dec(self.soft_decoding)
        hdr_dec      = lora_sdr_extra.header_decoder(self.impl_head, self.cr_45, self.payload_length, self.has_crc, 2, False)
        dewhiten     = lora_sdr_extra.dewhitening()
        crc          = lora_sdr_extra.crc_verif(2, False)

        from . import rx_lora_base_engine_epy_block_2 as epy2_extra
        tag_adder = epy2_extra.blk()

        tagged_to_pdu = pdu_extra.tagged_stream_to_pdu(gr.types.byte_t, 'packet_len')

        from . import rx_lora_base_engine_epy_block_1 as epy1_extra
        chain_aggregator = epy1_extra.blk()
        chain_aggregator.preset_id = chain_preset_id

        # Signal metrics (epy_block_0)
        from . import rx_lora_base_engine_epy_block_0 as epy0_extra
        chain_metrics = epy0_extra.blk()

        c2mag = blocks.complex_to_mag_squared(1)
        mov_avg = blocks.moving_average_ff(1024, 1.0 / 1024, 4000, 1)

        # Connections
        self.connect((self.rtlsdr_source_0, 0), (xlat, 0))
        self.connect((xlat, 0), (frame_sync, 0))
        self.connect((xlat, 0), (c2mag, 0))
        self.connect((c2mag, 0), (mov_avg, 0))
        self.connect((mov_avg, 0), (chain_metrics, 0))
        self.connect((frame_sync, 0), (fft_demod, 0))
        self.connect((fft_demod, 0), (gray_map, 0))
        self.connect((gray_map, 0), (deinterleave, 0))
        self.connect((deinterleave, 0), (hamming, 0))
        self.connect((hamming, 0), (hdr_dec, 0))
        self.connect((hdr_dec, 0), (dewhiten, 0))
        self.connect((dewhiten, 0), (crc, 0))
        self.connect((crc, 0), (tag_adder, 0))
        self.connect((tag_adder, 0), (tagged_to_pdu, 0))

        self.msg_connect((crc, 'msg'),            (chain_metrics, 'payload_done'))
        self.msg_connect((hdr_dec, 'frame_info'), (chain_metrics, 'frame_info'))
        self.msg_connect((hdr_dec, 'frame_info'), (frame_sync,    'frame_info'))
        self.msg_connect((chain_metrics, 'out'),  (chain_aggregator, 'metrics'))
        self.msg_connect((tagged_to_pdu, 'pdus'), (chain_aggregator, 'payload'))

        # Output goes to the same aggregator as main chain
        chain_id = len(self._extra_chains)
        setattr(self, f'_chain_{chain_id}_blocks', [
            xlat, frame_sync, fft_demod, gray_map, deinterleave,
            hamming, hdr_dec, dewhiten, crc, tag_adder,
            tagged_to_pdu, chain_aggregator, chain_metrics, c2mag, mov_avg
        ])
        self._extra_chains.append({
            'aggregator': chain_aggregator,
            'xlat': xlat,
        })


    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_bandpass250k(firdes.complex_band_pass(1.0, self.samp_rate, -self.lora_bw/2, self.lora_bw/2, self.lora_bw/10, window.WIN_HAMMING, 6.76))
        self.rtlsdr_source_0.set_sample_rate(self.samp_rate)

    def get_lora_bw(self):
        return self.lora_bw

    def set_lora_bw(self, lora_bw):
        self.lora_bw = lora_bw
        self.set_bandpass250k(firdes.complex_band_pass(1.0, self.samp_rate, -self.lora_bw/2, self.lora_bw/2, self.lora_bw/10, window.WIN_HAMMING, 6.76))

    def get_sync_word(self):
        return self.sync_word

    def set_sync_word(self, sync_word):
        self.sync_word = sync_word

    def get_soft_decoding(self):
        return self.soft_decoding

    def set_soft_decoding(self, soft_decoding):
        self.soft_decoding = soft_decoding

    def get_sf(self):
        return self.sf

    def set_sf(self, sf):
        self.sf = sf

    def get_preamble_length(self):
        return self.preamble_length

    def set_preamble_length(self, preamble_length):
        self.preamble_length = preamble_length

    def get_ppm(self):
        return self.ppm

    def set_ppm(self, ppm):
        self.ppm = ppm
        self.rtlsdr_source_0.set_freq_corr(self.ppm, 0)

    def get_payload_length(self):
        return self.payload_length

    def set_payload_length(self, payload_length):
        self.payload_length = payload_length

    def get_impl_head(self):
        return self.impl_head

    def set_impl_head(self, impl_head):
        self.impl_head = impl_head

    def get_has_crc(self):
        return self.has_crc

    def set_has_crc(self, has_crc):
        self.has_crc = has_crc

    def get_gain(self):
        return self.gain

    def set_gain(self, gain):
        self.gain = gain
        self.rtlsdr_source_0.set_gain(self.gain, 0)

    def get_cr_48(self):
        return self.cr_48

    def set_cr_48(self, cr_48):
        self.cr_48 = cr_48

    def get_cr_47(self):
        return self.cr_47

    def set_cr_47(self, cr_47):
        self.cr_47 = cr_47

    def get_cr_46(self):
        return self.cr_46

    def set_cr_46(self, cr_46):
        self.cr_46 = cr_46

    def get_cr_45(self):
        return self.cr_45

    def set_cr_45(self, cr_45):
        self.cr_45 = cr_45

    def get_cr_44(self):
        return self.cr_44

    def set_cr_44(self, cr_44):
        self.cr_44 = cr_44

    def get_center_freq(self):
        return self.center_freq

    def set_center_freq(self, center_freq):
        self.center_freq = center_freq
        self.rtlsdr_source_0.set_center_freq(self.center_freq, 0)

    def get_bandpass250k(self):
        return self.bandpass250k

    def set_bandpass250k(self, bandpass250k):
        self.bandpass250k = bandpass250k
        self.freq_xlating_fir_filter_xxx_0.set_taps(self.bandpass250k)




def build_top_block(**kwargs):
    """Factory for the engine top block."""
    return rx_lora_base_engine(**kwargs)


def main(top_block_cls=rx_lora_base_engine, options=None):
    tb = top_block_cls()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    tb.start()
    tb.flowgraph_started.set()

    try:
        input('Press Enter to quit: ')
    except EOFError:
        pass
    tb.stop()
    tb.wait()


if __name__ == '__main__':
    main()
