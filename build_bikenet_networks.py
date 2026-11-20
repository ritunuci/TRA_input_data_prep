"""
Build the Lemon Grove and Downtown San Diego road networks for FleetPy from the SANDAG
ABM3 bike network (data_new/network/EMMEOutputs.gdb, layers Bike_Net_2022_Build and
Bike_Node_2022_Build). Bike_Net is built from the same Roads_All street data as the
MGRA13-era networks, so this keeps the old method on MGRA15-era data.

Decisions (user, 2026-11-19; see CLAUDE.md "Road network redo for MGRA15"):

  extent      links that intersect the union of the area's MGRA15 polygons
              (data_new/<area>/<prefix>_MGRA15.shp), then the largest connected component
  links       Func_Class 3-7 with AB_Lanes + BA_Lanes > 0 (bike-only links have no lanes),
              minus "PERSHING BIKEWAY". Func_Class 10 (connectors) and 0/-1 (paths,
              walkways, ped bridges, platform links) are dropped, except
                - FC 0/-1 links named like the streets in RESCUED_STREETS (short, miscoded
                  pieces of real Downtown streets): kept as normal streets;
                - FC 0/-1 links named ALLEY: kept as alleys.
  alleys      links named "ALLEY" and private driveways ("... DY") are routing only
  pickups     IS_DEMAND = 1 for streets with Func_Class 4-7 (and the rescued streets);
              private roads ("PRIVATE RD") count as streets. Func_Class 3 is routing only.
  one-way     ignored: the pipeline drives every link both ways (as before)
  speed       SPEED as in Bike_Net (mph); the pipeline applies its 10 mph floor

Cleaning (Claude): self-loops are dropped; where two links join the same two nodes, the
faster one (LENGTH / max(SPEED, 10)) is kept, because FleetPy cannot hold parallel edges.

Node -> zone (for the mapping step):
  - a node belongs to the area's MGRA15 polygon it lies in. A node on a boundary between
    zones goes to the lowest MGRA ID (reported). Nodes outside the area's zones get none.
  - every zone also gets its access node: the street node that its Bike_Net
    "MGRA Connector" (FC 10) attaches to. If that node is not in the final network, the
    nearest network node is used instead (reported). The mapping step uses the access node
    for zones that contain no pickup node.

Outputs, in <out-root>/<area>/network/ (default out-root: outputs_MGRA15):
  <prefix>_raw_edges.csv   one row per link, in the layout that
      pre_process.process_network.create_network_from_raw_edges() reads: FNODE, TNODE,
      FRXCOORD, FRYCOORD, TOXCOORD, TOYCOORD (EPSG:2230 ft), L_TRACT, R_TRACT (0),
      ROADSEGID, LENGTH (ft), SPEED (mph), RD20FULL; extra columns Func_Class, LINK_KIND
      (street / alley / driveway), IS_DEMAND.
      create_network_from_raw_edges() marks demand nodes from IS_DEMAND when the file has
      that column (else by SPEED == 20, the old rule).
  <prefix>_roads.shp       the same links as a line layer (EPSG:2230), for QGIS
  <prefix>_raw_nodes.csv   NODE, X, Y, IS_DEMAND (on a pickup link), MGRA (zone it lies in,
      empty if outside the area's zones), ON_BOUNDARY (lies on more than one zone)
  <prefix>_mgra_access.csv one row per area zone: MGRA, CONNECTOR_NODE (street end of the
      zone's MGRA Connector), ACCESS_NODE (node used), ACCESS_DIST_FT (0 unless replaced),
      N_PICKUP_NODES (pickup nodes inside the zone)

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_bikenet_networks.py
    python build_bikenet_networks.py --areas downtown_sd --out-root <dir> --overwrite
"""

import argparse
import os
import sys
from collections import Counter

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_NEW = os.path.join(HERE, "data_new")
GDB = os.path.join(DATA_NEW, "network", "EMMEOutputs.gdb")
DEFAULT_OUT_ROOT = os.path.join(HERE, "outputs_MGRA15")

AREAS = {
    "lemon_grove": dict(mgra15_shp=os.path.join(DATA_NEW, "lemon_grove", "Lemon_grove_MGRA15.shp"),
                        prefix="Lemon_grove"),
    "downtown_sd": dict(mgra15_shp=os.path.join(DATA_NEW, "Downtown_SD", "Downtown_SD_MGRA15.shp"),
                        prefix="Downtown_SD"),
}

