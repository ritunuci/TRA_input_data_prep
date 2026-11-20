"""
Build the FleetPy/MTSM demand (trip-request) files for Lemon Grove and Downtown San Diego from the
SANDAG ABM3 (ActivitySim) resident trips, on the MGRA15 networks.

Run build_bikenet_networks.py and build_fleetpy_network_mapping.py first. Decisions: CLAUDE.md,
"Demand file redo for ABM3".

Inputs
  data_new/demand_data/final_trips.csv, final_households.csv, final_persons.csv   (ABM3, MGRA15 zones)
  data_new/<area dir>/<prefix>_MGRA15.csv                                          (study-area zones)
  data_new/mgra15/mgra15.shp                                                       (zone-system guard)
  outputs_MGRA15/<area>/mapping/Nodes_MGRA_mapping.csv, network/nodes.csv, network/<prefix>_raw_nodes.csv

Steps per area (one pass over final_trips.csv serves both areas)
  1. trips with origin and destination in the area's MGRA15 list (all purposes and modes)
  2. household join: trips from income-0 households are dropped; income = household income / max(num_workers, 1);
     auto_available = auto_ownership >= 1; num_autos = auto_ownership
  3. person join: transit_pass = transit_pass_ownership (must be 0/1)
  4. trips sorted by trip_id; np.random.default_rng(42) per area; draws in this order:
       rq_time = 10800 + (depart - 1) * 1800 + U{0..1799}  (periods 43-48 run past 86400 s)
       start = random node of the origin zone; end = random node of the destination zone other than start;
       if the destination's only node is the start, start is redrawn from the origin's other nodes;
       only when both zones have just that one node is the trip dropped (reason no_distinct_end_node)
       betas (TRPartA means/SDs/floors), cost-beta mean = 0.5 * mean_income / max(income, 5000),
       mean_income = mean (unfloored) income of the trips written; bt_t_fr = bt_c_gas
       Downtown only: parking_dur_min = max(0, lognorm(s, loc, scale)) in minutes
       debug sample: 1% of the rows, without replacement
  5. rows sorted by rq_time (numeric), then trip_id; request_id = 0..N-1

Outputs in outputs_MGRA15/<area>/demand/
  <area>_trips_nodes_study_area_with_beta.csv         21 columns (Downtown: + parking_dur_min = 22)
  <area>_debug_trips_nodes_study_area_with_beta.csv   1% sample, same request_ids as the full file
  <area>_trips_trace.csv                              request_id -> trip_id, household_id, person_id,
                                                      origin, destination, depart, trip_mode, purpose
  <area>_dropped_trips.csv                            dropped trips with reason (income_0, no_distinct_end_node)

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_abm3_demand_files.py
    python build_abm3_demand_files.py --areas lemon_grove --overwrite
"""

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import pyogrio

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_NEW = os.path.join(HERE, "data_new")
DEMAND_DIR = os.path.join(DATA_NEW, "demand_data")
MGRA15_SHP = os.path.join(DATA_NEW, "mgra15", "mgra15.shp")
DEFAULT_OUT_ROOT = os.path.join(HERE, "outputs_MGRA15")
SEED = 42
DEBUG_FRAC = 0.01
CHUNK = 2_000_000
# cost beta mean uses max(income, INCOME_FLOOR) (user decision 2026-11-19); the income column keeps the real value
INCOME_FLOOR = 5000

AREAS = {
    "lemon_grove": dict(prefix="Lemon_grove", parking=False,
                        zone_list=os.path.join(DATA_NEW, "lemon_grove", "Lemon_grove_MGRA15.csv")),
    "downtown_sd": dict(prefix="Downtown_SD", parking=True,
                        zone_list=os.path.join(DATA_NEW, "Downtown_SD", "Downtown_SD_MGRA15.csv")),
}

TRIP_COLS = ["trip_id", "person_id", "household_id", "origin", "destination", "depart", "trip_period",
             "trip_mode", "purpose"]

# TRPartA_demand_files (commit 768dca4): (column, mean, sd, floor); the cost mean is per row (None here)
BETAS = [("bt_c_0", 0.0, 0.0, 0.0),
         ("bt_c_ivt", 0.2, 0.047, 0.01),
         ("bt_c_gas", None, 0.377, 0.05),
         ("bt_t_0", 0.242, 0.04, 0.0),
         ("bt_t_wk", 0.3, 0.140, 0.01),
         ("bt_m_wt", 0.15, 0.022, 0.01),
         ("bt_f_wt", 0.15, 0.022, 0.01),
         ("bt_m_ivt", 0.15, 0.022, 0.01),
         ("bt_f_ivt", 0.15, 0.029, 0.01),
         ("bt_f_trfer", 0.5, 0.022, 0.01)]

