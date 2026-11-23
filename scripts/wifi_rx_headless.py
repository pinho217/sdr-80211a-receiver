#!/usr/bin/env python3
"""Headless offline IEEE 802.11a/g/p receiver (same flow graph as wifi_rx_offline.py / wifi_phy_hier).

  python3 scripts/wifi_rx_headless.py recordings/Sample1_20MHz_Channel36.bin
  python3 scripts/wifi_rx_headless.py frames_1248k.bin --samp-rate 20e6 --channel 36 --pad 50000

* samp_rate and channel are read from the file name (Sample<k>_<bw>MHz_Channel<n>.bin) unless given.
  They are NOT cosmetic: frame_equalizer scales the sampling-offset compensation and the reported
  CFO with bandwidth/carrier (a 10 MHz / 5.89 GHz setting scales them by 0.44).
* Outputs: <csv> (packet_quality_monitor v2) and <pcap> (open with Wireshark).
* --start/--count select a slice of the recording in samples; --pad appends zeros so that a frame
  at the very end of a short excerpt is flushed through the blocks.
Topology = the GRC-generated one (Fig. 1 of the paper): 8 autocorrelation blocks -> sync_short ->
sync_long -> stream_to_vector(64) -> FFT -> frame_equalizer -> decode_mac -> parse_mac/wireshark/monitor.
"""
import argparse
import re
import sys
from pathlib import Path


