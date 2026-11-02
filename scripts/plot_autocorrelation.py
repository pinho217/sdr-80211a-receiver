#!/usr/bin/env python3
"""Plot a zoomed delay-16 autocorrelation around the first detected frame."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


DELAY = 16
DEFAULT_THRESHOLD = 0.8
DEFAULT_READ_SAMPLES = 500_000
ZOOM_SAMPLES = 450
PLATEAU_SAMPLES = 4


def autocorrelation(samples: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Return |x[n]|/p[n] and |x[n]| for every valid window."""
    if window <= 0:
        raise ValueError("N_win must be positive")
    count = samples.size - DELAY - window + 1
    if count <= 0:
        raise ValueError(
            f"recording has {samples.size} samples; need more than "
            f"{DELAY + window - 1}"
        )

    products = samples[:-DELAY] * np.conj(samples[DELAY:])
    powers = np.abs(samples) ** 2
    correlation = np.convolve(products, np.ones(window), mode="valid")
    power = np.convolve(powers, np.ones(window), mode="valid")[:count]
    coefficient = np.divide(
        np.abs(correlation[:count]),
        power,
        out=np.zeros(count, dtype=np.float64),
        where=power > np.finfo(np.float64).eps,
    )
    return coefficient, np.abs(correlation[:count])


def read_iq(path: Path, max_samples: int | None) -> np.ndarray:
    """Read interleaved float32 I,Q values as complex64 samples."""
    samples = np.fromfile(path, dtype=np.complex64, count=-1 if max_samples is None else max_samples)
    if samples.size == 0:
        raise ValueError(f"recording is empty or unreadable: {path}")
    return samples


def first_sustained_crossing(
    coefficient: np.ndarray, threshold: float, plateau: int
) -> int | None:
    """Return the first index with ``plateau`` consecutive threshold hits."""
    above = coefficient >= threshold
    runs = np.convolve(above.astype(np.int8), np.ones(plateau, dtype=np.int8), "valid")
    hits = np.flatnonzero(runs == plateau)
    return int(hits[0]) if hits.size else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recording",
        nargs="?",
        type=Path,
        help="complex64 recording; defaults to the first *.bin in recordings/",
    )
    parser.add_argument(
        "--window",
        type=int,
        nargs="+",
        default=[16, 48, 64],
        help="one or more N_win values (default: 16 48 64)",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("wiki/figura2_autocorrelacao.png"),
    )
    parser.add_argument(
        "--zoom-samples",
        type=int,
        default=ZOOM_SAMPLES,
        help="number of samples shown around the first crossing",
    )
    parser.add_argument(
        "--plateau",
        type=int,
        default=PLATEAU_SAMPLES,
        help="consecutive threshold hits required for detection",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=DEFAULT_READ_SAMPLES,
        help="optional limit, useful for quick experiments with large recordings",
    )
    args = parser.parse_args()

    recording = args.recording
    if recording is None:
        candidates = sorted(Path("recordings").glob("*.bin"))
        if not candidates:
            parser.error("no recording supplied and recordings/*.bin was not found")
        recording = candidates[0]
    if not recording.is_file():
        parser.error(f"recording not found: {recording}")
    if any(window <= 0 for window in args.window):
        parser.error("--window values must be positive")
    if args.zoom_samples <= 0 or args.plateau <= 0:
        parser.error("--zoom-samples and --plateau must be positive")
    if not 0 <= args.threshold:
        parser.error("--threshold must be non-negative")

    samples = read_iq(recording, args.max_samples)
    coefficients: dict[int, np.ndarray] = {}
    for window in args.window:
        coefficients[window], _ = autocorrelation(samples, window)

    detection_window = 48 if 48 in coefficients else args.window[0]
    event = first_sustained_crossing(
        coefficients[detection_window], args.threshold, args.plateau
    )
    if event is None:
        event = int(np.argmax(coefficients[detection_window]))
        print(
            f"warning: no sustained threshold crossing; using maximum at sample {event}"
        )

    start = max(0, event - args.zoom_samples // 4)
    stop = min(len(coefficients[detection_window]), start + args.zoom_samples)
    start = max(0, stop - args.zoom_samples)
    x = np.arange(start, stop) - event

    figure, axis = plt.subplots(figsize=(11, 5))
    for window, coefficient in coefficients.items():
        axis.plot(x, coefficient[start:stop], label=f"N_win={window}")

    axis.axhline(
        args.threshold,
        color="black",
        linestyle="--",
        label=f"threshold={args.threshold:g}",
    )
    axis.axvline(0, color="tab:red", linestyle=":", label=f"first frame: n={event}")
    axis.set_title(f"802.11a delay-{DELAY} autocorrelation near first frame")
    axis.set_xlabel(f"Sample offset relative to first frame (absolute n={event})")
    axis.set_ylabel(r"$c[n] = |x[n]| / p[n]$")
    axis.set_ylim(0, 1.05)
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=150)
    print(f"saved {args.output} ({samples.size} samples, zoom n={start}:{stop})")


if __name__ == "__main__":
    main()