# parking duration (minutes), the user's scipy.stats.lognorm(s, loc, scale), drawn with numpy
PARK_S, PARK_LOC, PARK_SCALE = 0.5341062374951262, -23.623502968177117, 95.44354175433033

OUT_COLS = ["rq_time", "start", "end", "request_id",
            "bt_c_0", "bt_c_ivt", "bt_c_gas", "bt_t_0", "bt_t_wk", "bt_m_wt", "bt_f_wt", "bt_m_ivt", "bt_f_ivt",
            "bt_f_trfer", "bt_t_fr", "income", "transit_pass", "auto_available", "num_autos", "purpose", "trip_mode"]
TRACE_COLS = ["request_id", "trip_id", "household_id", "person_id", "origin", "destination", "depart",
              "trip_mode", "purpose"]


def out_files(out_root, name):
    d = os.path.join(out_root, name, "demand")
    return d, {k: os.path.join(d, f"{name}_{s}.csv") for k, s in [
        ("full", "trips_nodes_study_area_with_beta"), ("debug", "debug_trips_nodes_study_area_with_beta"),
        ("trace", "trips_trace"), ("dropped", "dropped_trips")]}


def load_area(name, cfg, out_root, mgra15_ids):
    """Zone list + zone -> pickup node arrays, with the input checks."""
    zl = pd.read_csv(cfg["zone_list"])
    if "share_in" not in zl.columns:
        sys.exit(f"ERROR: {cfg['zone_list']} has no share_in column (not an MGRA15 study-area list).")
    zones = set(zl["MGRA"].astype(int))
    bad = zones - mgra15_ids
    if bad:
        sys.exit(f"ERROR: {len(bad)} zones of {cfg['zone_list']} are not in mgra15.shp, e.g. {sorted(bad)[:5]}")

    net = os.path.join(out_root, name, "network")
    mp = pd.read_csv(os.path.join(out_root, name, "mapping", "Nodes_MGRA_mapping.csv"))
    nodes = pd.read_csv(os.path.join(net, "nodes.csv"))
    rn = pd.read_csv(os.path.join(net, cfg["prefix"] + "_raw_nodes.csv"))
    if set(mp.MGRA) != zones:
        sys.exit(f"ERROR: the mapping covers {mp.MGRA.nunique()} zones, the list has {len(zones)}.")
    # FleetPy IDs are the raw node IDs in ascending order
    raw_sorted = np.sort(rn.NODE.values)
    if len(raw_sorted) != len(nodes):
        sys.exit("ERROR: nodes.csv and the raw nodes differ in size.")
    if not (raw_sorted[mp.node.values] == mp.raw_node.values).all():
        sys.exit("ERROR: mapping node IDs do not match the FleetPy IDs of their raw nodes.")
    pick = set(rn.loc[rn.IS_DEMAND == 1, "NODE"])
    if not set(mp.raw_node) <= pick:
        sys.exit("ERROR: a mapped node is not a pickup node.")
    zn = {int(m): np.sort(g.node.values.astype(np.int64)) for m, g in mp.groupby("MGRA")}
    return zones, zn


def read_trips(all_zones):
    t0 = time.time()
    path = os.path.join(DEMAND_DIR, "final_trips.csv")
    header = pd.read_csv(path, nrows=0).columns
    miss = [c for c in TRIP_COLS if c not in header]
    if miss:
        sys.exit(f"ERROR: final_trips.csv lacks {miss} (not the ABM3 trip file?).")
    parts = []
    for ch in pd.read_csv(path, usecols=TRIP_COLS, chunksize=CHUNK):
        parts.append(ch[ch.origin.isin(all_zones) & ch.destination.isin(all_zones)])
    tr = pd.concat(parts, ignore_index=True)
    print(f"read final_trips.csv: {len(tr):,} trips with both ends in a study zone ({time.time() - t0:.0f} s)")
    return tr


