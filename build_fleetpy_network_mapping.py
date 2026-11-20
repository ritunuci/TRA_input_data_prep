"""
Turn the MGRA15 networks from build_bikenet_networks.py into FleetPy node/edge files and write the
zone -> pickup node mapping that the demand step uses.

Run build_bikenet_networks.py first. Per area, in <out-root>/<area>/ (default out-root: outputs_MGRA15):

network/ (inputs, from build_bikenet_networks.py)
  <prefix>_raw_edges.csv, <prefix>_raw_nodes.csv, <prefix>_mgra_access.csv

network/ (written here)
  pre_nodes.csv, pre_edges.csv   process_network.create_network_from_raw_edges():
                                 every link -> two edges (one-way ignored), distance ft -> m,
                                 travel_time = distance / max(SPEED, 10 mph) (free flow), pickup nodes from
                                 the IS_DEMAND column, FleetPy IDs 0..N-1 in order of Bike_Net node ID.
                                 is_stop_only is then set to False for every node (user decision
                                 2026-11-19), replacing the function's random 0.35%.
  nodes.csv, edges.csv           process_network.write_node_edge_from_pre_node_edge():
                                 the same network with FleetPy node IDs.

mapping/ (written here; node IDs are FleetPy IDs)
  Nodes_MGRA_mapping.csv   node, MGRA, raw_node, source. One row per (pickup node, zone):
                             source = inside: a pickup node that lies in the zone (point-in-polygon);
                             source = access: for a zone with no pickup node inside, the street node that
                                      its Bike_Net MGRA Connector attaches to (ACCESS_NODE in
                                      <prefix>_mgra_access.csv). Such a node can also be listed for the
                                      zone it lies in, so a node can appear in two rows.
                           Sorted by MGRA, node. The first two columns are the format that
                           generate_demand_files.create_tt_demand_file() reads.
  MGRA_Nodes_mapping.csv   MGRA, nodes (list of FleetPy node IDs), as in the old files.

Checks (stops on failure): node IDs 0..N-1; edges = 2 x links, no duplicate edges; one connected
network; pickup nodes equal the raw IS_DEMAND nodes; every zone in <prefix>_MGRA15.csv has >= 1 node;
every mapped node is a pickup node.

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_fleetpy_network_mapping.py
    python build_fleetpy_network_mapping.py --areas downtown_sd --overwrite
"""

import argparse
import csv
import os
import random
import sys

import numpy as np
import pandas as pd

import process_network as p_n

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_NEW = os.path.join(HERE, "data_new")
DEFAULT_OUT_ROOT = os.path.join(HERE, "outputs_MGRA15")
SEED = 42

AREAS = {
    "lemon_grove": dict(prefix="Lemon_grove",
                        zone_list=os.path.join(DATA_NEW, "lemon_grove", "Lemon_grove_MGRA15.csv")),
    "downtown_sd": dict(prefix="Downtown_SD",
                        zone_list=os.path.join(DATA_NEW, "Downtown_SD", "Downtown_SD_MGRA15.csv")),
}


def set_no_stop_only(pre_node_file):
    """Rewrite pre_nodes.csv with is_stop_only = False for every node (same csv layout as process_network)."""
    with open(pre_node_file, newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    n_before = sum(r["is_stop_only"] == "True" for r in rows)
    for r in rows:
        r["is_stop_only"] = "False"
    with open(pre_node_file, "w", newline="") as f:
        w = csv.writer(f, delimiter=",", quotechar="|", quoting=csv.QUOTE_MINIMAL)
        w.writerow(fields)
        for r in rows:
            w.writerow([r[k] for k in fields])
    return n_before


def n_components(edges):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(edges["from_node"], edges["to_node"]):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(x) for x in list(parent)})


