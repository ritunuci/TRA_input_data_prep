"""
Nested 10% and 25% samples of the ABM3 demand files written by build_abm3_demand_files.py (added 2026-11-27).

Run build_abm3_demand_files.py first. Nothing upstream is read or rewritten: each sample is a subset of the rows of
the full file, copied byte for byte (same columns, values, request_ids and rq_time order).

Method (as the 1% debug sample in build_abm3_demand_files.py): exact size round(n * fraction), drawn without
replacement with rng.choice from np.random.default_rng(42), one stream per area. Nested: debug (1%) is inside 10%,
10% is inside 25%. The first sample starts from the existing debug rows and adds random rows from the rest of the
full file; each larger sample adds random rows to the one before. The debug file is read, never changed.

Inputs and outputs in outputs_MGRA15/<area>/demand/
  in   <area>_trips_nodes_study_area_with_beta.csv          full file
  in   <area>_debug_trips_nodes_study_area_with_beta.csv    1% debug sample
  out  <area>_10pct_trips_nodes_study_area_with_beta.csv    10% sample
  out  <area>_25pct_trips_nodes_study_area_with_beta.csv    25% sample
The trace file (<area>_trips_trace.csv) serves every sample (request_ids are the full file's). FleetPy and MTSM load
<area>_trips_nodes_study_area_with_beta.csv, so a sample is renamed (or named in the config) when it is used.
No scaling: a 25% sample is a quarter of the demand.

Checks (stops on failure, before writing): request_ids of the full file are 0..N-1 in row order; debug rows equal
their full-file rows; sizes exact; samples nested; every sample line equals its full-file line; rq_time sorted.

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_abm3_demand_samples.py
    python build_abm3_demand_samples.py --areas lemon_grove --fractions 0.1 0.25 --overwrite
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT_ROOT = os.path.join(HERE, "outputs_MGRA15")
SEED = 42
FRACTIONS = [0.10, 0.25]
AREAS = ["lemon_grove", "downtown_sd"]
STEM = "trips_nodes_study_area_with_beta"


def files(out_root, area, fractions):
    d = os.path.join(out_root, area, "demand")
    full = os.path.join(d, f"{area}_{STEM}.csv")
    debug = os.path.join(d, f"{area}_debug_{STEM}.csv")
    out = {f: os.path.join(d, f"{area}_{round(f * 100):d}pct_{STEM}.csv") for f in fractions}
    return full, debug, out


def read_lines(path):
    """header line + data lines, as bytes with their line ends"""
    with open(path, "rb") as fh:
        lines = fh.read().splitlines(keepends=True)
    return lines[0], lines[1:]


def build_area(area, out_root, fractions, seed):
    full_f, debug_f, out = files(out_root, area, fractions)
    for p in (full_f, debug_f):
        if not os.path.exists(p):
            sys.exit(f"ERROR: {p} not found. Run build_abm3_demand_files.py first.")
    header, lines = read_lines(full_f)
    dheader, dlines = read_lines(debug_f)
    full = pd.read_csv(full_f, usecols=["request_id", "rq_time"])
    ids = pd.read_csv(debug_f, usecols=["request_id"]).request_id.to_numpy()
    n = len(full)

    # checks on the inputs
    if len(lines) != n or not np.array_equal(full.request_id.to_numpy(), np.arange(n)):
        sys.exit(f"ERROR: {full_f}: request_id is not 0..N-1 in row order (or a line break inside a field).")
    if dheader != header:
        sys.exit(f"ERROR: {debug_f} has another header than the full file.")
    if len(set(ids)) != len(ids) or any(dlines[i] != lines[r] for i, r in enumerate(ids)):
        sys.exit(f"ERROR: {debug_f}: rows are not rows of the full file.")
    if not full.rq_time.is_monotonic_increasing:
        sys.exit(f"ERROR: {full_f}: rq_time is not sorted.")

    rng = np.random.default_rng(seed)
    chosen = np.zeros(n, dtype=bool)
    chosen[ids] = True
    report = {"debug (1%)": int(chosen.sum())}
    samples = {}
    for f in sorted(fractions):
        size = int(round(n * f))
        if size < chosen.sum():
            sys.exit(f"ERROR: {f:.0%} of {n} = {size} rows, fewer than the sample inside it ({chosen.sum()}).")
        pool = np.flatnonzero(~chosen)  # ascending row (= request_id) order
        add = rng.choice(len(pool), size=size - int(chosen.sum()), replace=False)
        chosen[pool[add]] = True
        rows = np.flatnonzero(chosen)  # full-file order
        if len(rows) != size:
            sys.exit(f"ERROR: {f:.0%} sample has {len(rows)} rows, expected {size}.")
        samples[f] = rows
        report[f"{f:.0%}"] = size

    # nesting and row checks
    prev = set(ids)
    for f in sorted(fractions):
        s = set(samples[f])
        if not prev <= s:
            sys.exit(f"ERROR: {f:.0%} sample does not contain the smaller one.")
        prev = s

    for f in sorted(fractions):
        with open(out[f], "wb") as fh:
            fh.write(header)
            fh.writelines(lines[r] for r in samples[f])
    # read back
    for f in sorted(fractions):
        h, sl = read_lines(out[f])
        if h != header or sl != [lines[r] for r in samples[f]]:
            sys.exit(f"ERROR: {out[f]} does not read back equal.")
        s = pd.read_csv(out[f], usecols=["request_id", "rq_time"])
        if not s.rq_time.is_monotonic_increasing or not np.array_equal(s.request_id.to_numpy(), samples[f]):
            sys.exit(f"ERROR: {out[f]}: order or request_id wrong.")
    print(f"{area}: full {n:,} rows; " + ", ".join(f"{k} {v:,}" for k, v in report.items()))
    for f in sorted(fractions):
        print(f"  written {out[f]}")


def main():
    ap = argparse.ArgumentParser(description="Nested 10%/25% samples of the ABM3 demand files.")
    ap.add_argument("--areas", nargs="+", choices=AREAS, default=AREAS)
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--fractions", nargs="+", type=float, default=FRACTIONS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    if any(not 0.01 < f < 1 for f in args.fractions) or len(set(args.fractions)) != len(args.fractions):
        sys.exit("ERROR: fractions must be distinct and between 0.01 and 1 (the 1% debug sample is the smallest).")
    for a in args.areas:
        for p in files(args.out_root, a, args.fractions)[2].values():
            if os.path.exists(p) and not args.overwrite:
                sys.exit(f"ERROR: {p} exists. Use --overwrite.")
    for a in args.areas:
        build_area(a, args.out_root, args.fractions, args.seed)


if __name__ == "__main__":
    main()