def build_area(name, cfg, tr_all, hh, per, zones, zn, files, out_dir):
    print(f"\n== {name}")
    t = tr_all[tr_all.origin.isin(zones) & tr_all.destination.isin(zones)].copy()
    print(f"  trips with both ends in the area: {len(t):,}")

    # ---- household and person joins
    t = t.merge(hh, on="household_id", how="left", validate="many_to_one")
    if t.hh_income.isna().any():
        sys.exit("ERROR: trips whose household is not in final_households.csv.")
    t = t.merge(per, on="person_id", how="left", validate="many_to_one")
    if t.transit_pass.isna().any():
        sys.exit("ERROR: trips whose person is not in final_persons.csv.")
    dropped = [t[t.hh_income == 0].assign(reason="income_0")]
    t = t[t.hh_income > 0].copy()
    print(f"  removed, household income 0: {len(dropped[0]):,} trips "
          f"({dropped[0].household_id.nunique()} households); kept {len(t):,}")
    t["income"] = t.hh_income / t.num_workers.clip(lower=1)
    t["auto_available"] = t.auto_ownership >= 1
    t["num_autos"] = t.auto_ownership.astype(int)

    # ---- random draws, in a fixed order
    t = t.sort_values("trip_id").reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    n = len(t)
    if not t.depart.between(1, 48).all():
        sys.exit("ERROR: depart outside 1..48.")
    t["rq_time"] = 10800 + (t.depart.astype(np.int64) - 1) * 1800 + rng.integers(0, 1800, n)
    u1, u2 = rng.random(n), rng.random(n)
    start = np.empty(n, np.int64)
    end = np.full(n, -1, np.int64)
    for i, (o, d) in enumerate(zip(t.origin.values, t.destination.values)):
        so = zn[o]
        s = so[int(u1[i] * len(so))]
        cand = zn[d]
        if s in cand:
            if len(cand) == 1 and len(so) > 1:
                # the destination has only this node: start from another node of the origin zone instead
                so = so[so != s]
                s = so[int(u1[i] * len(so))]
            else:
                cand = cand[cand != s]
        start[i] = s
        if len(cand):
            end[i] = cand[int(u2[i] * len(cand))]
    t["start"], t["end"] = start, end
    nodrop = t.end < 0
    dd = t[nodrop].assign(reason="no_distinct_end_node")
    dropped.append(dd)
    t = t[~nodrop].reset_index(drop=True)
    same = dd.origin == dd.destination
    print(f"  removed, no end node other than the start node: {len(dd):,} "
          f"(same zone {int(same.sum()):,} in {dd[same].origin.nunique()} zones; different zones {int((~same).sum()):,})")
    if len(dd):
        print(f"    by mode: {dd.trip_mode.value_counts().to_dict()}")
        print(f"    top zones (origin): {dd.origin.value_counts().head(8).to_dict()}")
    n = len(t)

    mean_income = t.income.mean()
    print(f"  mean_income (per worker, trips written) {mean_income:,.1f}")
    n_floor = int((t.income < INCOME_FLOOR).sum())
    print(f"  cost beta: income floored at {INCOME_FLOOR:,} for {n_floor:,} requests (income column unchanged)")
    beta_income = np.maximum(t.income.values, INCOME_FLOOR)
    for col, mean, sd, floor in BETAS:
        m = 0.5 * mean_income / beta_income if mean is None else mean
        t[col] = np.maximum(rng.normal(m, sd, n), floor)
    t["bt_t_fr"] = t.bt_c_gas
    cols = list(OUT_COLS)
    if cfg["parking"]:
        raw = PARK_LOC + rng.lognormal(mean=np.log(PARK_SCALE), sigma=PARK_S, size=n)
        t["parking_dur_min"] = np.maximum(raw, 0.0)
        cols.append("parking_dur_min")
        print(f"  parking_dur_min: {int((raw < 0).sum())} negative draws set to 0; "
              f"p5/median/mean/p95/p99 {np.percentile(t.parking_dur_min, 5):.1f} / "
              f"{t.parking_dur_min.median():.1f} / {t.parking_dur_min.mean():.1f} / "
              f"{np.percentile(t.parking_dur_min, 95):.1f} / {np.percentile(t.parking_dur_min, 99):.1f}")
    debug_idx = rng.choice(n, size=int(round(n * DEBUG_FRAC)), replace=False)
    t["is_debug"] = False
    t.loc[debug_idx, "is_debug"] = True

    # ---- order and IDs
    t = t.sort_values(["rq_time", "trip_id"], kind="stable").reset_index(drop=True)
    t["request_id"] = np.arange(n)

    # ---- checks
    if (t.start == t.end).any():
        sys.exit("ERROR: a request has start == end.")
    if not all(s in zn[o] and e in zn[d] for s, e, o, d in zip(t.start, t.end, t.origin, t.destination)):
        sys.exit("ERROR: a start/end node is not a node of its zone.")
    if t[cols].isna().any().any():
        sys.exit("ERROR: NaN in the output columns.")
    if not t.rq_time.is_monotonic_increasing:
        sys.exit("ERROR: rq_time not sorted.")
    if not set(t.transit_pass.unique()) <= {0, 1}:
        sys.exit("ERROR: transit_pass is not 0/1.")

    # ---- write
    os.makedirs(out_dir, exist_ok=True)
    full = t[cols]
    full.to_csv(files["full"], index=False)
    full[t.is_debug].to_csv(files["debug"], index=False)
    t[TRACE_COLS].to_csv(files["trace"], index=False)
    dr = pd.concat(dropped, ignore_index=True)
    dr[TRACE_COLS[1:] + ["reason"]].sort_values("trip_id").to_csv(files["dropped"], index=False)

    # ---- report
    print(f"  written: {n:,} requests ({len(cols)} columns), debug {int(t.is_debug.sum()):,}, dropped {len(dr):,}")
    print(f"  rq_time s min/max {t.rq_time.min()} / {t.rq_time.max()}; past 86400 s: {int((t.rq_time >= 86400).sum())}")
    print(f"  by period: {t.trip_period.value_counts().reindex(['EA', 'AM', 'MD', 'PM', 'EV']).to_dict()}")
    print(f"  by mode: {t.trip_mode.value_counts().to_dict()}")
    print(f"  by purpose: {t.purpose.value_counts().to_dict()}")
    print(f"  transit_pass: {t.transit_pass.value_counts().sort_index().to_dict()}; "
          f"auto_available: {t.auto_available.value_counts().to_dict()}; "
          f"num_autos: {t.num_autos.value_counts().sort_index().to_dict()}")
    print(f"  income per worker: mean {t.income.mean():,.0f}, median {t.income.median():,.0f}, "
          f"min {t.income.min():,.0f}")
    q = t[[c for c, *_ in BETAS]].describe(percentiles=[0.05, 0.5, 0.95]).T[["mean", "5%", "50%", "95%", "max"]]
    print("  betas:\n" + q.round(4).to_string().replace("\n", "\n    ").join(["    ", ""]))
    for k in ("full", "debug", "trace", "dropped"):
        print(f"  wrote {files[k]}")


