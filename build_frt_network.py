"""
Build the FRT (fixed-route transit) stop and link files that MTSM's build_super_network.py reads, for the MGRA15
study areas, from the SANDAG transit network in data_new/network/EMMEOutputs.gdb (the ABM3 run's transit input,
scenario year 2022).

Run build_bikenet_networks.py and build_fleetpy_network_mapping.py first (the stops are joined to their nodes).

Inputs
  EMMEOutputs.gdb  trrt (route variants: Route_Name = route x 1000 + direction x 100 + configuration, Mode,
                   headways, fare), TransitRoute (Route, Direction, Operator, Start, Stop, route line),
                   trstop (stops of each variant in order: Stop_ID, Node = TNED node, Milepost in mi, StopName),
                   TNED_HwyNodes / TNED_RailNodes (stop node coordinates, EPSG:2230 ft)
  data_new/<area>/<prefix>_MGRA15.shp                     study area (union of its MGRA15 zones)
  <out-root>/<area>/network/nodes.csv, <prefix>_node_kind.csv  our road network (FleetPy IDs)

Rules (user decisions 2026-11-23)
  - Route variants: all variants in the gdb except the area's EXCLUDE list (one variant per route and direction;
    the script stops if two variants of a route and direction both get links).
  - A stop is in the area if its TNED node lies within TOLERANCE_FT (50 ft) of the MGRA15 union (stops on boundary
    streets count).
  - Links: one per pair of consecutive stops of a variant (Stop_ID order = Milepost order) with both stops in the
    area. A variant that leaves the area and comes back gets no link across the gap.
    distance = Milepost difference x 1609.344 m; travel_time = distance / 30 mph (bus and rail alike).
    If the same (from, to) pair occurs twice in one variant (loops), the shorter link is kept and reported.
  - One stop node per TNED node x route x direction: stop_id = (route x 10 + direction) x 100,000 + TNED node.
    route = the route number (both directions); a loop that revisits a TNED node reuses its stop node.
  - Each stop is joined to the nearest street node of our network (node_kind street, split nodes allowed, never a
    freeway node; a tie goes to the smaller node number). pos_x / pos_y = that street node's coordinates, so that
    MTSM's nearest-walk-node join finds exactly that node. All stops are kept; joins over JOIN_REPORT_FT are listed.

Outputs in <out-root>/<area>/frt/ (coordinates EPSG:2230 ft, as nodes.csv)
  frt_stops.csv   stop_id, pos_x, pos_y (MTSM), then route, direction, route_variant, tned_node, stop_name, mode,
                  street_node, join_dist_ft, tned_x, tned_y, dist_outside_ft. Only stops that have a link.
  frt_edges.csv   from_stop, to_stop, distance (m), travel_time (s), route (MTSM), then direction, route_variant,
                  mode, from_tned_node, to_tned_node, from_milepost, to_milepost (mi)
  frt_routes.csv  one row per kept variant: route, direction, route_variant, mode, mode_name, operator, start, end,
                  n_stops, n_links, length_m (inside the area), route_length_mi (whole variant), the ABM3 headways
                  (min) and hours, fare ($). For reference (MTSM's line cost); the network uses one headway.
  frt_stops.shp   QGIS: stops at their TNED location, with the joined street node
  frt_links.shp   QGIS: each link as the piece of the route line between its mileposts (straight line if the route
                  line can't be cut)

Checks (stops on failure, before writing): mileposts strictly increase along every variant; TNED nodes < 100,000
and all have coordinates; excluded variants exist; one variant per route and direction; every edge stop is in the
stop file; no duplicate (from, to); distance > 0; stop_id unique and < 2^31; every stop joined to a street node with
its coordinates.

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_frt_network.py
    python build_frt_network.py --areas downtown_sd --overwrite
"""

import argparse
import os
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_NEW = os.path.join(HERE, "data_new")
GDB = os.path.join(DATA_NEW, "network", "EMMEOutputs.gdb")
DEFAULT_OUT_ROOT = os.path.join(HERE, "outputs_MGRA15")
CRS = "EPSG:2230"

TOLERANCE_FT = 50.0  # a stop within this distance of the study area counts as inside
SPEED_MPH = 30.0  # FRT run speed, bus and rail
M_PER_MI = 1609.344
MPS_PER_MPH = 0.44704
FT_PER_MI = 5280.0
JOIN_REPORT_FT = 300.0  # list stop -> street node joins longer than this
STOP_ID_BASE = 100_000

