"""
plot_qcc_log.py

Offline burn-test analysis for the CSV logs written by core/frame_logger.py
(Tools -> Start Data Logging in the main GUI). Prints a summary (loss %,
delay percentiles, message-number gaps) and renders one figure with three
panels:

  1. Response delay (us) vs. time - every response as a dot, every TIMEOUT
     as a red x pinned above the delay band, so latency drift/spikes and
     the exact time of each missing packet are visible in one glance.
  2. Rolling packet-loss %% over the same time axis (same x as panel 1).
  3. Histogram of the OK response delays - the distribution's shape
     (tight? long-tailed? bimodal?) that the time series can't show.

Standalone command-line tool, not part of the GUI:

    python apps/plot_qcc_log.py qcc_log_20260712_120000.csv
    python apps/plot_qcc_log.py log.csv --window 500 --out fig.png --no-show

Requires matplotlib (not a GUI dependency):  pip install matplotlib
"""

import argparse
import csv
import statistics
import sys
from datetime import datetime

# Timeouts/loss wear the status red in every panel; responses wear the one
# data hue. Timeouts additionally differ by marker shape (x vs dot), so the
# encoding survives grayscale/colorblind viewing.
DATA_BLUE = "#4269d0"
STATUS_RED = "#c5453e"
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarize(rows, path):
    queries = [r for r in rows if r["result"] != "UNSOLICITED"]
    by_result = {}
    for r in rows:
        by_result[r["result"]] = by_result.get(r["result"], 0) + 1
    ok_delays = [float(r["delay_us"]) for r in queries
                 if r["result"] == "OK" and r["delay_us"]]

    print(f"\n{path}")
    print(f"  queries sent:        {len(queries)}")
    for result in ("OK", "TIMEOUT", "CRC_FAIL", "MSG_NUM_MISMATCH", "UNSOLICITED"):
        if by_result.get(result):
            print(f"  {result:<20} {by_result[result]}")
    timeouts = by_result.get("TIMEOUT", 0)
    if queries:
        print(f"  packet loss:         {100.0 * timeouts / len(queries):.3f} %")

    # msg_number continuity - the GUI increments it on every send, so a
    # skipped number means a send this log never saw (e.g. logging started
    # mid-run is normal; gaps in the middle are not).
    nums = sorted(int(r["msg_number"]) for r in queries if r["msg_number"])
    gaps = [(a, b) for a, b in zip(nums, nums[1:]) if b - a > 1]
    if gaps:
        print(f"  msg_number gaps:     {len(gaps)} (first: {gaps[0][0]} -> {gaps[0][1]})")

    if ok_delays:
        ok_delays.sort()
        p99 = ok_delays[min(len(ok_delays) - 1, int(0.99 * len(ok_delays)))]
        print(f"  delay us (OK):       min {ok_delays[0]:.1f} | "
              f"mean {statistics.fmean(ok_delays):.1f} | "
              f"median {statistics.median(ok_delays):.1f} | "
              f"p99 {p99:.1f} | max {ok_delays[-1]:.1f}")
    print()
    return queries


def rolling_loss(queries, window):
    """Percent of TIMEOUTs in a sliding window over the queries, in time order."""
    losses = [1.0 if r["result"] == "TIMEOUT" else 0.0 for r in queries]
    out, running = [], 0.0
    for i, v in enumerate(losses):
        running += v
        if i >= window:
            running -= losses[i - window]
        out.append(100.0 * running / min(i + 1, window))
    return out


def main():
    ap = argparse.ArgumentParser(description="Plot a QCC burn-test CSV log.")
    ap.add_argument("csv_path")
    ap.add_argument("--window", type=int, default=200,
                    help="rolling packet-loss window, in queries (default 200)")
    ap.add_argument("--out", help="output image path (default: <csv>.png)")
    ap.add_argument("--no-show", action="store_true", help="save only, no window")
    args = ap.parse_args()

    try:
        import matplotlib
        if args.no_show:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("matplotlib is required for plotting:  pip install matplotlib")

    rows = load_rows(args.csv_path)
    queries = summarize(rows, args.csv_path)
    if not queries:
        sys.exit("No query rows in this log - nothing to plot.")

    times = [datetime.strptime(r["tx_timestamp"], TS_FMT) for r in queries]
    ok_t, ok_d = [], []
    for r, t in zip(queries, times):
        if r["delay_us"]:
            ok_t.append(t)
            ok_d.append(float(r["delay_us"]))
    to_t = [t for r, t in zip(queries, times) if r["result"] == "TIMEOUT"]

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(12, 9), height_ratios=[3, 1.2, 1.5],
        constrained_layout=True,
    )
    fig.suptitle(f"QCC query/response burn test - {args.csv_path}", fontsize=11)

    # Panel 1: delay vs time; timeouts pinned in a band above the data so a
    # missing packet never masquerades as a fast response at y=0.
    ax1.scatter(ok_t, ok_d, s=6, color=DATA_BLUE, label="response delay (µs)")
    if to_t:
        ceiling = max(ok_d) * 1.08 if ok_d else 1.0
        ax1.scatter(to_t, [ceiling] * len(to_t), s=28, marker="x",
                    color=STATUS_RED, label="TIMEOUT (no response)")
    ax1.set_ylabel("delay (µs)")
    ax1.legend(loc="upper right", frameon=False, fontsize=9)

    # Panel 2: rolling loss, same time axis as panel 1.
    ax2.plot(times, rolling_loss(queries, args.window),
             color=STATUS_RED, linewidth=1.5)
    ax2.set_ylabel(f"loss % ({args.window}-query window)", fontsize=9)
    ax2.set_ylim(bottom=0)
    ax2.sharex(ax1)

    # Panel 3: delay distribution (its own x axis - µs, not time).
    if ok_d:
        ax3.hist(ok_d, bins=80, color=DATA_BLUE)
    ax3.set_xlabel("delay (µs)")
    ax3.set_ylabel("responses")

    for ax in (ax1, ax2, ax3):
        ax.grid(True, alpha=0.25, linewidth=0.5)
        ax.spines[["top", "right"]].set_visible(False)

    out = args.out or args.csv_path + ".png"
    fig.savefig(out, dpi=130)
    print(f"figure saved to {out}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