def build_area(name, cfg, out_root, overwrite):
    net_dir = os.path.join(out_root, name, "network")
    map_dir = os.path.join(out_root, name, "mapping")
    pfx = os.path.join(net_dir, cfg["prefix"])
    raw_edges, raw_nodes, access_f = pfx + "_raw_edges.csv", pfx + "_raw_nodes.csv", pfx + "_mgra_access.csv"
    for p in (raw_edges, raw_nodes, access_f, cfg["zone_list"]):
        if not os.path.exists(p):
            sys.exit(f"ERROR: {p} not found (run build_bikenet_networks.py first).")
    pre_nodes, pre_edges = os.path.join(net_dir, "pre_nodes.csv"), os.path.join(net_dir, "pre_edges.csv")
    nodes_f, edges_f = os.path.join(net_dir, "nodes.csv"), os.path.join(net_dir, "edges.csv")
    n2m, m2n = os.path.join(map_dir, "Nodes_MGRA_mapping.csv"), os.path.join(map_dir, "MGRA_Nodes_mapping.csv")
    for p in (pre_nodes, pre_edges, nodes_f, edges_f, n2m, m2n):
        if os.path.exists(p) and not overwrite:
            sys.exit(f"ERROR: {p} exists. Use --overwrite.")

    print(f"\n== {name}")
    # nothing random is kept (stop-only is reset below), but seed anyway so the run is fully repeatable
    random.seed(SEED)
    np.random.seed(SEED)

    p_n.create_network_from_raw_edges(raw_edges, pre_nodes, pre_edges)
    n_stop = set_no_stop_only(pre_nodes)
    print(f"  is_stop_only set to False for all nodes (the function had drawn {n_stop})")
    nodes_fleetpy, demand_nodes = p_n.write_node_edge_from_pre_node_edge(nodes_f, edges_f, pre_nodes, pre_edges)

    # ---- network checks
    raw = pd.read_csv(raw_edges)
    rn = pd.read_csv(raw_nodes)
    nodes = pd.read_csv(nodes_f)
    edges = pd.read_csv(edges_f)
    if list(nodes["node_index"]) != list(range(len(nodes))):
        sys.exit("ERROR: node_index is not 0..N-1 in order.")
    if len(nodes) != len(rn) or set(nodes_fleetpy) != set(rn["NODE"]):
        sys.exit(f"ERROR: {len(nodes)} FleetPy nodes vs {len(rn)} raw nodes.")
    if len(edges) != 2 * len(raw) or edges.duplicated(["from_node", "to_node"]).any():
        sys.exit("ERROR: edges are not exactly two per link without duplicates.")
    if nodes["is_stop_only"].astype(str).ne("False").any():
        sys.exit("ERROR: some node is still stop-only.")
    if n_components(edges) != 1:
        sys.exit("ERROR: the network is not connected.")
    raw_pick = set(rn.loc[rn.IS_DEMAND == 1, "NODE"])
    if {nodes_fleetpy[n] for n in raw_pick} != set(demand_nodes):
        sys.exit("ERROR: pipeline pickup nodes differ from the raw IS_DEMAND nodes.")
    print(f"  nodes {len(nodes)}, edges {len(edges)} (2 x {len(raw)} links), pickup nodes {len(demand_nodes)}, "
          f"travel_time s min/median/max {edges.travel_time.min():.1f} / {edges.travel_time.median():.1f} / "
          f"{edges.travel_time.max():.1f}")

    # ---- zone -> pickup node mapping
    zones = set(pd.read_csv(cfg["zone_list"])["MGRA"].astype(int))
    acc = pd.read_csv(access_f)
    if set(acc["MGRA"]) != zones:
        sys.exit("ERROR: zones in the access file differ from the study-area list.")
    inside = rn[(rn.IS_DEMAND == 1) & rn.MGRA.notna()]
    rows = [(int(n), int(m), "inside") for n, m in zip(inside.NODE, inside.MGRA)]
    n_inside = inside.groupby("MGRA").size()
    if not acc.set_index("MGRA")["N_PICKUP_NODES"].eq(n_inside.reindex(acc.MGRA).fillna(0).astype(int).values).all():
        sys.exit("ERROR: N_PICKUP_NODES in the access file differs from the raw nodes.")
    need = acc[acc.N_PICKUP_NODES == 0]
    not_pick = need[~need.ACCESS_NODE.isin(raw_pick)]
    if len(not_pick):
        sys.exit(f"ERROR: access nodes that are not pickup nodes: {not_pick[['MGRA', 'ACCESS_NODE']].to_dict('records')}")
    rows += [(int(n), int(m), "access") for n, m in zip(need.ACCESS_NODE, need.MGRA)]

    mp = pd.DataFrame(rows, columns=["raw_node", "MGRA", "source"])
    mp["node"] = mp["raw_node"].map(nodes_fleetpy)
    mp = mp[["node", "MGRA", "raw_node", "source"]].sort_values(["MGRA", "node"]).reset_index(drop=True)
    if set(mp.MGRA) != zones:
        sys.exit(f"ERROR: {len(zones - set(mp.MGRA))} zones have no node.")
    if not set(mp.node) <= set(demand_nodes):
        sys.exit("ERROR: a mapped node is not a pickup node.")

    os.makedirs(map_dir, exist_ok=True)
    mp.to_csv(n2m, index=False)
    with open(m2n, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["MGRA", "nodes"])
        w.writeheader()
        for m, g in mp.groupby("MGRA", sort=True):
            w.writerow({"MGRA": int(m), "nodes": [int(x) for x in g["node"]]})

    per_zone = mp.groupby("MGRA").size()
    print(f"  mapping: {len(mp)} rows, {mp.node.nunique()} distinct nodes, {len(per_zone)} zones "
          f"(inside {int((mp.source == 'inside').sum())} rows, access {int((mp.source == 'access').sum())} rows); "
          f"nodes per zone min/median/max {per_zone.min()} / {int(per_zone.median())} / {per_zone.max()}; "
          f"zones with a single node: {int((per_zone == 1).sum())}; "
          f"nodes listed for two zones: {int(mp.node.duplicated().sum())}")
    for p in (pre_nodes, pre_edges, nodes_f, edges_f, n2m, m2n):
        print(f"  wrote {p}")


def main():
    ap = argparse.ArgumentParser(description="FleetPy node/edge files and zone -> node mapping for the MGRA15 networks.")
    ap.add_argument("--areas", nargs="+", choices=list(AREAS), default=list(AREAS))
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    for name in args.areas:
        build_area(name, AREAS[name], args.out_root, args.overwrite)


if __name__ == "__main__":
    main()
