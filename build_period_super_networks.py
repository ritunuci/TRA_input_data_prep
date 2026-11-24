"""
Build MTSM's super-networks for each ABM3 time period (one set per period), with build_super_network.py.

Run after build_period_travel_times.py and build_frt_network.py. Decisions (user, 2026-11-23):
  - Freeways, freeway connectors and ramps are car only. Walk links (type 0) and microtransit links (type 4) use
    only the street, alley and driveway edges ("local" edges, from <prefix>_edge_kind.csv); the car network
    (auto_edges, type 7) uses every edge. Virtual stops are drawn from all street nodes (the nodes on a local edge);
    FRT stops are joined to street nodes. Freeway nodes keep their numbers in every layer but get no link there.
  - One super-network per period. Per period only the car times change: microtransit in-vehicle (type 4) = the
    period's car time x MICRO_TIME_FACTOR, car network (type 7) = the period's car time. The period times are
    tt_EA ... tt_EV of <prefix>_link_period_tt.csv (ABM3 loaded link times). Walk (0), FRT (1), FRT waiting (2) and
    microtransit waiting (5) are the same in every period.
  - Full grid: micro = HEADWAYS x SHARES, non_micro = HEADWAYS, micro_only = SHARES, plus auto_edges.
  - Walk speed 1.6 m/s; virtual stops drawn with seed 42 (the same stops in every period and headway; with one seed
    a smaller share is a subset of a larger one); microtransit factor 1.4 and waiting time 180 s (the builder's
    defaults).

Inputs per area, in <out-root>/<area>/:
  network/nodes.csv, network/edges.csv, network/<prefix>_edge_kind.csv, network/<prefix>_node_kind.csv,
  network/<prefix>_link_period_tt.csv, frt/frt_stops.csv, frt/frt_edges.csv

Outputs in <out-root>/<area>/super_network/ (MTSM's file names with the period appended; proposed names):
  initial_super_network_micro_hw_<h>_virstop_<s>_<P>.csv   from_node,to_node,distance,travel_time,link_type,route
  initial_super_network_non_micro_hw_<h>_<P>.csv
  initial_super_network_micro_only_virstop_<s>_<P>.csv
  auto_edges_<P>.csv                                       the car network (type 7, every road edge)
  super_network_info.csv                                   setting,value: num_walk_nodes, microtransit_node_offset,
                                                           ... for mtsm_constant_configurations.csv
  inputs/local_edges.csv          from_node,to_node: the edges for walking and microtransit
  inputs/road_edges_<P>.csv       from_node,to_node,distance,travel_time (the period's car time, s)
  inputs/virtual_stops_<s>.csv    node_index: the virtual stops of each share
  (P = EA, AM, MD, PM, EV; h = headway in min; s = virtual-stop share in %)

Checks (stops on failure): link_period_tt.csv and edge_kind.csv in edges.csv order; local edges two-way and their
nodes = the street nodes; per file no duplicate (from, to); walk and microtransit links only on local edges; car
network = every edge; virtual stops are street nodes; every FRT stop joined at 0 m; walk, FRT and waiting links and
the node numbering the same in every period.

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_period_super_networks.py
    python build_period_super_networks.py --areas downtown_sd --overwrite
"""

import argparse
import contextlib
import io
import os
import shutil
import sys

import pandas as pd

import build_super_network as bsn

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT_ROOT = os.path.join(HERE, "outputs_MGRA15")

PERIODS = {"EA": "3:00-6:00", "AM": "6:00-9:00", "MD": "9:00-15:30", "PM": "15:30-19:00", "EV": "19:00-3:00"}
HEADWAYS = [10, 15, 20, 30, 60]  # min
SHARES = [50, 75, 100]  # % of the street nodes that are virtual stops
SEED = 42
WALK_SPEED = 1.6  # m/s
MICRO_TIME_FACTOR = 1.4
MICRO_WAIT_S = 180
LOCAL_KINDS = ("street", "alley", "driveway")  # link_kind values walkers and microtransit may use

AREAS = {"lemon_grove": dict(prefix="Lemon_grove"), "downtown_sd": dict(prefix="Downtown_SD")}


def write_quiet(path, links):
    with contextlib.redirect_stdout(io.StringIO()):
        bsn.write_links(path, links)