def channel_to_freq(channel: int) -> float:
    if channel == 14:
        return 2484e6
    if 1 <= channel <= 13:
        return (2407 + 5 * channel) * 1e6
    return (5000 + 5 * channel) * 1e6                    # 5 GHz band: 36 -> 5.18 GHz, 100 -> 5.5 GHz


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", type=Path)
    ap.add_argument("--samp-rate", type=float)
    ap.add_argument("--channel", type=int)
    ap.add_argument("--freq", type=float, help="carrier in Hz (overrides --channel)")
    ap.add_argument("--chan-est", type=int, default=0, choices=[0, 1, 2, 3], help="0 LS, 1 LMS, 2 comb, 3 STA")
    ap.add_argument("--threshold", type=float, default=0.56)
    ap.add_argument("--min-plateau", type=int, default=2)
    ap.add_argument("--window-size", type=int, default=48)
    ap.add_argument("--sync-length", type=int, default=320)
    ap.add_argument("--start", type=int, default=0, help="first sample")
    ap.add_argument("--count", type=int, default=0, help="number of samples (0 = to the end)")
    ap.add_argument("--pad", type=int, default=0, help="append this many zero samples")
    ap.add_argument("--csv", type=Path)
    ap.add_argument("--pcap", type=Path)
    ap.add_argument("--debug", action="store_true", help="verbose block logs (only for short excerpts!)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    name = a.recording.name
    m_bw = re.search(r"(\d+)MHz", name, re.I)
    m_ch = re.search(r"Channel(\d+)", name, re.I)
    samp_rate = a.samp_rate or (float(m_bw.group(1)) * 1e6 if m_bw else 20e6)
    freq = a.freq or channel_to_freq(a.channel or (int(m_ch.group(1)) if m_ch else 36))
    stem = a.recording.stem
    csv_path = a.csv or Path("scripts") / f"metricas_{stem}.csv"
    pcap_path = a.pcap or Path(f"{stem}.pcap")
    total = a.recording.stat().st_size // 8
    count = a.count or (total - a.start)
    print(f"recording {a.recording} ({total:,} samples) slice [{a.start:,}, {a.start + count:,})\n"
          f"samp_rate={samp_rate:g} Hz  freq={freq:g} Hz  chan_est={a.chan_est}  threshold={a.threshold}  "
          f"min_plateau={a.min_plateau}  window={a.window_size}  sync_length={a.sync_length}\n"
          f"csv={csv_path}  pcap={pcap_path}", flush=True)
    if a.dry_run:
        return
    if csv_path.exists():
        sys.exit(f"{csv_path} already exists; remove it or pass --csv (the monitor appends)")

    from gnuradio import blocks, fft, gr
    from gnuradio.fft import window
    import foo
    import ieee802_11
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from packet_quality_monitor import QualityMonitor

    tb = gr.top_block("wifi_rx_headless")
    src = blocks.file_source(gr.sizeof_gr_complex, str(a.recording), False, a.start, count)
    if a.pad:
        mux = blocks.stream_mux(gr.sizeof_gr_complex, [count, a.pad])
        zeros = blocks.vector_source_c([0j] * a.pad, False)
        tb.connect((src, 0), (mux, 0))
        tb.connect((zeros, 0), (mux, 1))
        src = mux

    w = a.window_size
    mag_sq = blocks.complex_to_mag_squared(1)
    avg_power = blocks.moving_average_ff(w + 16, 1, 4000, 1)          # power window = N + 16 (!)
    delay16 = blocks.delay(gr.sizeof_gr_complex, 16)
    conj = blocks.conjugate_cc()
    mult = blocks.multiply_vcc(1)
    avg_corr = blocks.moving_average_cc(w, 1, 4000, 1)                # correlation window = N
    mag = blocks.complex_to_mag(1)
    div = blocks.divide_ff(1)
    sync_short = ieee802_11.sync_short(a.threshold, a.min_plateau, a.debug, a.debug)
    delay_sync = blocks.delay(gr.sizeof_gr_complex, a.sync_length)     # must equal SYNC_LENGTH
    sync_long = ieee802_11.sync_long(a.sync_length, a.debug, a.debug)
    s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, 64)
    fft_b = fft.fft_vcc(64, True, window.rectangular(64), True, 1)
    eq = ieee802_11.frame_equalizer(ieee802_11.Equalizer(a.chan_est), freq, samp_rate, a.debug, a.debug)
    dec = ieee802_11.decode_mac(True, a.debug)
    parse = ieee802_11.parse_mac(False, True)
    wsk = foo.wireshark_connector(127, False)
    pcap = blocks.file_sink(gr.sizeof_char, str(pcap_path), False)
    pcap.set_unbuffered(True)
    mon = QualityMonitor(str(csv_path))

    tb.connect((src, 0), (mag_sq, 0))
    tb.connect((src, 0), (delay16, 0))
    tb.connect((src, 0), (mult, 0))
    tb.connect((mag_sq, 0), (avg_power, 0))
    tb.connect((delay16, 0), (conj, 0))
    tb.connect((delay16, 0), (sync_short, 0))
    tb.connect((conj, 0), (mult, 1))
    tb.connect((mult, 0), (avg_corr, 0))
    tb.connect((avg_corr, 0), (mag, 0))
    tb.connect((avg_corr, 0), (sync_short, 1))
    tb.connect((mag, 0), (div, 0))
    tb.connect((avg_power, 0), (div, 1))
    tb.connect((div, 0), (sync_short, 2))
    tb.connect((sync_short, 0), (delay_sync, 0))
    tb.connect((sync_short, 0), (sync_long, 0))
    tb.connect((delay_sync, 0), (sync_long, 1))
    tb.connect((sync_long, 0), (s2v, 0))
    tb.connect((s2v, 0), (fft_b, 0))
    tb.connect((fft_b, 0), (eq, 0))
    tb.connect((eq, 0), (dec, 0))
    tb.connect((wsk, 0), (pcap, 0))
    tb.msg_connect((dec, "out"), (parse, "in"))
    tb.msg_connect((dec, "out"), (wsk, "in"))
    tb.msg_connect((dec, "out"), (mon, "decoded"))
    tb.msg_connect((eq, "symbols"), (mon, "symbols"))

    tb.run()
    print(f"done: {mon.core.pdu_count} PDUs with a valid CRC -> {csv_path}, {pcap_path}")


if __name__ == "__main__":
    main()
