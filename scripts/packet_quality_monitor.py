#!/usr/bin/env python3
"""Per-frame quality monitor for the gr-ieee802-11 receiver (v2).

Why a v2?  The first version joined the `decoded` PDUs with the `symbols` messages purely
by arrival order and always sliced the data with a BPSK/QPSK decision.  Two problems:

* frame_equalizer publishes `symbols` for EVERY frame whose SIGNAL field passes the parity
  test, also for false triggers (narrow-band interference gives ~1/8 of random SIGNAL fields
  a valid parity/rate) and for frames whose CRC later fails.  The buffer was only cleared on a
  CRC-valid PDU, so one row could hold the symbols of hundreds of frames
  (symbol_count up to 448 944 = 9 353 OFDM symbols).
* 16-QAM / 64-QAM frames were measured against QPSK points: a clean 16-QAM frame gives
  EVM = 0.460 (6.75 dB) and 64-QAM 0.504 (5.95 dB) no matter how good the channel is.

v2 uses the metadata that decode_mac already attaches to every PDU (tags created by
frame_equalizer.decode_signal_field):  "encoding", "frame bytes", "snr" (LS estimate from
the two LTS copies, dB) and "frequency offset" (total CFO in Hz = tag * bw / 2pi).
With encoding and PSDU length the exact number of constellation points of the frame is known:

    n_sym = ceil((16 + 8*psdu_bytes + 6) / N_DBPS[encoding])      points = 48 * n_sym

and the monitor keeps only the LAST `points` buffered values (symbols of the frame that just
finished decoding), reporting how many older points were discarded (`points_dropped`).
The EVM is computed against the real constellation of the frame.

Columns:  see CSV_FIELDS.   assoc = exact | tail | short | nometa
    exact : buffer held exactly the frame's points          -> EVM trustworthy
    tail  : older points (failed/false frames) were dropped -> EVM probably fine
    short : fewer points than expected                      -> EVM left empty
    nometa: PDU without encoding/frame-bytes metadata       -> EVM left empty

Run offline with  scripts/wifi_rx_headless.py ; the block keeps the old constructor
QualityMonitor(csv_path) so the GRC-generated script still works.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import math
import struct
import time
from pathlib import Path

import numpy as np

try:  # GNU Radio is only needed for the block, not for the maths / self-test
    import pmt
    from gnuradio import gr
except ImportError:  # pragma: no cover
    pmt = None
    gr = None

CSV_FIELDS = (
    "pdu_index", "timestamp", "encoding", "rate_mbps", "modulation", "psdu_bytes", "n_sym",
    "points_used", "points_dropped", "assoc", "evm_rms", "snr_evm_db", "snr_ls_db", "cfo_khz",
    "frame_type", "receiver", "transmitter", "sequence", "retry", "ssid", "note",
)

# encoding -> (label, constellation, N_DBPS, Mbit/s)   (lib/utils.cc, mapper.h)
RATES = {
    0: ("BPSK 1/2", "BPSK", 24, 6),
    1: ("BPSK 3/4", "BPSK", 36, 9),
    2: ("QPSK 1/2", "QPSK", 48, 12),
    3: ("QPSK 3/4", "QPSK", 72, 18),
    4: ("16-QAM 1/2", "16QAM", 96, 24),
    5: ("16-QAM 3/4", "16QAM", 144, 36),
    6: ("64-QAM 2/3", "64QAM", 192, 48),
    7: ("64-QAM 3/4", "64QAM", 216, 54),
}


def ideal_points(constellation: str) -> np.ndarray:
    """Unit-average-power constellation (802.11-2012, 18.3.5.8)."""
    if constellation == "BPSK":
        return np.array([1, -1], dtype=np.complex128)
    side = {"QPSK": 2, "16QAM": 4, "64QAM": 8}[constellation]
    levels = np.arange(-(side - 1), side, 2, dtype=float)
    grid = (levels[:, None] + 1j * levels[None, :]).ravel()
    return grid / math.sqrt(np.mean(np.abs(grid) ** 2))


def n_symbols(psdu_bytes: int, encoding: int) -> int:
    return math.ceil((16 + 8 * psdu_bytes + 6) / RATES[encoding][2])


def evm_rms(points: np.ndarray, constellation: str) -> float:
    """RMS error to the nearest ideal point, relative to unit average power."""
    ideal = ideal_points(constellation)
    points = points[np.isfinite(points)]
    if points.size == 0:
        return math.nan
    error = np.min(np.abs(points[:, None] - ideal[None, :]), axis=1)
    return float(np.sqrt(np.mean(error ** 2)))


# ---------------------------------------------------------------- MAC header parsing
_MGMT = {0: "assoc-req", 1: "assoc-resp", 2: "reassoc-req", 3: "reassoc-resp", 4: "probe-req",
         5: "probe-resp", 8: "beacon", 9: "atim", 10: "disassoc", 11: "auth", 12: "deauth",
         13: "action"}
_CTRL = {7: "ctrl-wrapper", 8: "block-ack-req", 9: "block-ack", 10: "ps-poll", 11: "rts",
         12: "cts", 13: "ack", 14: "cf-end", 15: "cf-end+ack"}
_CTRL_WITH_TA = {7, 8, 9, 10, 11, 14, 15}      # cts and ack carry only the receiver address


def _mac(frame: bytes, offset: int) -> str:
    if len(frame) < offset + 6:
        return ""
    return ":".join(f"{b:02x}" for b in frame[offset:offset + 6])


def parse_mac_header(frame: bytes) -> dict:
    """Frame type, addresses, sequence number and SSID from a PDU WITHOUT the FCS."""
    info = dict(frame_type="short", receiver="", transmitter="", sequence=-1, retry="", ssid="")
    if len(frame) < 2:
        return info
    ftype, subtype = (frame[0] >> 2) & 3, frame[0] >> 4
    if ftype == 0:
        info["frame_type"] = "mgmt:" + _MGMT.get(subtype, str(subtype))
    elif ftype == 1:
        info["frame_type"] = "ctrl:" + _CTRL.get(subtype, str(subtype))
    elif ftype == 2:
        info["frame_type"] = f"data:{subtype}"
    else:
        info["frame_type"] = "reserved"
    info["retry"] = int(bool(frame[1] & 0x08))
    info["receiver"] = _mac(frame, 4)
    has_ta = (ftype != 1) or (subtype in _CTRL_WITH_TA)
    if has_ta:
        info["transmitter"] = _mac(frame, 10)
    if ftype in (0, 2) and len(frame) >= 24:                 # control frames have no sequence field
        info["sequence"] = (struct.unpack_from("<H", frame, 22)[0] >> 4) & 0xFFF
    if ftype == 0 and subtype in (5, 8) and len(frame) >= 36:
        info["ssid"] = _ssid(frame, 36)
    elif ftype == 0 and subtype == 4 and len(frame) >= 24:
        info["ssid"] = _ssid(frame, 24)
    return info


def _ssid(frame: bytes, position: int) -> str:
    while position + 2 <= len(frame):
        element_id, length = frame[position], frame[position + 1]
        position += 2
        if element_id == 0:
            return frame[position:position + length].decode("utf-8", errors="replace")
        position += length
    return ""


# ---------------------------------------------------------------- core (no GNU Radio)
class QualityCore:
    """Pure-Python join of `symbols` and `decoded` messages."""

    MAX_BUFFER_POINTS = 1600 * 48           # longest possible frame is ~1366 symbols

    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        header = ",".join(CSV_FIELDS)
        if self.csv_path.exists() and self.csv_path.stat().st_size:
            first = self.csv_path.open(encoding="utf-8").readline().strip()
            if first != header:
                raise RuntimeError(
                    f"{self.csv_path} has a different (older) header; choose a new --csv name "
                    "or move the old file away")
            new_file = False
        else:
            new_file = True
        self._file = self.csv_path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_FIELDS)
        if new_file:
            self._writer.writeheader()
            self._file.flush()
        self._buffer: list[np.ndarray] = []
        self._buffered = 0
        self.pdu_count = 0

    def close(self) -> None:
        self._file.close()

    def add_symbols(self, points: np.ndarray) -> None:
        self._buffer.append(np.asarray(points, dtype=np.complex128))
        self._buffered += len(points)
        if self._buffered > 2 * self.MAX_BUFFER_POINTS:      # bound the memory
            joined = np.concatenate(self._buffer)[-self.MAX_BUFFER_POINTS:]
            self._buffer, self._buffered = [joined], len(joined)

    def handle_pdu(self, meta: dict, payload: bytes, now: float | None = None) -> dict:
        encoding = meta.get("encoding")
        psdu = meta.get("frame bytes")
        row = {f: "" for f in CSV_FIELDS}
        row.update(pdu_index=self.pdu_count, timestamp=f"{(now if now is not None else time.time()):.6f}",
                   **parse_mac_header(payload))
        notes = []
        row["snr_ls_db"] = _fmt(meta.get("snr"), 3)
        if meta.get("frequency offset") is not None:
            row["cfo_khz"] = _fmt(meta["frequency offset"] / 1e3, 2)
        if encoding in RATES and psdu:
            label, constellation, _, mbps = RATES[encoding]
            expected = n_symbols(int(psdu), int(encoding))
            need = 48 * expected
            have = self._buffered
            row.update(encoding=encoding, rate_mbps=mbps, modulation=constellation,
                       psdu_bytes=int(psdu), n_sym=expected, points_used=0, points_dropped=have)
            if have >= need:
                points = np.concatenate(self._buffer)[-need:] if self._buffer else np.empty(0)
                evm = evm_rms(points, constellation)
                row.update(points_used=need, points_dropped=have - need,
                           assoc="exact" if have == need else "tail",
                           evm_rms=_fmt(evm, 6), snr_evm_db=_fmt(-20 * math.log10(max(evm, 1e-12)), 3))
            else:
                row["assoc"] = "short"
        else:
            row["assoc"] = "nometa"
            notes.append("no encoding/frame-bytes metadata")
        ta = row["transmitter"]
        if ta and (int(ta[:2], 16) & 1):
            notes.append("transmitter has group bit set: not a valid unicast source")
        row["note"] = "; ".join(notes)
        self._buffer, self._buffered = [], 0            # whatever remains belongs to failed frames
        self.pdu_count += 1
        self._writer.writerow(row)
        self._file.flush()
        return row


def _fmt(value, digits: int) -> str:
    if value is None or not np.isfinite(value):
        return ""
    return f"{value:.{digits}f}"


# ---------------------------------------------------------------- GNU Radio block
if gr is not None:
    def _blob_bytes(value) -> bytes:
        if pmt.is_blob(value):
            capsule = pmt.blob_data(value)
            get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
            get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
            get_pointer.restype = ctypes.c_void_p
            return ctypes.string_at(get_pointer(capsule, None), pmt.blob_length(value))
        if pmt.is_u8vector(value):
            return bytes(pmt.u8vector_elements(value))
        return b""

    def _number(value):
        if pmt.is_uint64(value):
            return pmt.to_uint64(value)
        if pmt.is_integer(value):
            return pmt.to_long(value)
        if pmt.is_real(value):
            return pmt.to_double(value)
        return None

    class QualityMonitor(gr.basic_block):
        """Message sink: ports `decoded` (decode_mac.out) and `symbols` (frame_equalizer.symbols)."""

        def __init__(self, csv_path: str | Path = "scripts/metricas_qualidade_v2.csv"):
            gr.basic_block.__init__(self, name="wifi_packet_quality_monitor", in_sig=None, out_sig=None)
            self.message_port_register_in(pmt.intern("decoded"))
            self.message_port_register_in(pmt.intern("symbols"))
            self.set_msg_handler(pmt.intern("decoded"), self._handle_decoded)
            self.set_msg_handler(pmt.intern("symbols"), self._handle_symbols)
            self.core = QualityCore(csv_path)

        def stop(self) -> bool:
            self.core.close()
            return super().stop()

        def _handle_symbols(self, message) -> None:
            if pmt.is_pair(message) and pmt.is_c32vector(pmt.cdr(message)):
                self.core.add_symbols(np.asarray(pmt.c32vector_elements(pmt.cdr(message))))

        def _handle_decoded(self, message) -> None:
            if not pmt.is_pair(message):
                return
            meta_pmt, payload = pmt.car(message), _blob_bytes(pmt.cdr(message))
            meta = {}
            for key in ("encoding", "frame bytes", "snr", "frequency offset"):
                value = _number(pmt.dict_ref(meta_pmt, pmt.intern(key), pmt.PMT_NIL))
                if value is not None:
                    meta[key] = value
            row = self.core.handle_pdu(meta, payload)
            print(f"PDU {row['pdu_index']}: {row['frame_type']} {row['rate_mbps']} Mb/s "
                  f"EVM={row['evm_rms'] or '-'} SNR_LS={row['snr_ls_db'] or '-'} dB "
                  f"assoc={row['assoc']} dropped={row['points_dropped']}", flush=True)
else:  # pragma: no cover
    QualityMonitor = None


# ---------------------------------------------------------------- self-test
def _self_test(path: Path) -> None:
    rng = np.random.default_rng(1)

    def frame_points(constellation: str, n_sym: int, snr_db: float) -> np.ndarray:
        ideal = ideal_points(constellation)
        points = rng.choice(ideal, 48 * n_sym)
        sigma = 10 ** (-snr_db / 20) / math.sqrt(2)
        return points + sigma * (rng.normal(size=points.size) + 1j * rng.normal(size=points.size))

    core = QualityCore(path)
    # 1) ACK at 24 Mb/s (16-QAM 1/2, 14-byte PSDU, 2 symbols), preceded by 5 000 points of failed frames.
    core.add_symbols(rng.normal(size=5000) + 1j * rng.normal(size=5000))
    core.add_symbols(frame_points("16QAM", 2, 20.0))
    ack = bytes([0xD4, 0, 0, 0]) + bytes.fromhex("e27e5d2ca6da")
    row = core.handle_pdu(dict(encoding=4, **{"frame bytes": 14, "snr": 20.1, "frequency offset": 8520.0}), ack)
    assert row["n_sym"] == 2 and row["points_used"] == 96 and row["assoc"] == "tail", row
    assert row["frame_type"] == "ctrl:ack" and row["transmitter"] == "" and row["sequence"] == -1
    assert abs(float(row["snr_evm_db"]) - 20.0) < 1.5, row
    # 2) QPSK data frame, exact association.
    core.add_symbols(frame_points("QPSK", 10, 15.0))
    beacon = bytearray(36)
    beacon[0] = 0x80
    beacon[4:10] = b"\xff" * 6
    beacon[10:16] = bytes.fromhex("548aba179e0c")
    struct.pack_into("<H", beacon, 22, 2404 << 4)
    beacon += bytes([0, 11]) + b"raspberrypi"
    psdu = len(beacon) + 4
    enc = 2
    nsym = n_symbols(psdu, enc)
    core._buffer, core._buffered = [], 0
    core.add_symbols(frame_points("QPSK", nsym, 15.0))
    row = core.handle_pdu(dict(encoding=enc, **{"frame bytes": psdu, "snr": 15.2}), bytes(beacon))
    assert row["assoc"] == "exact" and row["ssid"] == "raspberrypi" and row["sequence"] == 2404, row
    # 3) buffer too short -> no EVM.
    core.add_symbols(frame_points("QPSK", 1, 15.0))
    row = core.handle_pdu(dict(encoding=2, **{"frame bytes": 100}), bytes(40))
    assert row["assoc"] == "short" and row["evm_rms"] == "", row
    # 4) the old measurement error: 16-QAM measured with a QPSK slicer.
    pts = frame_points("16QAM", 200, 60.0)
    assert abs(evm_rms(pts, "QPSK") - 0.46) < 0.01 and abs(evm_rms(pts, "16QAM")) < 0.01
    core.close()
    print("self-test OK ->", path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--csv", type=Path, default=Path("scripts/metricas_selftest.csv"))
    args = parser.parse_args()
    if args.self_test:
        if args.csv.exists():
            args.csv.unlink()
        _self_test(args.csv)
