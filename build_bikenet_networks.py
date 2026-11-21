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
  one-way     ignored for streets: the pipeline drives every street link both ways (as before)
  speed       SPEED as in Bike_Net (mph); the pipeline applies its 10 mph floor

Freeways and ramps (user, 2026-11-20; CLAUDE.md "Freeways and ramps from TNED"; --no-freeways leaves them out and
writes the streets-only files of before, without the new columns):
  links       TNED_HwyNet FC 1 (freeway), 8 (freeway-to-freeway connector), 9 (ramp) that touch the area's MGRA15
              union, HOV / toll / reversible lanes included. Driven in TNED's direction only (ONEWAY = 1) unless
              the link has BA lanes. Speed TNED SPD, LENGTH miles -> ft.
  joining     only at ramp ends (TNED nodes of these links that also end a TNED street, FC 2-7): our nearest
              street node within 100 ft whose street name agrees (build_period_travel_times.names_agree); else,
              if one of our links with an agreeing name passes within 30 ft, that link is split at the ramp point
              (mid-block ramp; the split node is not a pickup node); else not joined.
  extent      only freeway links in the strongly connected part that holds the streets (on and off inside the
              area); a split is made only if a kept link uses it. Self-loops and links parallel to a street or a
              faster freeway link are dropped. Streets are not otherwise changed.
  IDs         freeway node 300,000,000 + TNED node, split node 310,000,000 + TNED node of the ramp end; freeway
              link ROADSEGID 900,000,000 + HWYCOV0_ID, split piece 800,000,000 + 10 x parent ROADSEGID + piece no.
              All above the Bike_Net IDs, so the FleetPy IDs of the street nodes do not change.

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
      process_network.create_network_from_raw_edges() reads: FNODE, TNODE,
      FRXCOORD, FRYCOORD, TOXCOORD, TOYCOORD (EPSG:2230 ft), L_TRACT, R_TRACT (0),
      ROADSEGID, LENGTH (ft), SPEED (mph), RD20FULL; extra columns Func_Class, LINK_KIND
      (street / alley / driveway / freeway / fwy_connector / ramp), IS_DEMAND, and with freeways ONEWAY,
      PARENT_SEGID (Bike_Net ROADSEGID of a split piece's whole link; = ROADSEGID for other streets, 0 for
      freeway links), TNED_ID (HWYCOV0_ID of freeway links, else 0). Freeway rows: Func_Class = TNED FC,
      RD20FULL = TNED NM.
      create_network_from_raw_edges() marks demand nodes from IS_DEMAND when the file has
      that column (else by SPEED == 20, the old rule).
  <prefix>_roads.shp       the same links as a line layer (EPSG:2230), for QGIS (PARENT_SEGID -> PARENT_SEG)
  <prefix>_raw_nodes.csv   NODE, X, Y, IS_DEMAND (on a pickup link), MGRA (zone it lies in,
      empty if outside the area's zones), ON_BOUNDARY (lies on more than one zone), and with freeways
      NODE_KIND (street / freeway / split)
  <prefix>_mgra_access.csv one row per area zone: MGRA, CONNECTOR_NODE (street end of the
      zone's MGRA Connector), ACCESS_NODE (node used), ACCESS_DIST_FT (0 unless replaced),
      N_PICKUP_NODES (pickup nodes inside the zone)

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_bikenet_networks.py
    python build_bikenet_networks.py --areas downtown_sd --out-root <dir> --overwrite
    python build_bikenet_networks.py --no-freeways --out-root <dir>
"""

import argparse
import os
import sys
from collections import Counter, defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
import shapely.ops

from build_period_travel_times import names_agree  # street-name rule shared with the period-time matching

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

# freeways, freeway-to-freeway connectors and ramps from TNED_HwyNet (user, 2026-11-20)
FWY_KIND = {1: "freeway", 8: "fwy_connector", 9: "ramp"}  # TNED FC -> LINK_KIND
TNED_STREET_FC = (2, 7)
TNED_BBOX_FT = 15000.0       # TNED is read in the area's bounding box plus this margin
SNAP_FT = 100.0              # ramp end -> our node within this distance whose street name agrees
SPLIT_FT = 30.0              # else: split one of our same-name links that passes within this distance
FWY_NODE_BASE = 300_000_000  # freeway node ID = base + TNED node (Bike_Net node IDs are far below)
SPLIT_NODE_BASE = 310_000_000  # split node ID = base + TNED node of the ramp end
SPLIT_SEG_BASE = 800_000_000   # split piece ROADSEGID = base + 10 x parent ROADSEGID + piece number
FWY_SEG_BASE = 900_000_000     # freeway link ROADSEGID = base + TNED HWYCOV0_ID
LANE_FIELDS = ["EA", "A", "MD", "P", "EV"]

ROUTING_FC = {3, 4, 5, 6, 7}
PICKUP_FC = {4, 5, 6, 7}
ODD_FC = {0, -1}
CONNECTOR_FC = 10
DROP_NAMES = {"PERSHING BIKEWAY"}
RESCUED_STREETS = {"02ND AV", "03RD AV", "04TH AV", "05TH AV", "06TH AV", "W GRAPE ST", "W HAWTHORN ST"}
SPEED_FLOOR_MPH = 10.0  # same floor as create_network_from_raw_edges()

RAW_COLS = ["FNODE", "TNODE", "FRXCOORD", "FRYCOORD", "TOXCOORD", "TOYCOORD", "L_TRACT", "R_TRACT",
            "ROADSEGID", "LENGTH", "SPEED", "RD20FULL", "Func_Class", "LINK_KIND", "IS_DEMAND"]
FWY_COLS = ["ONEWAY", "PARENT_SEGID", "TNED_ID"]  # added when freeways are included


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


def scc_labels(nodes, edges):
    """Strongly connected components (Kosaraju, iterative). edges: (a, b) pairs. Returns {node: component root}."""
    out, inn = defaultdict(list), defaultdict(list)
    for a, b in edges:
        out[a].append(b)
        inn[b].append(a)
    seen, order = set(), []
    for s in nodes:
        if s in seen:
            continue
        seen.add(s)
        stack = [(s, iter(out[s]))]
        while stack:
            v, it = stack[-1]
            nxt = next((w for w in it if w not in seen), None)
            if nxt is None:
                order.append(v)
                stack.pop()
            else:
                seen.add(nxt)
                stack.append((nxt, iter(out[nxt])))
    lab = {}
    for s in reversed(order):
        if s in lab:
            continue
        lab[s] = s
        st = [s]
        while st:
            v = st.pop()
            for w in inn[v]:
                if w not in lab:
                    lab[w] = s
                    st.append(w)
    return lab


def as_multi_z(g):
    """A 2-D line as MultiLineString Z (z = 0), the geometry type of the Bike_Net links in the roads layer."""
    return shapely.force_3d(shapely.multilinestrings([g]), 0.0)


def add_freeways(area, poly, out, geoms, tned_nodes):
    """Add TNED freeways / fwy-fwy connectors / ramps to the street links `out` (raw-edges layout).

    Ramp ends (TNED nodes of these links that also end a TNED street, FC 2-7) join our network: at our nearest
    node within SNAP_FT whose street name agrees, else by splitting our nearest same-name link within SPLIT_FT
    (mid-block ramp). Other freeway nodes get FWY_NODE_BASE + TNED node. Freeway links are one-way unless they have
    BA lanes. Only freeway links in the strongly connected part that holds the streets are kept; a split is made
    only if a kept link uses it. Streets are not otherwise changed. Returns (out, geoms, new_nodes, report).
    """
    out = out.copy()
    out["ONEWAY"] = 0
    out["PARENT_SEGID"] = out["ROADSEGID"]
    out["TNED_ID"] = 0
    t = pyogrio.read_dataframe(GDB, layer="TNED_HwyNet", bbox=tuple(area.buffer(TNED_BBOX_FT).total_bounds))
    if t.crs != area.crs:
        sys.exit(f"ERROR: CRS differs: TNED {t.crs} vs area {area.crs}")
    bad = t["HWYCOV0_ID"].isna() | t["AN"].isna() | t["BN"].isna()
    t = t[~bad].copy()
    for c in ("HWYCOV0_ID", "AN", "BN", "FC"):
        t[c] = t[c].astype(int)
    if t["HWYCOV0_ID"].max() >= 10_000_000 or t[["AN", "BN"]].max().max() >= SPLIT_NODE_BASE - FWY_NODE_BASE:
        sys.exit("ERROR: TNED IDs too large for the ID offsets.")
    fw = t[t["FC"].isin(list(FWY_KIND)) & t.intersects(poly)].copy()
    fw["geometry"] = shapely.line_merge(shapely.force_2d(fw.geometry.values))
    if (shapely.get_type_id(fw.geometry.values) != 1).any():
        sys.exit("ERROR: some TNED freeway geometry is not a single line.")
    st = t[t["FC"].between(*TNED_STREET_FC)]
    print(f"  TNED: {len(fw)} freeway/connector/ramp links touch the area ({fw.LENGTH.sum():.2f} mi), "
          f"by FC {fw.FC.value_counts().sort_index().to_dict()} (TNED rows without ID/AN/BN skipped: {int(bad.sum())})")
    nolanes = fw[[f"BALN{p}" for p in LANE_FIELDS]].fillna(0).max(axis=1) <= 0
    fw["ONEWAY"] = nolanes.astype(int)
    if ((fw["WAY"] == 1) != (fw["ONEWAY"] == 1)).any():
        print(f"  NOTE: WAY and BA lanes disagree on {int(((fw['WAY'] == 1) != (fw['ONEWAY'] == 1)).sum())} links "
              "(BA lanes used)")
    ln = {p: fw[[f"ABLN{p}", f"BALN{p}"]].fillna(0) for p in LANE_FIELDS}
    print(f"  HOV codes {fw.HOV.value_counts().to_dict()}; links with a toll: "
          f"{int((fw[[f'TOLL{p}' for p in LANE_FIELDS]].fillna(0) > 0).any(axis=1).sum())}; links whose lanes are 0 "
          f"in some periods: {int(sum((ln[p].sum(axis=1) == 0) for p in LANE_FIELDS).between(1, 4).sum())} "
          "(all included)")

    # ---- ramp ends and how they join our network
    st_names = defaultdict(set)
    for a, b, nm in zip(st["AN"], st["BN"], st["NM"]):
        st_names[a].add(nm)
        st_names[b].add(nm)
    our_names = defaultdict(set)
    for a, b, nm in zip(out["FNODE"], out["TNODE"], out["RD20FULL"]):
        our_names[a].add(nm)
        our_names[b].add(nm)
    node_xy = {}
    for a, x1, y1, b, x2, y2 in out[["FNODE", "FRXCOORD", "FRYCOORD", "TNODE", "TOXCOORD", "TOYCOORD"]].itertuples(index=False):
        node_xy[a], node_xy[b] = (x1, y1), (x2, y2)
    our_ids = np.array(sorted(node_xy))
    ntree = shapely.STRtree(shapely.points([node_xy[n] for n in our_ids]))
    seg_ids = out["ROADSEGID"].to_numpy()
    line = {s: shapely.line_merge(shapely.force_2d(geoms[s])) for s in seg_ids}  # 2-D single lines
    if any(shapely.get_type_id(g) != 1 for g in line.values()):
        sys.exit("ERROR: some street link geometry is not a single line.")
    lgeom = np.array([line[s] for s in seg_ids])
    ltree = shapely.STRtree(lgeom)
    ends = sorted(n for n in set(fw["AN"]) | set(fw["BN"]) if n in st_names)
    missing = [n for n in set(fw["AN"]) | set(fw["BN"]) if n not in tned_nodes.index]
    if missing:
        sys.exit(f"ERROR: {len(missing)} TNED nodes not in TNED_HwyNodes, e.g. {missing[:5]}")
    snap, split, unjoined = {}, {}, []
    for n in ends:
        p = shapely.Point(*tned_nodes.loc[n, ["X_COORD", "Y_COORD"]].astype(float))
        cand = [(p.distance(shapely.Point(node_xy[our_ids[i]])), int(our_ids[i]))
                for i in ntree.query(p, predicate="dwithin", distance=SNAP_FT)
                if any(names_agree(a, b) for a in st_names[n] for b in our_names[our_ids[i]])]
        if cand:
            snap[n] = min(cand)
            continue
        cand = [(lgeom[i].distance(p), int(seg_ids[i])) for i in ltree.query(p, predicate="dwithin", distance=SPLIT_FT)
                if any(names_agree(a, out["RD20FULL"].iat[i]) for a in st_names[n])]
        if cand:
            d, seg = min(cand)
            split[n] = (seg, float(line[seg].project(p)), d)
        else:
            unjoined.append(n)
    print(f"  ramp ends (meet a TNED street): {len(ends)}; at our node <= {SNAP_FT:.0f} ft with an agreeing name: "
          f"{len(snap)} (ft min/median/max {min(d for d, _ in snap.values()):.0f} / "
          f"{np.median([d for d, _ in snap.values()]):.0f} / {max(d for d, _ in snap.values()):.0f}); "
          f"mid-block, split candidate (<= {SPLIT_FT:.0f} ft of a same-name link): {len(split)}; not joined: {len(unjoined)}")
    seg_name = dict(zip(out["ROADSEGID"], out["RD20FULL"]))
    if split:
        print("    split candidates: " + "; ".join(f"TNED node {n} on {seg_name[s]} ({d:.0f} ft)"
                                                   for n, (s, _, d) in split.items()))
    if unjoined:
        print("    not joined: " + "; ".join(f"TNED node {n} [{'/'.join(sorted(st_names[n]))}]" for n in unjoined))

    def node_of(n):
        if n in snap:
            return snap[n][1]
        if n in split:
            return SPLIT_NODE_BASE + n
        return FWY_NODE_BASE + n

    fw["A"] = fw["AN"].map(node_of)
    fw["B"] = fw["BN"].map(node_of)
    loops = fw["A"] == fw["B"]
    if loops.any():
        print(f"  dropping {int(loops.sum())} freeway link(s) whose two ends join the same node: "
              f"{fw.loc[loops, ['HWYCOV0_ID', 'NM']].to_dict('records')}")
        fw = fw[~loops]

    # ---- strongly connected part: streets both ways (with every split candidate), freeway links in their direction
    by_seg = defaultdict(list)
    for n, (seg, pos, _) in split.items():
        by_seg[seg].append((pos, SPLIT_NODE_BASE + n))
    street_edges = []
    for a, b, seg in zip(out["FNODE"], out["TNODE"], out["ROADSEGID"]):
        seq = [a] + [s for _, s in sorted(by_seg.get(seg, []))] + [b]
        for u, v in zip(seq[:-1], seq[1:]):
            street_edges += [(u, v), (v, u)]
    street_pairs = set(street_edges)
    fw = fw.assign(TT=fw["LENGTH"] / fw["SPD"]).sort_values(["TT", "HWYCOV0_ID"])
    fe, seen_pairs, n_par = {}, set(), []
    for hid, a, b, one in zip(fw["HWYCOV0_ID"], fw["A"], fw["B"], fw["ONEWAY"]):
        dirs = [(a, b)] + ([] if one else [(b, a)])
        if any(d in street_pairs or d in seen_pairs for d in dirs):
            n_par.append(int(hid))
            continue
        seen_pairs.update(dirs)
        fe[hid] = dirs
    if n_par:
        print(f"  dropping {len(n_par)} freeway link(s) parallel to a street or a faster freeway link: {n_par}")
    all_edges = street_edges + [d for dirs in fe.values() for d in dirs]
    lab = scc_labels(sorted({u for e in all_edges for u in e}), all_edges)
    roots = {lab[n] for n in our_ids}
    if len(roots) != 1:
        sys.exit("ERROR: the street nodes are not in one strongly connected part.")
    root = roots.pop()
    keep = [hid for hid, dirs in fe.items() if all(lab[u] == root and lab[v] == root for u, v in dirs)]
    kf = fw[fw["HWYCOV0_ID"].isin(keep)].sort_values("HWYCOV0_ID")
    used_nodes = set(kf["A"]) | set(kf["B"])
    split = {n: v for n, v in split.items() if SPLIT_NODE_BASE + n in used_nodes}
    print(f"  kept (strongly connected with the streets): {len(kf)} of {len(fw)} links, {kf.LENGTH.sum():.2f} mi, "
          f"by kind {kf.FC.map(FWY_KIND).value_counts().to_dict()}; one-way {int(kf.ONEWAY.sum())}")
    print(f"    by name: {kf.NM.value_counts().to_dict()}")
    print(f"    ramp ends used: at our node {len({n for n in used_nodes if n < FWY_NODE_BASE})}, "
          f"by a split {len(split)} ({'; '.join(seg_name[s] for s, _, _ in split.values())})")

    # ---- split our street links where a kept ramp joins mid-block
    by_seg = defaultdict(list)
    for n, (seg, pos, _) in split.items():
        by_seg[seg].append((pos, SPLIT_NODE_BASE + n))
    new_nodes = {}
    rows = []
    for seg, cuts in by_seg.items():
        r = out.loc[out["ROADSEGID"] == seg].iloc[0]
        geoms.pop(seg)
        g = line[seg]
        start = shapely.get_point(g, 0)
        if start.distance(shapely.Point(r.FRXCOORD, r.FRYCOORD)) > 1.0:
            sys.exit(f"ERROR: link {seg} geometry does not start at its FNODE.")
        L = g.length
        if 10 * int(seg) + 9 >= FWY_SEG_BASE - SPLIT_SEG_BASE:
            sys.exit(f"ERROR: ROADSEGID {seg} too large for the split-piece ID range.")
        cuts = sorted(cuts)
        pos = [0.0] + [c for c, _ in cuts] + [L]
        seq = [int(r.FNODE)] + [s for _, s in cuts] + [int(r.TNODE)]
        for k, (s0, s1) in enumerate(zip(pos[:-1], pos[1:]), start=1):
            if s1 - s0 < 1.0:
                sys.exit(f"ERROR: split piece shorter than 1 ft on link {seg}.")
            piece = shapely.ops.substring(g, s0, s1)
            q = r.copy()
            q["FNODE"], q["TNODE"] = seq[k - 1], seq[k]
            p0, p1 = shapely.get_point(piece, 0), shapely.get_point(piece, -1)
            q["FRXCOORD"], q["FRYCOORD"] = (r.FRXCOORD, r.FRYCOORD) if k == 1 else (p0.x, p0.y)
            q["TOXCOORD"], q["TOYCOORD"] = (r.TOXCOORD, r.TOYCOORD) if k == len(pos) - 1 else (p1.x, p1.y)
            q["ROADSEGID"] = SPLIT_SEG_BASE + 10 * int(seg) + k
            q["LENGTH"] = float(r.LENGTH) * (s1 - s0) / L
            q["PARENT_SEGID"] = int(seg)
            rows.append(q)
            geoms[q["ROADSEGID"]] = as_multi_z(piece)
            if k < len(pos) - 1:
                new_nodes[seq[k]] = (p1.x, p1.y, "split")
    out = out[~out["ROADSEGID"].isin(list(by_seg))]
    if rows:
        out = pd.concat([out, pd.DataFrame(rows)], ignore_index=True)

    # ---- freeway rows
    xy = dict(node_xy)
    xy.update({n: (x, y) for n, (x, y, _) in new_nodes.items()})
    for n in sorted(used_nodes):
        if n >= FWY_NODE_BASE and n < SPLIT_NODE_BASE:
            x, y = tned_nodes.loc[n - FWY_NODE_BASE, ["X_COORD", "Y_COORD"]].astype(float)
            new_nodes[n] = (x, y, "freeway")
            xy[n] = (x, y)
    fr = pd.DataFrame({
        "FNODE": kf["A"].values, "TNODE": kf["B"].values,
        "FRXCOORD": [xy[n][0] for n in kf["A"]], "FRYCOORD": [xy[n][1] for n in kf["A"]],
        "TOXCOORD": [xy[n][0] for n in kf["B"]], "TOYCOORD": [xy[n][1] for n in kf["B"]],
        "L_TRACT": 0, "R_TRACT": 0,
        "ROADSEGID": FWY_SEG_BASE + kf["HWYCOV0_ID"].values,
        "LENGTH": (kf["LENGTH"] * 5280.0).round(3).values,  # TNED LENGTH is in miles
        "SPEED": kf["SPD"].values, "RD20FULL": kf["NM"].fillna("").values, "Func_Class": kf["FC"].values,
        "LINK_KIND": kf["FC"].map(FWY_KIND).values, "IS_DEMAND": 0,
        "ONEWAY": kf["ONEWAY"].values, "PARENT_SEGID": 0, "TNED_ID": kf["HWYCOV0_ID"].values,
    })
    for hid, g in zip(kf["HWYCOV0_ID"], kf.geometry):
        geoms[FWY_SEG_BASE + int(hid)] = as_multi_z(g)
    out = pd.concat([out, fr], ignore_index=True)
    report = dict(links=len(kf), miles=float(kf.LENGTH.sum()), splits=len(split),
                  freeway_nodes=sum(k == "freeway" for *_, k in new_nodes.values()))
    return out, geoms, new_nodes, report


def build_area(name, cfg, nodes_xy, nodes_mgra, out_root, overwrite, tned_nodes=None):
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

    geoms = dict(zip(links["ROADSEGID"].astype(int), links.geometry))
    if tned_nodes is not None:
        n_street = len(nodes_out)
        out, geoms, new_nodes, rep = add_freeways(area, poly, out, geoms, tned_nodes)
        out = out.sort_values(["FNODE", "TNODE", "ROADSEGID"]).reset_index(drop=True)
        for c in ("FNODE", "TNODE", "ROADSEGID", "Func_Class", "IS_DEMAND") + tuple(FWY_COLS):
            out[c] = out[c].astype(int)
        if not out["ROADSEGID"].is_unique:
            sys.exit("ERROR: ROADSEGID is not unique after adding freeways.")
        # new nodes (freeway and split nodes): not pickup nodes; zone by point-in-polygon as for street nodes
        nodes_out["NODE_KIND"] = "street"
        nn = sorted(new_nodes)
        if nn and min(nn) <= max(nodes_out["NODE"]):
            sys.exit("ERROR: new node IDs are not above the street node IDs.")
        npts = gpd.GeoDataFrame({"NODE": nn}, geometry=gpd.points_from_xy([new_nodes[n][0] for n in nn],
                                                                          [new_nodes[n][1] for n in nn]), crs=area.crs)
        nj = gpd.sjoin(npts, area, predicate="intersects", how="left")
        nz = nj.groupby("NODE")["MGRA"]
        add = pd.DataFrame({"NODE": nn, "X": [new_nodes[n][0] for n in nn], "Y": [new_nodes[n][1] for n in nn],
                            "IS_DEMAND": 0, "MGRA": nz.min().reindex(nn).astype("Int64").values,
                            "ON_BOUNDARY": (nz.nunique().reindex(nn) > 1).astype(int).values,
                            "NODE_KIND": [new_nodes[n][2] for n in nn]})
        nodes_out = pd.concat([nodes_out, add], ignore_index=True)
        if set(nodes_out["NODE"]) != set(out["FNODE"]) | set(out["TNODE"]):
            sys.exit("ERROR: raw nodes and raw-edge nodes differ after adding freeways.")
        print(f"  with freeways: {len(out)} links ({int((out.ONEWAY == 1).sum())} one-way), {len(nodes_out)} nodes "
              f"({n_street} street + {rep['freeway_nodes']} freeway + {rep['splits']} split); pickup nodes "
              f"{int(nodes_out.IS_DEMAND.sum())} (unchanged)")
        print(f"  LINK_KIND: {out.LINK_KIND.value_counts().to_dict()}")

    os.makedirs(out_dir, exist_ok=True)
    out.to_csv(outs[0], index=False)
    geom = [geoms[s] for s in out["ROADSEGID"]]
    gpd.GeoDataFrame(out.drop(columns=["FRXCOORD", "FRYCOORD", "TOXCOORD", "TOYCOORD", "L_TRACT", "R_TRACT"])
                     .rename(columns={"PARENT_SEGID": "PARENT_SEG"}),  # shapefile field names: max 10 characters
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
    ap.add_argument("--no-freeways", action="store_true",
                    help="streets only (the network as built before 2026-11-20, without the new columns)")
    args = ap.parse_args()

    tned_nodes = None
    if not args.no_freeways:
        tned_nodes = pyogrio.read_dataframe(GDB, layer="TNED_HwyNodes", read_geometry=False,
                                            columns=["HNODE", "X_COORD", "Y_COORD"])
        if not tned_nodes["HNODE"].is_unique:
            sys.exit("ERROR: HNODE is not unique in TNED_HwyNodes.")
        tned_nodes = tned_nodes.set_index("HNODE")

    print(f"Reading Bike_Node_2022_Build from {GDB}")
    nodes = pyogrio.read_dataframe(GDB, layer="Bike_Node_2022_Build", read_geometry=False,
                                   columns=["NodeLev_ID", "MGRA", "XCOORD", "YCOORD"])
    if not nodes["NodeLev_ID"].is_unique:
        sys.exit("ERROR: NodeLev_ID is not unique in Bike_Node_2022_Build.")
    nodes = nodes.set_index("NodeLev_ID")
    if nodes.index.max() >= FWY_NODE_BASE:
        sys.exit("ERROR: Bike_Net node IDs reach the freeway node ID range.")

    for name in args.areas:
        build_area(name, AREAS[name], nodes[["XCOORD", "YCOORD"]], nodes["MGRA"], args.out_root, args.overwrite,
                   tned_nodes)


if __name__ == "__main__":
    main()