MODE_NAMES = {4: "Commuter Rail", 5: "Light Rail", 6: "Freeway Rapid", 7: "Arterial Rapid", 8: "Premium Express",
              9: "Express Bus", 10: "Local Bus"}

AREAS = {
    "lemon_grove": dict(prefix="Lemon_grove",
                        area_shp=os.path.join(DATA_NEW, "lemon_grove", "Lemon_grove_MGRA15.shp"),
                        exclude=[]),
    # Downtown: one variant per route and direction (user, 2026-11-23). Keeps 510108/510208, 20102/20202,
    # 120116/120216; drops the other configurations of 510, 20, 120 and the peak-only 110, 280, 290.
    "downtown_sd": dict(prefix="Downtown_SD",
                        area_shp=os.path.join(DATA_NEW, "Downtown_SD", "Downtown_SD_MGRA15.shp"),
                        exclude=[510119, 510219, 20114, 20214, 120112, 120212,
                                 110101, 110201, 280101, 280201, 290101, 290201]),
}

OUT_FILES = ["frt_stops.csv", "frt_edges.csv", "frt_routes.csv", "frt_stops.shp", "frt_links.shp"]


def read_transit():
    """route variants (trrt + TransitRoute), their lines {Route_Name: geometry} and their stops in order"""
    trrt = pyogrio.read_dataframe(GDB, layer="trrt", read_geometry=False)
    tr = pyogrio.read_dataframe(GDB, layer="TransitRoute")
    routes = trrt.merge(tr.drop(columns="geometry").rename(columns={"Mode": "Mode_tr"}),
                        left_on="Route_Name", right_on="RouteID", how="left", validate="1:1")
    if routes.Route.isna().any():
        sys.exit("ERROR: trrt variants without a TransitRoute row: %s" % routes.Route_Name[routes.Route.isna()].tolist())
    lines = dict(zip(tr.RouteID, tr.geometry))

    st = pyogrio.read_dataframe(GDB, layer="trstop", read_geometry=False)
    st["Milepost"] = pd.to_numeric(st.Milepost)
    st = st.merge(routes[["Route_ID", "Route_Name", "Route", "Direction", "Mode"]], on="Route_ID",
                  how="left", validate="m:1")
    if st.Route_Name.isna().any():
        sys.exit("ERROR: trstop rows whose Route_ID is not in trrt")
    xy = pd.concat([pyogrio.read_dataframe(GDB, layer=l, columns=["HNODE", "X_COORD", "Y_COORD"], read_geometry=False)
                    for l in ["TNED_HwyNodes", "TNED_RailNodes"]]).drop_duplicates("HNODE")
    st = st.merge(xy, left_on="Node", right_on="HNODE", how="left").drop(columns="HNODE")
    if st.X_COORD.isna().any():
        sys.exit("ERROR: stop nodes without TNED coordinates: %s" % sorted(st.Node[st.X_COORD.isna()].unique()))
    if (st.Node >= STOP_ID_BASE).any() or (st.Node <= 0).any():
        sys.exit("ERROR: TNED stop node outside 1 .. %d; the stop_id rule needs a new base" % (STOP_ID_BASE - 1))
    st = st.sort_values(["Route_ID", "Stop_ID"]).reset_index(drop=True)
    d = st.groupby("Route_ID").Milepost.diff()
    if (d <= 0).any():
        sys.exit("ERROR: Milepost does not increase along variants %s" % sorted(st.Route_Name[d <= 0].unique()))
    for c in ["Route", "Direction", "Mode"]:
        st[c] = st[c].astype(int)
    return routes, lines, st


def join_street_nodes(px, py, nodes):
    """nearest street node for each point; tie -> smaller node_index. Returns (node_index array, distance array)"""
    street = nodes[nodes.kind == "street"].reset_index(drop=True)
    tree = shapely.STRtree(shapely.points(street.pos_x.values, street.pos_y.values))
    (i_pt, i_node), dist = tree.query_nearest(shapely.points(px, py), return_distance=True, all_matches=True)
    m = pd.DataFrame({"pt": i_pt, "node": street.node_index.values[i_node], "dist": dist})
    m = m.sort_values(["pt", "node"]).drop_duplicates("pt")
    if len(m) != len(px):
        sys.exit("ERROR: a stop got no street node")
    return m.node.values, m.dist.values


