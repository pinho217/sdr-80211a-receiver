#!/usr/bin/env python3
"""Summarise a CSV written by packet_quality_monitor v2 (numpy + csv only).

  python3 scripts/summarize_metrics.py scripts/metricas_Sample3_20MHz_Channel100.csv

Prints: PDUs per association quality, EVM / LS-SNR per rate (trustworthy rows only: assoc exact|tail),
frame types, transmitters, and a beacon coverage estimate from the 802.11 sequence counter.
"""
import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    a = ap.parse_args()
    rows = list(csv.DictReader(a.csv.open(encoding="utf-8")))
    print(f"{a.csv}: {len(rows)} PDUs with a valid CRC")
    print("association:", dict(Counter(r["assoc"] for r in rows)))
    good = [r for r in rows if r["assoc"] in ("exact", "tail")]

    print("\nper rate (assoc exact|tail):  n   EVM median   SNR(EVM) median   SNR(LS) median")
    by_rate = defaultdict(list)
    for r in good:
        by_rate[(int(r["rate_mbps"]), r["modulation"])].append(r)
    for (mbps, mod), g in sorted(by_rate.items()):
        evm = np.nanmedian([num(r["evm_rms"]) for r in g])
        s_evm = np.nanmedian([num(r["snr_evm_db"]) for r in g])
        s_ls = np.nanmedian([num(r["snr_ls_db"]) for r in g])
        print(f"  {mbps:>2} Mb/s {mod:<6} {len(g):>5}   {evm:>9.3f}   {s_evm:>14.1f} dB   {s_ls:>12.1f} dB")

    print("\nframe types:", dict(Counter(r["frame_type"] for r in rows).most_common(10)))
    tx = Counter(r["transmitter"] for r in rows if r["transmitter"])
    print("transmitters:", dict(tx.most_common(8)))
    bad = [r["transmitter"] for r in rows if "group bit" in r["note"]]
    if bad:
        print(f"suspect transmitter addresses (group bit set): {len(bad)} rows, e.g. {bad[0]}")

    print("\nbeacon coverage (distinct seq / seq span; assumes one counter step per frame sent):")
    seqs = defaultdict(set)
    for r in rows:
        if r["frame_type"] == "mgmt:beacon" and r["sequence"] not in ("", "-1"):
            seqs[(r["transmitter"], r["ssid"])].add(int(r["sequence"]))
    for key, s in sorted(seqs.items()):
        v = np.array(sorted(s))
        keep = v[np.abs(v - np.median(v)) <= 1000] if len(v) > 2 else v     # drop mis-parsed outliers
        span = keep.max() - keep.min() + 1
        print(f"  {key[0]} {key[1] or '(hidden)':<12} beacons {len(keep):>3} / span {span:>4} = {len(keep) / span:.0%}"
              + (f"  [{len(v) - len(keep)} outlier(s) dropped]" if len(keep) != len(v) else ""))


if __name__ == "__main__":
    main()
