"""
plot_qcc_log.py

Offline burn-test analysis for the CSV logs written by core/frame_logger.py
(Tools -> Start Data Logging in the main GUI). Prints a summary (loss %,
delay percentiles, message-number gaps, per-LRU failure counts) and
renders one figure:

  1. Response delay (us) vs. time - every response as a dot, every TIMEOUT
     as a red x pinned above the delay band, so latency drift/spikes and
     the exact time of each missing packet are visible in one glance.
  2. Rolling packet-loss %% over the same time axis (same x as panel 1).
  3. LRU NOT_OK events vs. time (Link Test rows only, same x) - which
     LRU failed and exactly when, so "LRU-17 drops out every night"
     and "half the array died at once" look different at a glance.
  4. Histogram of the OK response delays - the distribution's shape
     (tight? long-tailed? bimodal?) that the time series can't show.
  5. NOT_OK count per LRU - the ranking of flaky/dead LRUs over the
     whole run. (Whole-frame TIMEOUTs are not counted here - no LRU
     answered those, and they're already in panels 1-2.)

Panels 3 and 5 appear only when the log has per-LRU data (Link Test burn
runs); logs of other commands fall back to the original 3-panel layout.

How many LRUs a run covered is read from the log's own lru_NN columns
rather than from the GUI's current configuration - a log analyzed here may
well come from a differently-shaped array than the one now configured, or
from another machine entirely.

Command-line tool:

    python apps/plot_qcc_log.py qcc_log_20260712_120000.csv
    python apps/plot_qcc_log.py log.csv --window 500 --out fig.png --no-show

`summarize()` and `build_figure()` are also called directly by
widgets/plot_log_dialog.py (Tools -> Plot Log File… in the main GUI) to
embed the same figure in a Qt dialog instead of a separate matplotlib
window - build_figure() takes an already-constructed Figure so either
caller can supply one from pyplot (this script) or from
matplotlib.figure.Figure (the Qt-embedded canvas).

Requires matplotlib (not a GUI dependency):  pip install matplotlib
"""

import argparse
import csv
import os
import statistics
import sys
from datetime import datetime

# Default output location for a saved figure when neither this script's
# --out nor the GUI's Save Image… dialog is given an explicit path - a
# plots/ folder next to wherever the tool is run from, so figures land in
# one predictable place instead of scattered next to whatever CSV they
# came from.
DEFAULT_PLOTS_DIR = os.path.join(os.getcwd(), "plots")

# Timeouts/loss wear the status red in every panel; responses wear the one
# data hue. Timeouts additionally differ by marker shape (x vs dot), so the
# encoding survives grayscale/colorblind viewing.
DATA_BLUE = "#4269d0"
STATUS_RED = "#c5453e"
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"

# Fallback when a log has no lru_NN columns at all to count (an empty file,
# or a non-Link-Test run) - only affects axis extents on panels that
# wouldn't be drawn anyway.
DEFAULT_NUM_LRU = 96


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def num_lru_in(rows):
    """
    How many LRUs the log covers, counted from its own lru_NN column names.
    A log written by a differently-configured GUI (or an older lru_NN one)
    still plots against its own array size rather than whatever this
    machine happens to be set to.
    """
    if not rows:
        return DEFAULT_NUM_LRU
    count = sum(
        1 for name in rows[0]
        if name and (name.startswith("lru_") or name.startswith("qtrm_"))
        and name.rpartition("_")[2].isdigit()
    )
    return count or DEFAULT_NUM_LRU


def _row_value(row, *names):
    """First non-empty value among `names` - reads pre-rename logs too."""
    for name in names:
        value = row.get(name)
        if value:
            return value
    return ""


def is_link_row(row):
    """True for rows the logger ran per-LRU Link analysis on."""
    return _row_value(row, "lru_ok_count", "qtrm_ok_count") != ""


def not_ok_indices(row):
    """0-based LRU indices marked NOT_OK on this row."""
    s = _row_value(row, "lru_not_ok_list", "qtrm_not_ok_list")
    return [int(x) for x in s.split(",") if x]


