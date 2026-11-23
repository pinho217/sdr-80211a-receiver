#!/usr/bin/env python3
"""Figure 2 of the paper: delay-16 autocorrelation coefficient c[n] around a sync_short trigger.

--power-window grc   : p[n] over N_win+16 samples, as in wifi_phy_hier.py  (plateau = N/(N+16) = 0.75 for N=48)
--power-window paper : p[n] over N_win samples, as in Eq. (2)             (plateau ~ 1)
  python3 scripts/plot_autocorrelation.py recordings/X.bin --trigger 123456
"""
import argparse
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
from sdr80211 import *

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("recording", type=Path)
ap.add_argument("--trigger", type=int, required=True)
ap.add_argument("--window", type=int, nargs="+", default=[16, 48, 64])
ap.add_argument("--power-window", choices=["grc", "paper"], default="grc")
ap.add_argument("--threshold", type=float, default=0.56)
ap.add_argument("--before", type=int, default=120)
ap.add_argument("--after", type=int, default=380)
ap.add_argument("--output", type=Path, default=Path("wiki/figura2_autocorrelacao.png"))
a = ap.parse_args()

lo = max(0, a.trigger - a.before - 300)
x = read_iq(a.recording, lo, a.trigger - lo + a.after + 100)
rel = np.arange(-a.before, a.after)
fig, ax = plt.subplots(figsize=(11, 5))
for N in a.window:
    c, _ = graph_metric(x, N, None if a.power_window == "grc" else N)
    idx = a.trigger - lo + rel
    ax.plot(rel, c[idx], label=f"N_win={N}" + (f" (plateau {N/(N+16):.2f})" if a.power_window == "grc" else ""))
ax.axhline(a.threshold, color="k", ls="--", label=f"threshold={a.threshold:g}")
ax.axvline(0, color="tab:red", ls=":", label=f"sync_short trigger n={a.trigger}")
ax.set_xlabel("sample offset relative to the trigger"); ax.set_ylabel("c[n] = |a[n]| / p[n]"); ax.set_ylim(0, 1.1)
ax.set_title(f"delay-16 autocorrelation ({a.power_window} normalisation)"); ax.grid(alpha=.3); ax.legend(); fig.tight_layout()
a.output.parent.mkdir(parents=True, exist_ok=True); fig.savefig(a.output, dpi=150); print("saved", a.output)
