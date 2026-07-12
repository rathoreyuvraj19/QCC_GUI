"""
plot_qcc_log.py

Offline burn-test analysis for the CSV logs written by core/frame_logger.py
(Tools -> Start Data Logging in the main GUI). Prints a summary (loss %,
delay percentiles, message-number gaps, per-QTRM failure counts) and
renders one figure:

  1. Response delay (us) vs. time - every response as a dot, every TIMEOUT
     as a red x pinned above the delay band, so latency drift/spikes and
     the exact time of each missing packet are visible in one glance.
  2. Rolling packet-loss %% over the same time axis (same x as panel 1).
  3. QTRM NOT_OK events vs. time (Link Test rows only, same x) - which
     QTRM failed and exactly when, so "QTRM-17 drops out every night"
     and "half the array died at once" look different at a glance.
  4. Histogram of the OK response delays - the distribution's shape
     (tight? long-tailed? bimodal?) that the time series can't show.
  5. NOT_OK count per QTRM - the ranking of flaky/dead QTRMs over the
     whole run. (Whole-frame TIMEOUTs are not counted here - no QTRM
     answered those, and they're already in panels 1-2.)

Panels 3 and 5 appear only when the log has per-QTRM data (Link Test burn
runs); logs of other commands fall back to the original 3-panel layout.

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
NUM_QTRM = 96


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def is_link_row(row):
    """True for rows the logger ran per-QTRM Link analysis on."""
    return (row.get("qtrm_ok_count") or "") != ""


def not_ok_indices(row):
    """0-based QTRM indices marked NOT_OK on this row."""
    s = row.get("qtrm_not_ok_list") or ""
    return [int(x) for x in s.split(",") if x]


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

    # Per-QTRM Link Test breakdown - which QTRMs failed to reply, ranked.
    link_rows = [r for r in queries if is_link_row(r)]
    if link_rows:
        fails = {}
        for r in link_rows:
            for q in not_ok_indices(r):
                fails[q] = fails.get(q, 0) + 1
        print(f"  link-test rows:      {len(link_rows)} (per-QTRM analyzed)")
        if fails:
            total = sum(fails.values())
            print(f"  QTRM NOT_OK marks:   {total} across {len(fails)} QTRM(s)")
            ranked = sorted(fails.items(), key=lambda kv: (-kv[1], kv[0]))
            for q, n in ranked[:15]:
                print(f"    QTRM-{q:<3} NOT_OK {n} time(s) "
                      f"({100.0 * n / len(link_rows):.2f} % of link tests)")
            if len(ranked) > 15:
                print(f"    ... and {len(ranked) - 15} more (see panel 5)")
        else:
            print("  QTRM NOT_OK marks:   0 - every queried QTRM replied "
                  "on every answered link test")
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

    # Per-QTRM NOT_OK events (Link Test rows only): when + which QTRM.
    ev_t, ev_q = [], []
    fail_counts = [0] * NUM_QTRM
    has_qtrm_data = False
    for r, t in zip(queries, times):
        if not is_link_row(r):
            continue
        has_qtrm_data = True
        for q in not_ok_indices(r):
            ev_t.append(t)
            ev_q.append(q)
            fail_counts[q] += 1

    if has_qtrm_data:
        fig = plt.figure(figsize=(12, 12), constrained_layout=True)
        gs = fig.add_gridspec(4, 2, height_ratios=[2.6, 1.0, 2.0, 1.5])
        ax1 = fig.add_subplot(gs[0, :])
        ax2 = fig.add_subplot(gs[1, :])
        axq = fig.add_subplot(gs[2, :])
        ax3 = fig.add_subplot(gs[3, 0])
        axb = fig.add_subplot(gs[3, 1])
    else:
        fig, (ax1, ax2, ax3) = plt.subplots(
            3, 1, figsize=(12, 9), height_ratios=[3, 1.2, 1.5],
            constrained_layout=True,
        )
        axq = axb = None
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

    # Panel 3 (Link Test runs only): every QTRM NOT_OK event as (time, QTRM#),
    # same time axis - vertical stripes = whole-array events, horizontal
    # bands = one flaky QTRM.
    if axq is not None:
        if ev_t:
            axq.scatter(ev_t, ev_q, s=16, marker="x", color=STATUS_RED)
        else:
            axq.text(0.5, 0.5, "no QTRM NOT_OK events",
                     transform=axq.transAxes, ha="center", va="center",
                     fontsize=10, color="0.45")
        axq.set_ylim(-3, NUM_QTRM + 2)
        axq.set_yticks(range(0, NUM_QTRM + 1, 16))
        axq.set_ylabel("QTRM # of NOT_OK reply")
        axq.sharex(ax1)

    # Delay distribution (its own x axis - µs, not time).
    if ok_d:
        ax3.hist(ok_d, bins=80, color=DATA_BLUE)
    ax3.set_xlabel("delay (µs)")
    ax3.set_ylabel("responses")

    # Per-QTRM failure ranking over the whole run (Link Test runs only).
    if axb is not None:
        axb.bar(range(NUM_QTRM), fail_counts, width=0.85, color=STATUS_RED)
        axb.set_xlim(-1, NUM_QTRM)
        axb.set_xticks(range(0, NUM_QTRM + 1, 16))
        axb.set_xlabel("QTRM #")
        axb.set_ylabel("NOT_OK count")

    for ax in (ax1, ax2, ax3, axq, axb):
        if ax is None:
            continue
        ax.grid(True, alpha=0.25, linewidth=0.5)
        ax.spines[["top", "right"]].set_visible(False)

    out = args.out or args.csv_path + ".png"
    fig.savefig(out, dpi=130)
    print(f"figure saved to {out}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