def summarize(rows, path):
    """Prints the burn-test summary and returns (queries, summary_text) -
    the same text, joined, for callers (the GUI dialog) that want to show
    it somewhere other than stdout."""
    lines = []

    def emit(s=""):
        lines.append(s)
        print(s)

    queries = [r for r in rows if r["result"] != "UNSOLICITED"]
    by_result = {}
    for r in rows:
        by_result[r["result"]] = by_result.get(r["result"], 0) + 1
    ok_delays = [float(r["delay_us"]) for r in queries
                 if r["result"] == "OK" and r["delay_us"]]

    emit(f"{path}")
    emit(f"  queries sent:        {len(queries)}")
    for result in ("OK", "TIMEOUT", "CRC_FAIL", "MSG_NUM_MISMATCH", "UNSOLICITED"):
        if by_result.get(result):
            emit(f"  {result:<20} {by_result[result]}")
    timeouts = by_result.get("TIMEOUT", 0)
    if queries:
        emit(f"  packet loss:         {100.0 * timeouts / len(queries):.3f} %")

    # msg_number continuity - the GUI increments it on every send, so a
    # skipped number means a send this log never saw (e.g. logging started
    # mid-run is normal; gaps in the middle are not).
    nums = sorted(int(r["msg_number"]) for r in queries if r["msg_number"])
    gaps = [(a, b) for a, b in zip(nums, nums[1:]) if b - a > 1]
    if gaps:
        emit(f"  msg_number gaps:     {len(gaps)} (first: {gaps[0][0]} -> {gaps[0][1]})")

    if ok_delays:
        ok_delays.sort()
        p99 = ok_delays[min(len(ok_delays) - 1, int(0.99 * len(ok_delays)))]
        emit(f"  delay us (OK):       min {ok_delays[0]:.1f} | "
             f"mean {statistics.fmean(ok_delays):.1f} | "
             f"median {statistics.median(ok_delays):.1f} | "
             f"p99 {p99:.1f} | max {ok_delays[-1]:.1f}")

    # Per-LRU Link Test breakdown - which LRUs failed to reply, ranked.
    link_rows = [r for r in queries if is_link_row(r)]
    if link_rows:
        fails = {}
        for r in link_rows:
            for q in not_ok_indices(r):
                fails[q] = fails.get(q, 0) + 1
        emit(f"  link-test rows:      {len(link_rows)} (per-LRU analyzed)")
        if fails:
            total = sum(fails.values())
            emit(f"  LRU NOT_OK marks:   {total} across {len(fails)} LRU(s)")
            ranked = sorted(fails.items(), key=lambda kv: (-kv[1], kv[0]))
            for q, n in ranked[:15]:
                emit(f"    LRU-{q:<3} NOT_OK {n} time(s) "
                     f"({100.0 * n / len(link_rows):.2f} % of link tests)")
            if len(ranked) > 15:
                emit(f"    ... and {len(ranked) - 15} more (see panel 5)")
        else:
            emit("  LRU NOT_OK marks:   0 - every queried LRU replied "
                 "on every answered link test")
    return queries, "\n".join(lines)


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


def _index_ticks(n_lru):
    """
    Roughly a dozen evenly-spaced LRU-index ticks. The old fixed step of 16
    was right for 96 LRUs and unreadable at either extreme of the
    configurable range.
    """
    step = max(1, round(n_lru / 12))
    return range(0, n_lru + 1, step)