ROUTING_FC = {3, 4, 5, 6, 7}
PICKUP_FC = {4, 5, 6, 7}
ODD_FC = {0, -1}
CONNECTOR_FC = 10
DROP_NAMES = {"PERSHING BIKEWAY"}
RESCUED_STREETS = {"02ND AV", "03RD AV", "04TH AV", "05TH AV", "06TH AV", "W GRAPE ST", "W HAWTHORN ST"}
SPEED_FLOOR_MPH = 10.0  # same floor as create_network_from_raw_edges()

RAW_COLS = ["FNODE", "TNODE", "FRXCOORD", "FRYCOORD", "TOXCOORD", "TOYCOORD", "L_TRACT", "R_TRACT",
            "ROADSEGID", "LENGTH", "SPEED", "RD20FULL", "Func_Class", "LINK_KIND", "IS_DEMAND"]


def link_kind(name):
    name = (name or "").strip().upper()
    if "ALLEY" in name:
        return "alley"
    if name.split()[-1:] == ["DY"]:
        return "driveway"
    return "street"


def largest_component(a_nodes, b_nodes):
    """Union-find over undirected links; returns the node set of the largest component and all sizes."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(a_nodes, b_nodes):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    sizes = Counter(find(x) for x in list(parent))
    root = max(sizes, key=sizes.get)
    return {x for x in parent if find(x) == root}, sorted(sizes.values(), reverse=True)


def select_links(links):
    """Apply the link rules; returns the kept links with LINK_KIND and IS_DEMAND."""
    links = links.copy()
    name = links["RD20FULL"].fillna("").str.strip().str.upper()
    links["LINK_KIND"] = name.map(link_kind)
    has_lanes = (links["AB_Lanes"] + links["BA_Lanes"]) > 0
    fc = links["Func_Class"]
    rescued = fc.isin(ODD_FC) & name.isin(RESCUED_STREETS)
    odd_alley = fc.isin(ODD_FC) & (links["LINK_KIND"] == "alley")
    keep = has_lanes & (fc.isin(ROUTING_FC) | rescued | odd_alley) & ~name.isin(DROP_NAMES)
    out = links[keep].copy()
    out["IS_DEMAND"] = ((out["LINK_KIND"] == "street")
                        & (out["Func_Class"].isin(PICKUP_FC) | rescued[keep])).astype(int)
    return out, int(rescued[keep].sum()), int(odd_alley[keep].sum())


def build_area(name, cfg, nodes_xy, nodes_mgra, out_root, overwrite):
    out_dir = os.path.join(out_root, name, "network")
    pfx = os.path.join(out_dir, cfg["prefix"])
    outs = [pfx + "_raw_edges.csv", pfx + "_roads.shp", pfx + "_raw_nodes.csv", pfx + "_mgra_access.csv"]
    for p in outs:
        if os.path.exists(p) and not overwrite:
            sys.exit(f"ERROR: {p} exists. Use --overwrite.")

    print(f"\n== {name}")
    area = gpd.read_file(cfg["mgra15_shp"])[["MGRA", "geometry"]]
    area["MGRA"] = area["MGRA"].astype(int)
    poly = area.geometry.make_valid().union_all()
    bbox = tuple(area.total_bounds)
    allb = pyogrio.read_dataframe(GDB, layer="Bike_Net_2022_Build", bbox=bbox)
    if allb.crs != area.crs:
        sys.exit(f"ERROR: CRS differs: Bike_Net {allb.crs} vs area {area.crs}")
    touch = allb[allb.intersects(poly)]
    links, n_rescued, n_odd_alley = select_links(touch)
    print(f"  links touching area: {len(touch)} -> kept by link rules: {len(links)} "
          f"(incl. {n_rescued} rescued FC 0/-1 street pieces, {n_odd_alley} FC 0/-1 alleys)")
    dropped = touch.loc[~touch.index.isin(links.index)]
    print(f"  dropped by Func_Class: {dropped['Func_Class'].value_counts().sort_index().to_dict()}")

    self_loops = links["A"] == links["B"]
    if self_loops.any():
        print(f"  dropping {int(self_loops.sum())} self-loop link(s): "
              f"{links.loc[self_loops, ['ROADSEGID', 'RD20FULL']].to_dict('records')}")
        links = links[~self_loops]

    links["LENGTH_FT"] = links["Distance"].astype(float)  # Bike_Net Distance is in feet (= geometry length)
    links["TT"] = links["LENGTH_FT"] / links["SPEED"].clip(lower=SPEED_FLOOR_MPH)
    links["PAIR"] = [(min(a, b), max(a, b)) for a, b in zip(links["A"], links["B"])]
    links = links.sort_values(["PAIR", "TT", "ROADSEGID"])
    par = links["PAIR"].duplicated(keep="first")
    if par.any():
        kept = links[links["PAIR"].isin(links.loc[par, "PAIR"]) & ~par]
        print(f"  parallel links: dropping {int(par.sum())} slower link(s) "
              f"{links.loc[par, ['ROADSEGID', 'RD20FULL', 'SPEED', 'LENGTH_FT']].to_dict('records')}; "
              f"kept {kept[['ROADSEGID', 'RD20FULL', 'SPEED', 'LENGTH_FT']].to_dict('records')}")
        links = links[~par]

    keep_nodes, sizes = largest_component(links["A"], links["B"])
    n_before = len(links)
    links = links[links["A"].isin(keep_nodes)].copy()
    print(f"  largest component: {len(links)} of {n_before} links, {len(keep_nodes)} nodes "
          f"(components: {len(sizes)}, sizes {sizes[:6]})")

    missing = keep_nodes - set(nodes_xy.index)
    if missing:
        sys.exit(f"ERROR: {len(missing)} link nodes not in Bike_Node_2022_Build, e.g. {sorted(missing)[:5]}")

    out = pd.DataFrame({
        "FNODE": links["A"].astype(int).values,
        "TNODE": links["B"].astype(int).values,
        "FRXCOORD": nodes_xy.loc[links["A"], "XCOORD"].values,
        "FRYCOORD": nodes_xy.loc[links["A"], "YCOORD"].values,
        "TOXCOORD": nodes_xy.loc[links["B"], "XCOORD"].values,
        "TOYCOORD": nodes_xy.loc[links["B"], "YCOORD"].values,
        "L_TRACT": 0,
        "R_TRACT": 0,
        "ROADSEGID": links["ROADSEGID"].astype(int).values,
        "LENGTH": links["LENGTH_FT"].round(3).values,
        "SPEED": links["SPEED"].values,
        "RD20FULL": links["RD20FULL"].fillna("").values,
        "Func_Class": links["Func_Class"].astype(int).values,
        "LINK_KIND": links["LINK_KIND"].values,
        "IS_DEMAND": links["IS_DEMAND"].values,
    }, columns=RAW_COLS).sort_values(["FNODE", "TNODE", "ROADSEGID"]).reset_index(drop=True)
    if not out["ROADSEGID"].is_unique:
        sys.exit("ERROR: ROADSEGID is not unique within the area.")

    print(f"  Func_Class: {out.Func_Class.value_counts().sort_index().to_dict()}  "
          f"LINK_KIND: {out.LINK_KIND.value_counts().to_dict()}")
    print(f"  SPEED: {out.SPEED.value_counts().sort_index().to_dict()}  "
          f"(below {SPEED_FLOOR_MPH:.0f} mph -> floored by the pipeline: {int((out.SPEED < SPEED_FLOOR_MPH).sum())})")

    # nodes: pickup flag and zone
    pick = out[out.IS_DEMAND == 1]
    pickup_nodes = set(pick.FNODE) | set(pick.TNODE)
    node_ids = sorted(keep_nodes)
    pts = gpd.GeoDataFrame({"NODE": node_ids},
                           geometry=gpd.points_from_xy(nodes_xy.loc[node_ids, "XCOORD"],
                                                       nodes_xy.loc[node_ids, "YCOORD"]), crs=area.crs)
    j = gpd.sjoin(pts, area, predicate="intersects", how="left")
    n_zones = j.groupby("NODE")["MGRA"].nunique()
    zone = j.groupby("NODE")["MGRA"].min()
    nodes_out = pd.DataFrame({
        "NODE": node_ids,
        "X": nodes_xy.loc[node_ids, "XCOORD"].values,
        "Y": nodes_xy.loc[node_ids, "YCOORD"].values,
        "IS_DEMAND": [int(n in pickup_nodes) for n in node_ids],
        "MGRA": zone.reindex(node_ids).astype("Int64").values,
        "ON_BOUNDARY": (n_zones.reindex(node_ids) > 1).astype(int).values,
    })
    print(f"  nodes: {len(nodes_out)}; pickup nodes: {int(nodes_out.IS_DEMAND.sum())}; "
          f"outside the area's zones: {int(nodes_out.MGRA.isna().sum())}; "
          f"on a zone boundary (given the lowest MGRA): {int(nodes_out.ON_BOUNDARY.sum())}")

    # zone access nodes from the MGRA connectors
    # (filtering with pyogrio's `where` together with `bbox` returned no rows here, so filter in pandas)
    conn = pyogrio.read_dataframe(GDB, layer="Bike_Net_2022_Build", read_geometry=False,
                                  bbox=tuple(area.buffer(1000).total_bounds),
                                  columns=["A", "B", "RD20FULL", "Func_Class"])
    conn = conn[(conn["Func_Class"] == CONNECTOR_FC) & (conn["RD20FULL"] == "MGRA Connector")].copy()
    conn["mA"] = nodes_mgra.reindex(conn["A"]).values
    conn["mB"] = nodes_mgra.reindex(conn["B"]).values
    conn["ZONE"] = np.where(conn["mA"] > 0, conn["mA"], conn["mB"])
    conn["STREET_NODE"] = np.where(conn["mA"] > 0, conn["B"], conn["A"])
    conn = conn[conn["ZONE"].isin(set(area["MGRA"]))]
    multi = conn.groupby("ZONE")["STREET_NODE"].nunique()
    if (multi > 1).any():
        print(f"  NOTE: {int((multi > 1).sum())} zones have more than one connector; the lowest node ID is used")
    access = conn.groupby("ZONE")["STREET_NODE"].min()
    no_conn = sorted(set(area["MGRA"]) - set(access.index))
    if no_conn:
        sys.exit(f"ERROR: {len(no_conn)} zones have no MGRA Connector, e.g. {no_conn[:5]}")

    net_xy = nodes_xy.loc[node_ids, ["XCOORD", "YCOORD"]].values
    tree = shapely.STRtree(shapely.points(net_xy))
    rows = []
    for mgra in sorted(area["MGRA"]):
        cnode = int(access[mgra])
        if cnode in keep_nodes:
            anode, dist = cnode, 0.0
        else:
            p = shapely.points(*nodes_xy.loc[cnode, ["XCOORD", "YCOORD"]].values)
            i, d = tree.query_nearest(p, return_distance=True)
            anode, dist = int(node_ids[i[0]]), float(d[0])
        rows.append((mgra, cnode, anode, round(dist, 1)))
    acc = pd.DataFrame(rows, columns=["MGRA", "CONNECTOR_NODE", "ACCESS_NODE", "ACCESS_DIST_FT"])
    npz = nodes_out[nodes_out.IS_DEMAND == 1].groupby("MGRA").size()
    acc["N_PICKUP_NODES"] = acc["MGRA"].map(npz).fillna(0).astype(int)
    replaced = acc[acc.ACCESS_DIST_FT > 0]
    print(f"  zones: {len(acc)}; with >= 1 pickup node inside: {int((acc.N_PICKUP_NODES > 0).sum())}; "
          f"using the access node instead: {int((acc.N_PICKUP_NODES == 0).sum())}")
    print(f"  access node not in the network (replaced by the nearest node): {len(replaced)}"
          + (f", distance ft: {replaced.ACCESS_DIST_FT.describe()[['min', '50%', 'max']].round(0).to_dict()}"
             if len(replaced) else ""))
    acc_pick = acc.ACCESS_NODE.map(nodes_out.set_index("NODE").IS_DEMAND)
    print(f"  access nodes that are also pickup nodes: {int(acc_pick.sum())} of {len(acc)}")

    os.makedirs(out_dir, exist_ok=True)
    out.to_csv(outs[0], index=False)
    geom = links.set_index(links["ROADSEGID"].astype(int)).loc[out["ROADSEGID"]].geometry.values
    gpd.GeoDataFrame(out.drop(columns=["FRXCOORD", "FRYCOORD", "TOXCOORD", "TOYCOORD", "L_TRACT", "R_TRACT"]),
                     geometry=geom, crs=area.crs).to_file(outs[1])
    nodes_out.to_csv(outs[2], index=False)
    acc.to_csv(outs[3], index=False)
    for p in outs:
        print(f"  wrote {p}")


def main():
    ap = argparse.ArgumentParser(description="Build LG / DT road networks from Bike_Net_2022_Build (ABM3).")
    ap.add_argument("--areas", nargs="+", choices=list(AREAS), default=list(AREAS))
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    print(f"Reading Bike_Node_2022_Build from {GDB}")
    nodes = pyogrio.read_dataframe(GDB, layer="Bike_Node_2022_Build", read_geometry=False,
                                   columns=["NodeLev_ID", "MGRA", "XCOORD", "YCOORD"])
    if not nodes["NodeLev_ID"].is_unique:
        sys.exit("ERROR: NodeLev_ID is not unique in Bike_Node_2022_Build.")
    nodes = nodes.set_index("NodeLev_ID")

    for name in args.areas:
        build_area(name, AREAS[name], nodes[["XCOORD", "YCOORD"]], nodes["MGRA"], args.out_root, args.overwrite)


if __name__ == "__main__":
    main()