def link_geometry(line, mp_from, mp_to, p_from, p_to):
    """route line between two mileposts; straight line if the route line is not one piece that starts at the stop"""
    if line is not None:
        g = shapely.line_merge(shapely.force_2d(line))
        if g.geom_type == "LineString":
            piece = shapely.ops.substring(g, mp_from * FT_PER_MI, mp_to * FT_PER_MI)
            if (piece.geom_type == "LineString" and shapely.Point(piece.coords[0]).distance(p_from) < TOLERANCE_FT
                    and shapely.Point(piece.coords[-1]).distance(p_to) < TOLERANCE_FT):
                return piece, "route"
    return shapely.LineString([p_from, p_to]), "straight"


def build_area(name, cfg, transit, out_root, overwrite):
    routes, lines, st_all = transit
    prefix = cfg["prefix"]
    out_dir = os.path.join(out_root, name, "frt")
    paths = {f: os.path.join(out_dir, f) for f in OUT_FILES}
    for p in paths.values():
        if os.path.exists(p) and not overwrite:
            sys.exit(f"ERROR: {p} exists. Use --overwrite.")
    print(f"\n######## {name}")

    excl = set(cfg["exclude"])
    missing = excl - set(routes.Route_Name)
    if missing:
        sys.exit(f"ERROR: excluded variants not in trrt: {sorted(missing)}")

    # stops in the area (TNED node within TOLERANCE_FT of the MGRA15 union)
    poly = gpd.read_file(cfg["area_shp"]).to_crs(CRS).geometry.make_valid().union_all()
    un = st_all.drop_duplicates("Node")
    near = shapely.intersects(shapely.points(un.X_COORD, un.Y_COORD), poly.buffer(TOLERANCE_FT + 1))
    un = un[near]
    dist_out = dict(zip(un.Node, shapely.distance(shapely.points(un.X_COORD, un.Y_COORD), poly)))
    st = st_all.copy()
    st["dist_out"] = st.Node.map(dist_out)
    st["inside"] = st.dist_out.le(TOLERANCE_FT)
    st_in = st[st.inside]
    print(f" TNED stop nodes in the area: {st_in.Node.nunique()} "
          f"({(st_in.drop_duplicates('Node').dist_out > 0).sum()} of them outside the zones, within {TOLERANCE_FT:.0f} ft)")

    # links between consecutive stops of a variant, both in the area
    s = st[~st.Route_Name.isin(excl)].copy()
    g = s.groupby("Route_ID")
    for c in ["Node", "Milepost", "inside", "X_COORD", "Y_COORD"]:
        s["next_" + c] = g[c].shift(-1)
    e = s[s.inside & s.next_inside.eq(True)].copy()
    e["to_node"] = e.next_Node.astype(int)
    sid = lambda route, d, node: (route * 10 + d) * STOP_ID_BASE + node
    e["from_stop"] = sid(e.Route, e.Direction, e.Node)
    e["to_stop"] = sid(e.Route, e.Direction, e.to_node)
    e["distance"] = (e.next_Milepost - e.Milepost) * M_PER_MI
    e["travel_time"] = e.distance / (SPEED_MPH * MPS_PER_MPH)
    e = e.rename(columns={"Route": "route", "Direction": "direction", "Route_Name": "route_variant", "Mode": "mode",
                          "Node": "from_tned_node", "to_node": "to_tned_node", "Milepost": "from_milepost",
                          "next_Milepost": "to_milepost"})
    n0 = len(e)
    dup = e[e.duplicated(["from_stop", "to_stop"], keep=False)]
    e = e.sort_values("distance").drop_duplicates(["from_stop", "to_stop"]).copy()
    if n0 != len(e):
        print(f" repeated (from, to) pairs: {n0 - len(e)} dropped (shorter kept):")
        print(dup[["route_variant", "from_tned_node", "to_tned_node", "distance"]].round(1).to_string(index=False))

    # variant checks
    nv = e.groupby(["route", "direction"]).route_variant.nunique()
    if (nv > 1).any():
        bad = e[e.set_index(["route", "direction"]).index.isin(nv[nv > 1].index)].route_variant.unique()
        sys.exit(f"ERROR: several variants of one route and direction have links: {sorted(bad)}. "
                 "Add the unwanted ones to EXCLUDE.")
    kept = set(e.route_variant)
    no_link = sorted(set(st_in.Route_Name) - kept - excl)
    print(f" variants with links: {len(kept)} (routes {sorted(set(e.route))})")
    print(f" excluded: {sorted(excl & set(st_in.Route_Name))}")
    print(f" variants with a stop in the area but no link: {no_link}")

    # stops: one per stop_id, joined to the nearest street node
    names = st.drop_duplicates(["Route_Name", "Node"]).set_index(["Route_Name", "Node"]).StopName
    a = e[["from_stop", "route", "direction", "route_variant", "from_tned_node", "mode"]].set_axis(
        ["stop_id", "route", "direction", "route_variant", "tned_node", "mode"], axis=1)
    b = e[["to_stop", "route", "direction", "route_variant", "to_tned_node", "mode"]].set_axis(a.columns, axis=1)
    stops = pd.concat([a, b]).drop_duplicates().sort_values("stop_id").reset_index(drop=True)
    if stops.stop_id.duplicated().any():
        sys.exit("ERROR: a stop_id stands for two TNED nodes or variants")
    stops["stop_name"] = [str(names.get((v, n), "")).strip() for v, n in zip(stops.route_variant, stops.tned_node)]
    xy = st.drop_duplicates("Node").set_index("Node")
    stops["tned_x"] = xy.X_COORD.reindex(stops.tned_node).values.astype(float)
    stops["tned_y"] = xy.Y_COORD.reindex(stops.tned_node).values.astype(float)
    stops["dist_outside_ft"] = stops.tned_node.map(dist_out).round(1)

    net = os.path.join(out_root, name, "network")
    nodes = pd.read_csv(os.path.join(net, "nodes.csv"))
    kind = pd.read_csv(os.path.join(net, f"{prefix}_node_kind.csv"))
    nodes = nodes.merge(kind[["node", "kind"]], left_on="node_index", right_on="node", how="left", validate="1:1")
    if nodes.kind.isna().any():
        sys.exit("ERROR: nodes.csv and node_kind.csv disagree")
    node_i, node_d = join_street_nodes(stops.tned_x.values, stops.tned_y.values, nodes)
    stops["street_node"] = node_i
    stops["join_dist_ft"] = np.round(node_d, 1)
    nxy = nodes.set_index("node_index")
    stops["pos_x"] = nxy.pos_x.reindex(node_i).values
    stops["pos_y"] = nxy.pos_y.reindex(node_i).values

    # checks
    if not (set(e.from_stop) | set(e.to_stop)) <= set(stops.stop_id):
        sys.exit("ERROR: an edge stop is not in the stop file")
    if e.duplicated(["from_stop", "to_stop"]).any() or (e.distance <= 0).any():
        sys.exit("ERROR: duplicate (from, to) or distance <= 0")
    if stops.stop_id.max() >= 2 ** 31:
        sys.exit("ERROR: stop_id too large")
    if not (nxy.kind.reindex(stops.street_node) == "street").all():
        sys.exit("ERROR: a stop joined to a non-street node")

    # tables
    stops = stops[["stop_id", "pos_x", "pos_y", "route", "direction", "route_variant", "tned_node", "stop_name", "mode",
                   "street_node", "join_dist_ft", "tned_x", "tned_y", "dist_outside_ft"]]
    e = e.sort_values(["route", "direction", "from_milepost"]).reset_index(drop=True)
    edges = e[["from_stop", "to_stop", "distance", "travel_time", "route", "direction", "route_variant", "mode",
               "from_tned_node", "to_tned_node", "from_milepost", "to_milepost"]].copy()
    edges["distance"] = edges.distance.round(3)
    edges["travel_time"] = edges.travel_time.round(3)

    rt = routes[routes.Route_Name.isin(kept)].copy()
    agg = edges.groupby("route_variant").agg(n_links=("from_stop", "size"), length_m=("distance", "sum"))
    agg["n_stops"] = stops.groupby("route_variant").size()
    rt = rt.merge(agg, left_on="Route_Name", right_index=True)
    rt["mode_name"] = rt.Mode.map(MODE_NAMES)
    rt["route_length_mi"] = (rt.SHAPE_Length / FT_PER_MI).round(3)
    rt["length_m"] = rt.length_m.round(1)
    rt = rt.rename(columns={"Route": "route", "Direction": "direction", "Route_Name": "route_variant", "Mode": "mode",
                            "Operator": "operator", "Start": "start", "Stop": "end",
                            "EarlyAM_Headway": "headway_ea", "AM_Headway": "headway_am",
                            "Midday_Headway": "headway_md", "PM_Headway": "headway_pm",
                            "Evening_Headway": "headway_ev", "EarlyAM_Hours": "hours_ea",
                            "Evening_Hours": "hours_ev", "Fare": "fare"})
    rt = rt[["route", "direction", "route_variant", "mode", "mode_name", "operator", "start", "end", "n_stops",
             "n_links", "length_m", "route_length_mi", "headway_ea", "headway_am", "headway_md", "headway_pm",
             "headway_ev", "hours_ea", "hours_ev", "fare"]].sort_values(["route", "direction"])
    rt[["route", "direction", "mode", "operator"]] = rt[["route", "direction", "mode", "operator"]].astype(int)

    # QGIS layers
    gs = gpd.GeoDataFrame(stops.rename(columns={"route_variant": "variant", "tned_node": "tned_node",
                                                "stop_name": "name", "street_node": "st_node",
                                                "join_dist_ft": "join_ft", "dist_outside_ft": "out_ft",
                                                "direction": "dir"})
                          [["stop_id", "route", "dir", "variant", "tned_node", "name", "mode", "st_node", "join_ft",
                            "out_ft"]],
                          geometry=gpd.points_from_xy(stops.tned_x, stops.tned_y), crs=CRS)
    geoms, src = [], []
    for r in e.itertuples():
        gl, how = link_geometry(lines.get(r.route_variant), r.from_milepost, r.to_milepost,
                                shapely.Point(r.X_COORD, r.Y_COORD), shapely.Point(r.next_X_COORD, r.next_Y_COORD))
        geoms.append(gl)
        src.append(how)
    gl = gpd.GeoDataFrame(edges[["from_stop", "to_stop", "route", "direction", "route_variant", "mode", "distance",
                                 "travel_time"]].set_axis(["from_stop", "to_stop", "route", "dir", "variant", "mode",
                                                           "dist_m", "tt_s"], axis=1).assign(geom_src=src),
                          geometry=geoms, crs=CRS)

    os.makedirs(out_dir, exist_ok=True)
    stops.to_csv(paths["frt_stops.csv"], index=False)
    edges.to_csv(paths["frt_edges.csv"], index=False)
    rt.to_csv(paths["frt_routes.csv"], index=False)
    gs.to_file(paths["frt_stops.shp"])
    gl.to_file(paths["frt_links.shp"])

    # report
    print(f" stop nodes {len(stops)} (TNED nodes {stops.tned_node.nunique()}, street nodes {stops.street_node.nunique()}),"
          f" links {len(edges)}, length {edges.distance.sum() / M_PER_MI:.2f} mi; link geometry "
          f"{pd.Series(src).value_counts().to_dict()}")
    j = stops.drop_duplicates("tned_node").join_dist_ft
    print(f" join distance per TNED node (ft): median {j.median():.0f}, p90 {j.quantile(.9):.0f}, max {j.max():.0f}")
    far = stops[stops.join_dist_ft > JOIN_REPORT_FT].drop_duplicates("tned_node")
    if len(far):
        print(f" joins over {JOIN_REPORT_FT:.0f} ft (kept):")
        print(far[["tned_node", "stop_name", "street_node", "join_dist_ft"]].to_string(index=False))
    print(rt[["route", "direction", "route_variant", "mode_name", "n_stops", "n_links", "length_m", "headway_am",
              "fare"]].to_string(index=False))
    print(f" wrote {out_dir}")


def main():
    ap = argparse.ArgumentParser(description="FRT stop and link files for MTSM from EMMEOutputs.gdb.")
    ap.add_argument("--areas", nargs="+", choices=list(AREAS), default=list(AREAS))
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    transit = read_transit()
    for name in args.areas:
        build_area(name, AREAS[name], transit, args.out_root, args.overwrite)


if __name__ == "__main__":
    main()