def check_links(links, local, n_edges, variant, what):
    pairs = [(l["from_node"], l["to_node"]) for l in links]
    if len(pairs) != len(set(pairs)):
        sys.exit(f"ERROR: {what}: duplicate (from_node, to_node) pairs")
    walk = {(l["from_node"], l["to_node"]) for l in links if l["link_type"] == bsn.WALK}
    if walk != local:
        sys.exit(f"ERROR: {what}: walk links are not exactly the local edges")
    n_micro = sum(l["link_type"] == bsn.MICRO for l in links)
    if n_micro != (len(local) if variant != "non_micro" else 0):
        sys.exit(f"ERROR: {what}: {n_micro} microtransit links, expected {len(local)}")


def build_area(name, cfg, out_root, overwrite):
    pfx = cfg["prefix"]
    net, frt = os.path.join(out_root, name, "network"), os.path.join(out_root, name, "frt")
    out = os.path.join(out_root, name, "super_network")
    inp = os.path.join(out, "inputs")
    files = {k: os.path.join(net, f) for k, f in [("nodes", "nodes.csv"), ("edges", "edges.csv"),
                                                  ("kind", f"{pfx}_edge_kind.csv"), ("nkind", f"{pfx}_node_kind.csv"),
                                                  ("tt", f"{pfx}_link_period_tt.csv")]}
    files.update(stops=os.path.join(frt, "frt_stops.csv"), frt_edges=os.path.join(frt, "frt_edges.csv"))
    for p in files.values():
        if not os.path.exists(p):
            sys.exit(f"ERROR: {p} not found (run steps 2-4 and build_frt_network.py first).")
    if os.path.exists(out):
        if not overwrite:
            sys.exit(f"ERROR: {out} exists. Use --overwrite.")
        shutil.rmtree(out)
    os.makedirs(inp)
    print(f"\n######## {name}")

    # local edges and per-period road edges (edges.csv order)
    edges = pd.read_csv(files["edges"])
    kind = pd.read_csv(files["kind"])
    tt = pd.read_csv(files["tt"])
    for d, what in [(kind, "edge_kind.csv"), (tt, "link_period_tt.csv")]:
        if not (d[["from_node", "to_node"]].to_numpy() == edges[["from_node", "to_node"]].to_numpy()).all():
            sys.exit(f"ERROR: {what} is not in edges.csv order")
    is_local = kind.link_kind.isin(LOCAL_KINDS)
    local_df = edges.loc[is_local, ["from_node", "to_node"]]
    local = set(zip(local_df.from_node, local_df.to_node))
    if any((b, a) not in local for a, b in local):
        sys.exit("ERROR: a local edge has no reverse edge")
    nkind = pd.read_csv(files["nkind"])
    street_nodes = set(nkind.loc[nkind.kind == "street", "node"])
    if bsn.nodes_of(local) != street_nodes:
        sys.exit("ERROR: the nodes on local edges are not exactly the street nodes")
    local_path = os.path.join(inp, "local_edges.csv")
    local_df.to_csv(local_path, index=False)
    road_paths = {}
    for p in PERIODS:
        road_paths[p] = os.path.join(inp, f"road_edges_{p}.csv")
        pd.DataFrame({"from_node": edges.from_node, "to_node": edges.to_node, "distance": edges.distance,
                      "travel_time": tt[f"tt_{p}"]}).to_csv(road_paths[p], index=False)
    print(f" road edges {len(edges)}: local (walk + microtransit) {len(local)}, car only {len(edges) - len(local)}; "
          f"nodes {len(nkind)} (street {len(street_nodes)}, freeway {len(nkind) - len(street_nodes)})")

    stops, frt_edges = bsn.read_frt(files["stops"], files["frt_edges"])
    first = {}  # period-independent links of the first period, per file key
    info = None
    n_files = 0
    for p in PERIODS:
        nodes, road_edges = bsn.read_road(files["nodes"], road_paths[p])
        loc = bsn.read_local_edges(local_path, road_edges)
        allowed = bsn.nodes_of(loc)
        virtual = {s: bsn.virtual_stops(nodes, None, s, SEED, allowed) for s in SHARES}
        if p == "EA":
            for s in SHARES:
                pd.DataFrame({"node_index": sorted(virtual[s])}).to_csv(
                    os.path.join(inp, f"virtual_stops_{s}.csv"), index=False)
            if not all(virtual[a] <= virtual[b] for a, b in zip(SHARES, SHARES[1:])):
                sys.exit("ERROR: a smaller virtual-stop share is not a subset of a larger one")
            if not set().union(*virtual.values()) <= street_nodes:
                sys.exit("ERROR: a virtual stop is not a street node")
        runs = [("micro", h, s, f"initial_super_network_micro_hw_{h}_virstop_{s}_{p}.csv")
                for h in HEADWAYS for s in SHARES]
        runs += [("non_micro", h, None, f"initial_super_network_non_micro_hw_{h}_{p}.csv") for h in HEADWAYS]
        runs += [("micro_only", HEADWAYS[0], s, f"initial_super_network_micro_only_virstop_{s}_{p}.csv")
                 for s in SHARES]
        for variant, h, s, fname in runs:
            links, _, summary = bsn.build(nodes, road_edges, stops, frt_edges, variant, h,
                                          virtual[s] if s is not None else set(), WALK_SPEED, MICRO_TIME_FACTOR,
                                          MICRO_WAIT_S, False, loc)
            check_links(links, loc, len(road_edges), variant, fname)
            if summary["largest stop -> walk node distance"] != 0:
                sys.exit(f"ERROR: {fname}: an FRT stop is not at its street node")
            fixed = [l for l in links if l["link_type"] not in (bsn.MICRO,)]
            key = fname[: -len(f"_{p}.csv")]
            if p == "EA":
                first[key] = fixed
            elif fixed != first[key]:
                sys.exit(f"ERROR: {fname}: walk / FRT / waiting links differ from EA")
            if variant == "micro" and h == 10 and s == 75 and p == "EA":
                info = summary
            write_quiet(os.path.join(out, fname), links)
            n_files += 1
        car = bsn.car_links(road_edges)
        if len(car) != len(edges):
            sys.exit("ERROR: the car network does not have every edge")
        write_quiet(os.path.join(out, f"auto_edges_{p}.csv"), car)
        n_files += 1
        print(f" {p}: {len(runs) + 1} files")

    n_walk, offset = info["num_walk_nodes"], info["microtransit_node_offset"]
    rows = [("num_walk_nodes", n_walk), ("microtransit_node_offset", offset),
            ("last_walk_node", n_walk - 1), ("first_frt_stop", n_walk), ("last_frt_stop", offset - 1),
            ("last_microtransit_node", offset + n_walk - 1), ("frt_stops", offset - n_walk),
            ("street_nodes", len(street_nodes)), ("freeway_nodes", n_walk - len(street_nodes)),
            ("road_edges", len(edges)), ("local_edges", len(local)),
            ("walk_speed_mps", WALK_SPEED), ("micro_time_factor", MICRO_TIME_FACTOR), ("micro_wait_s", MICRO_WAIT_S),
            ("virtual_stop_seed", SEED), ("headways_min", " ".join(map(str, HEADWAYS))),
            ("virtual_stop_shares", " ".join(map(str, SHARES)))]
    rows += [(f"virtual_stops_{s}", len(bsn.virtual_stops(nodes, None, s, SEED, bsn.nodes_of(local))))
             for s in SHARES]
    rows += [(f"period_{p}", PERIODS[p]) for p in PERIODS]
    pd.DataFrame(rows, columns=["setting", "value"]).to_csv(os.path.join(out, "super_network_info.csv"), index=False)
    print(f" num_walk_nodes {n_walk}, microtransit_node_offset {offset}, FRT stops {offset - n_walk}; virtual stops "
          + ", ".join(f"{s}% {v}" for (k, v) in rows if k.startswith("virtual_stops_") for s in [k.split('_')[-1]]))
    print(f" links per type (micro hw 10, 75 %): {info['links per type']}")
    print(f" wrote {n_files} networks + super_network_info.csv + inputs/ to {out}")


def main():
    ap = argparse.ArgumentParser(description="MTSM super-networks per ABM3 period (see the module docstring).")
    ap.add_argument("--areas", nargs="+", choices=list(AREAS), default=list(AREAS))
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    for name in args.areas:
        build_area(name, AREAS[name], args.out_root, args.overwrite)


if __name__ == "__main__":
    main()