def main():
    ap = argparse.ArgumentParser(description="ABM3 demand files for the MGRA15 networks.")
    ap.add_argument("--areas", nargs="+", choices=list(AREAS), default=list(AREAS))
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    # refuse to overwrite before the long read
    plan = {}
    for name in args.areas:
        d, files = out_files(args.out_root, name)
        for p in files.values():
            if os.path.exists(p) and not args.overwrite:
                sys.exit(f"ERROR: {p} exists. Use --overwrite.")
        plan[name] = (d, files)

    mgra15_ids = set(pyogrio.read_dataframe(MGRA15_SHP, columns=["MGRA"], read_geometry=False)["MGRA"].astype(int))
    area_data = {name: load_area(name, AREAS[name], args.out_root, mgra15_ids) for name in args.areas}
    all_zones = set().union(*(z for z, _ in area_data.values()))

    t0 = time.time()
    hh = pd.read_csv(os.path.join(DEMAND_DIR, "final_households.csv"),
                     usecols=["household_id", "income", "num_workers", "auto_ownership"])
    hh = hh.rename(columns={"income": "hh_income"})
    if (hh.hh_income < 0).any():
        sys.exit("ERROR: negative household income.")
    per = pd.read_csv(os.path.join(DEMAND_DIR, "final_persons.csv"), usecols=["person_id", "transit_pass_ownership"])
    tpo = per.transit_pass_ownership
    if tpo.dtype == bool:
        tpo = tpo.astype(int)
    print(f"final_persons transit_pass_ownership values (region): {tpo.value_counts(dropna=False).to_dict()}")
    if tpo.isna().any() or not set(tpo.unique()) <= {0, 1}:
        sys.exit("ERROR: transit_pass_ownership is not 0/1.")
    per = pd.DataFrame({"person_id": per.person_id, "transit_pass": tpo.astype(int)})
    print(f"read households and persons ({time.time() - t0:.0f} s)")

    tr = read_trips(all_zones)
    for name in args.areas:
        zones, zn = area_data[name]
        d, files = plan[name]
        build_area(name, AREAS[name], tr, hh, per, zones, zn, files, d)


if __name__ == "__main__":
    main()
