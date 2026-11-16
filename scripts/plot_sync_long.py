#!/usr/bin/env python3
"""Plot the 802.11a LTS matched-filter output and data-symbol boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


DELAY = 16
SHORT_THRESHOLD = 0.8
DEFAULT_READ_SAMPLES = 500_000
LTS = np.array(
    [
        (-0.0455 - 1.0679j), (0.3528 - 0.9865j), (0.8594 + 0.7348j),
        (0.1874 + 0.2475j), (0.5309 - 0.7784j), (-1.0218 - 0.4897j),
        (-0.3401 - 0.9423j), (0.8657 - 0.2298j), (0.4734 + 0.0362j),
        (0.0088 - 1.0207j), (-1.2142 - 0.4205j), (0.2172 - 0.5195j),
        (0.5207 - 0.1326j), (-0.1995 + 1.4259j), (1.0583 - 0.0363j),
        (0.5547 - 0.5547j), (0.3277 + 0.8728j), (-0.5077 + 0.3488j),
        (-1.1650 + 0.5789j), (0.7297 + 0.8197j), (0.6173 + 0.1253j),
        (-0.5353 + 0.7214j), (-0.5011 - 0.1935j), (-0.3110 - 1.3392j),
        (-1.0818 - 0.1470j), (-1.1300 - 0.1820j), (0.6663 - 0.6571j),
        (-0.0249 + 0.4773j), (-0.8155 + 1.0218j), (0.8140 + 0.9396j),
        (0.1090 + 0.8662j), (-1.3868 + 0j), (0.1090 - 0.8662j),
        (0.8140 - 0.9396j), (-0.8155 - 1.0218j), (-0.0249 - 0.4773j),
        (0.6663 + 0.6571j), (-1.1300 + 0.1820j), (-1.0818 + 0.1470j),
        (-0.3110 + 1.3392j), (-0.5011 + 0.1935j), (-0.5353 - 0.7214j),
        (0.6173 - 0.1253j), (0.7297 - 0.8197j), (-1.1650 - 0.5789j),
        (-0.5077 - 0.3488j), (0.3277 - 0.8728j), (0.5547 + 0.5547j),
        (1.0583 + 0.0363j), (-0.1995 - 1.4259j), (0.5207 + 0.1326j),
        (0.2172 + 0.5195j), (-1.2142 + 0.4205j), (0.0088 + 1.0207j),
        (0.4734 - 0.0362j), (0.8657 + 0.2298j), (-0.3401 + 0.9423j),
        (-1.0218 + 0.4897j), (0.5309 + 0.7784j), (0.1874 - 0.2475j),
        (0.8594 - 0.7348j), (0.3528 + 0.9865j), (-0.0455 + 1.0679j),
        (1.3868 + 0j),
    ],
    dtype=np.complex64,
)


def read_iq(path: Path, max_samples: int) -> np.ndarray:
    samples = np.fromfile(path, dtype=np.complex64, count=max_samples)
    if samples.size <= DELAY + 64:
        raise ValueError(f"recording is too short or unreadable: {path}")
    return samples


def short_metric(samples: np.ndarray, window: int = 48) -> np.ndarray:
    products = samples[:-DELAY] * np.conj(samples[DELAY:])
    power = np.abs(samples) ** 2
    numerator = np.convolve(products, np.ones(window), "valid")
    denominator = np.convolve(power, np.ones(window), "valid")[: numerator.size]
    return np.divide(
        np.abs(numerator),
        denominator,
        out=np.zeros(numerator.size),
        where=denominator > np.finfo(float).eps,
    )


def first_crossing(metric: np.ndarray, threshold: float) -> int:
    hits = np.flatnonzero(metric >= threshold)
    return int(hits[0]) if hits.size else int(np.argmax(metric))


def local_maxima(values: np.ndarray, minimum_distance: int = 32) -> np.ndarray:
    candidates = np.flatnonzero(
        (values[1:-1] >= values[:-2]) & (values[1:-1] > values[2:])
    ) + 1
    selected: list[int] = []
    for index in candidates[np.argsort(values[candidates])[::-1]]:
        if all(abs(int(index) - other) >= minimum_distance for other in selected):
            selected.append(int(index))
        if len(selected) == 4:
            break
    return np.array(sorted(selected), dtype=int)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", nargs="?", type=Path)
    parser.add_argument("--max-samples", type=int, default=DEFAULT_READ_SAMPLES)
    parser.add_argument("--threshold", type=float, default=SHORT_THRESHOLD)
    parser.add_argument("--output", type=Path, default=Path("wiki/figura3_sync_long.png"))
    args = parser.parse_args()

    recording = args.recording
    if recording is None:
        candidates = sorted(Path("recordings").glob("*.bin"))
        if not candidates:
            parser.error("no recording supplied and recordings/*.bin was not found")
        recording = candidates[0]
    if not recording.is_file():
        parser.error(f"recording not found: {recording}")

    samples = read_iq(recording, args.max_samples)
    metric = short_metric(samples)
    short_start = first_crossing(metric, args.threshold)

    # The LTS follows the first STS shortly afterwards; do not select peaks
    # belonging to later frames in a long recording.
    search_start = min(short_start, samples.size - 64)
    search_stop = min(samples.size, search_start + 500)
    correlation = np.convolve(
        samples[search_start:search_stop], np.conj(LTS[::-1]), "valid"
    )
    peaks = local_maxima(np.abs(correlation))
    if peaks.size == 0:
        parser.error("no matched-filter peak found in the recording")
    peaks_absolute = peaks + search_start
    np_first_data = int(peaks_absolute.max() + 64)

    half_width = 180
    plot_start = max(search_start, int(peaks_absolute.min()) - half_width)
    correlation_stop = search_start + correlation.size
    plot_stop = min(correlation_stop, np_first_data + half_width)
    x = np.arange(plot_start, plot_stop)
    y = np.abs(correlation[plot_start - search_start : plot_stop - search_start])

    figure, axis = plt.subplots(figsize=(11, 5))
    axis.plot(x, y, label="|LTS matched-filter output|")
    axis.scatter(
        peaks_absolute,
        np.abs(correlation[peaks]),
        color="tab:red",
        zorder=3,
        label="dominant peaks",
    )
    axis.axvline(np_first_data, color="tab:green", linestyle="--",
                 label=f"np = max(Np) + 64 = {np_first_data}")
    axis.set_title(f"802.11a LTS synchronization: {recording.name}")
    axis.set_xlabel("Sample n")
    axis.set_ylabel("Matched-filter magnitude")
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=150)
    print(f"saved {args.output}; short start={short_start}, peaks={peaks_absolute.tolist()}")


if __name__ == "__main__":
    main()