def build_figure(fig, queries, csv_path, window=200):
    """
    Populates an already-constructed (empty) matplotlib Figure with the
    burn-test panels and returns it. Takes the Figure rather than creating
    one so either caller can supply the kind it needs: this script's
    main() uses a pyplot-managed figure (for plt.show()), while
    widgets/plot_log_dialog.py uses a bare matplotlib.figure.Figure fed
    straight into a Qt canvas.
    """
    times = [datetime.strptime(r["tx_timestamp"], TS_FMT) for r in queries]
    ok_t, ok_d = [], []
    for r, t in zip(queries, times):
        if r["delay_us"]:
            ok_t.append(t)
            ok_d.append(float(r["delay_us"]))
    to_t = [t for r, t in zip(queries, times) if r["result"] == "TIMEOUT"]

    # Per-LRU NOT_OK events (Link Test rows only): when + which LRU.
    n_lru = num_lru_in(queries)
    ev_t, ev_q = [], []
    fail_counts = [0] * n_lru
    has_lru_data = False
    for r, t in zip(queries, times):
        if not is_link_row(r):
            continue
        has_lru_data = True
        for q in not_ok_indices(r):
            ev_t.append(t)
            ev_q.append(q)
            fail_counts[q] += 1

    fig.set_size_inches(12, 12 if has_lru_data else 9)
    if has_lru_data:
        gs = fig.add_gridspec(4, 2, height_ratios=[2.6, 1.0, 2.0, 1.5])
        ax1 = fig.add_subplot(gs[0, :])
        ax2 = fig.add_subplot(gs[1, :])
        axq = fig.add_subplot(gs[2, :])
        ax3 = fig.add_subplot(gs[3, 0])
        axb = fig.add_subplot(gs[3, 1])
    else:
        (ax1, ax2, ax3) = fig.subplots(3, 1, height_ratios=[3, 1.2, 1.5])
        axq = axb = None
    fig.suptitle(f"QCC query/response burn test - {csv_path}", fontsize=11)

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
    ax2.plot(times, rolling_loss(queries, window),
             color=STATUS_RED, linewidth=1.5)
    ax2.set_ylabel(f"loss % ({window}-query window)", fontsize=9)
    ax2.set_ylim(bottom=0)
    ax2.sharex(ax1)

    # Panel 3 (Link Test runs only): every LRU NOT_OK event as (time, LRU#),
    # same time axis - vertical stripes = whole-array events, horizontal
    # bands = one flaky LRU.
    if axq is not None:
        if ev_t:
            axq.scatter(ev_t, ev_q, s=16, marker="x", color=STATUS_RED)
        else:
            axq.text(0.5, 0.5, "no LRU NOT_OK events",
                     transform=axq.transAxes, ha="center", va="center",
                     fontsize=10, color="0.45")
        axq.set_ylim(-3, n_lru + 2)
        axq.set_yticks(_index_ticks(n_lru))
        axq.set_ylabel("LRU # of NOT_OK reply")
        axq.sharex(ax1)

    # Delay distribution (its own x axis - µs, not time).
    if ok_d:
        ax3.hist(ok_d, bins=80, color=DATA_BLUE)
    ax3.set_xlabel("delay (µs)")
    ax3.set_ylabel("responses")

    # Per-LRU failure ranking over the whole run (Link Test runs only).
    if axb is not None:
        axb.bar(range(n_lru), fail_counts, width=0.85, color=STATUS_RED)
        axb.set_xlim(-1, n_lru)
        axb.set_xticks(_index_ticks(n_lru))
        axb.set_xlabel("LRU #")
        axb.set_ylabel("NOT_OK count")

    for ax in (ax1, ax2, ax3, axq, axb):
        if ax is None:
            continue
        ax.grid(True, alpha=0.25, linewidth=0.5)
        ax.spines[["top", "right"]].set_visible(False)

    return fig


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
    queries, _summary_text = summarize(rows, args.csv_path)
    if not queries:
        sys.exit("No query rows in this log - nothing to plot.")

    fig = plt.figure(constrained_layout=True)
    build_figure(fig, queries, args.csv_path, args.window)

    out = args.out
    if not out:
        os.makedirs(DEFAULT_PLOTS_DIR, exist_ok=True)
        out = os.path.join(
            DEFAULT_PLOTS_DIR,
            os.path.splitext(os.path.basename(args.csv_path))[0] + ".png",
        )
    fig.savefig(out, dpi=130)
    print(f"figure saved to {out}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
