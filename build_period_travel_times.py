"""
Period link travel times for the MGRA15 networks, from the SANDAG ABM3 highway assignment.

Run after build_bikenet_networks.py and build_fleetpy_network_mapping.py.

Source (decisions: user, 2026-11-19; see CLAUDE.md "Period travel times"):
  - loaded link times per period, E:/sanmith/abm_runs/shaqa_new_result/report/hwyload_{EA,AM,MD,PM,EV}.csv
    (one row per TNED link, ID1 = HWYCOV0_ID; AB_Time / BA_Time in minutes). They contain ABM3's own
    speeds (TNED SPD) plus intersection delay and congestion.
  - TNED link geometry: TNED_HwyNet in data_new/network/EMMEOutputs.gdb (EPSG:2230), street links only
    (FC 2-7; no freeways, ramps, connectors or walk links). TNED geometry runs AN -> BN.
  - freeway / fwy_connector / ramp links (TNED links added by build_bikenet_networks.py, 2026-11-20) are not
    matched: their AB_Time / BA_Time come straight from hwyload for their own TNED link (TNED_ID), source =
    tned_freeway; a blocked period (> 1000 min) would get BLOCKED_TT_S. They are left out of the class values.
  - street links split where a ramp joins mid-block are matched and timed as their whole Bike_Net link
    (PARENT_SEGID); each piece gets the whole link's times x its share of the length, so street times are the
    same as without freeways.

Matching (Claude's rules, tested 2026-11-19):
  - each Bike_Net link (A -> B) is sampled every ~10 ft (at least 5 points). A point is matched to the
    nearest TNED street link within 30 ft if the two run parallel there (|cos| >= 0.8) and the street
    names agree (spelling variants allowed; names like UNKNOWN / DRIVEWAY / PRIVATE RD don't count against).
  - a link is "matched" if >= 80% of its points are matched. Its time per direction and period is
    (mean over matched points of the TNED pace, s/ft) x link length. The Bike_Net A -> B edge takes TNED
    AB_Time where the two run the same way and BA_Time where they run opposite. A one-way TNED link has
    BA_Time 0: then the AB time is used for both directions (one-way is ignored in our networks).
  - blocked TNED times (> 1000 min) are left out of the mean; if a period has no time left, the link's
    slowest other period is used.
  - unmatched links, one of two rules per area (AREAS[...]["unmatched_rule"], or --unmatched-rule):
      "delay" (additive; both areas, user's choice 2026-11-20): free flow + the class ABM3 delay per foot
          (period time - TNED's own free flow LENGTH/SPD, per ft) of the matched links of the same area and
          Func_Class, x link length. Unmatched streets keep Bike_Net's posted speed and get ABM3's delay +
          congestion in seconds. source = class_delay.
      "ratio" (user's option (i), used until 2026-11-20): free flow x the class ratio (period time /
          Bike_Net free flow) of the matched links. Unmatched streets get ABM3's speed level for their
          class, like matched streets. source = class_ratio.
    The class value is either the median over the matched link-directions, or (class_stat "mean", used in
    both areas) the aggregate: sum of delay / sum of length, i.e. the length-weighted mean delay per ft
    (delay), or sum of period times / sum of free flow (ratio). Per-link values are skewed (TNED's delay
    sits at the downstream end of each TNED link), so the median understates the class's delay. Set in
    AREAS[...]["class_stat"] or --class-stat. Func_Class 0 counts as 7 and both directions of a matched
    link are pooled; a class with no matched link takes the area-wide value.
    The earlier outputs (ratio + median) are kept in outputs_MGRA15/<area>/backup_option_i_ratio/ (Lemon
    Grove) and backup_ratio_median/ (Downtown); --unmatched-rule ratio --class-stat median reproduces them.
  - free flow = LENGTH / max(SPEED, 10 mph) with the pipeline's unit constants: the travel_time in edges.csv.

Outputs, in <out-root>/<area>/:
  network/<prefix>_link_period_tt.csv  one row per directed edge of edges.csv: from_node, to_node (FleetPy),
      raw_from, raw_to (Bike_Net), ROADSEGID, RD20FULL, Func_Class, distance (m), tt_free, tt_EA, tt_AM,
      tt_MD, tt_PM, tt_EV (s), source (tned / class_ratio / class_delay / tned_freeway), match_share (empty for
      freeway edges), tned_ids
  network/<prefix>_roads_period_tt.shp  the links with LINK_KIND, the AB/BA period times (BA empty on one-way
      links) and the source (QGIS)
  fleetpy_network/                     a FleetPy network folder:
      base/nodes.csv, base/edges.csv (copies; edges travel_time = free flow), base/crs.info (EPSG:2230)
      <t>/edges_td_att.csv (from_node, to_node, edge_tt) for t = 0 (EV), 10800 (EA, 3:00), 21600 (AM, 6:00),
      32400 (MD, 9:00), 55800 (PM, 15:30), 68400 (EV, 19:00). FleetPy's NetworkBasic loads a folder when
      update_network(sim_time) is called with exactly that time; before that it uses base/edges.csv.
      0/ holds the EV times because ABM3's EV period runs 19:00-3:00, so 0:00-3:00 is still EV.
      network_dynamics.csv (simulation_time, travel_time_folder) lists the six folders, for the scenario
      parameter nw_dynamic_f,network_dynamics.csv (without it FleetPy scans the integer-named folders itself).

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_period_travel_times.py
    python build_period_travel_times.py --areas downtown_sd --overwrite
    python build_period_travel_times.py --unmatched-rule ratio --class-stat median --out-root <dir>
        # = the earlier version (the backups above)
"""

import argparse
import difflib
import os
import re
import shutil
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
DEFAULT_HWYLOAD = "E:/sanmith/abm_runs/shaqa_new_result/report"

AREAS = {
    "lemon_grove": dict(prefix="Lemon_grove", mgra15_shp=os.path.join(DATA_NEW, "lemon_grove", "Lemon_grove_MGRA15.shp"),
                        unmatched_rule="delay", class_stat="mean"),
    "downtown_sd": dict(prefix="Downtown_SD", mgra15_shp=os.path.join(DATA_NEW, "Downtown_SD", "Downtown_SD_MGRA15.shp"),
                        unmatched_rule="delay", class_stat="mean"),
}
UNMATCHED_SOURCE = {"ratio": "class_ratio", "delay": "class_delay"}

PERIODS = ["EA", "AM", "MD", "PM", "EV"]
# FleetPy folders: simulation time (s) -> ABM3 period. EV runs 19:00-3:00, so it also covers 0:00-3:00.
FLEETPY_FOLDERS = {0: "EV", 10800: "EA", 21600: "AM", 32400: "MD", 55800: "PM", 68400: "EV"}
TNED_STREET_FC = (2, 7)
BUFFER_FT = 30.0
SPACING_FT = 10.0
MIN_POINTS = 5
PARALLEL_COS = 0.8
MIN_SHARE = 0.8
BLOCKED_MIN = 1000.0
BLOCKED_TT_S = 1.0e6  # freeway link blocked in a period (> BLOCKED_MIN): this time, i.e. closed in practice
FWY_KINDS = ("freeway", "fwy_connector", "ramp")  # TNED links added by build_bikenet_networks.py
SPEED_FLOOR_MPH = 10.0
CRS_INFO = "EPSG:2230"

SUFFIXES = {"ST", "STREET", "AV", "AVE", "AVENUE", "BL", "BLVD", "BOULEVARD", "DR", "DRIVE", "RD", "ROAD", "WY", "WAY",
            "PL", "PLACE", "CT", "COURT", "LN", "LANE", "PY", "PKWY", "PARKWAY", "HY", "HWY", "TR", "CR", "CL", "TER",
            "SQ", "PZ", "WK", "MALL", "ROW", "XING", "LOOP", "GLN", "PT"}
DIRECTIONS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
NO_NAME = {"", "UNKNOWN", "DRIVEWAY", "PRIVATE", "ALLEY", "UNNAMED"}


def base_name(name):
    """Street name without direction prefix, type suffix, punctuation and leading zeros ('02ND AV' -> '2ND')."""
    tok = re.sub(r"[^A-Z0-9 ]", " ", str(name or "").upper()).split()
    tok = [t for t in tok if t not in DIRECTIONS]
    while tok and tok[-1] in SUFFIXES:
        tok = tok[:-1]
    return "".join(t.lstrip("0") or "0" for t in tok)


def names_agree(a, b):
    a, b = base_name(a), base_name(b)
    if a in NO_NAME or b in NO_NAME:
        return True
    if a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def read_tned(area_shp, hwyload_dir):
    zone = gpd.read_file(area_shp)
    t = pyogrio.read_dataframe(GDB, layer="TNED_HwyNet", bbox=tuple(zone.buffer(3000).total_bounds))
    if t.crs != zone.crs:
        sys.exit(f"ERROR: CRS differs: TNED {t.crs} vs area {zone.crs}")
    t = t[t.FC.between(*TNED_STREET_FC)].reset_index(drop=True)
    t["len_ft"] = t["LENGTH"] * 5280.0  # TNED LENGTH is in miles
    n_blocked = 0
    for p in PERIODS:
        f = os.path.join(hwyload_dir, f"hwyload_{p}.csv")
        h = pd.read_csv(f, usecols=["ID1", "AB_Time", "BA_Time"]).set_index("ID1").reindex(t["HWYCOV0_ID"])
        if h["AB_Time"].isna().any():
            sys.exit(f"ERROR: {int(h['AB_Time'].isna().sum())} TNED street links are missing in {f}")
        ab = h["AB_Time"].to_numpy(float)
        ba = h["BA_Time"].to_numpy(float)
        ba = np.where(np.isnan(ba) | (ba <= 0), ab, ba)  # one-way: no BA time -> use AB
        ab = np.where(np.isnan(ab) | (ab <= 0), ba, ab)
        n_blocked += int(((ab > BLOCKED_MIN) | (ba > BLOCKED_MIN)).sum())
        t[f"pAB_{p}"] = np.where(ab > BLOCKED_MIN, np.nan, ab * 60.0 / t["len_ft"])  # pace, s per ft
        t[f"pBA_{p}"] = np.where(ba > BLOCKED_MIN, np.nan, ba * 60.0 / t["len_ft"])
    # TNED's own free-flow pace (s per ft) = LENGTH / SPD, the same in both directions (= its ABTM* fields)
    spd = t["SPD"].astype(float).to_numpy()
    t["pAB_FF"] = t["pBA_FF"] = np.where(spd > 0, 3600.0 / (np.where(spd > 0, spd, 1.0) * 5280.0), np.nan)
    return t, n_blocked


def match_links(links, tned):
    """Per Bike_Net link: matched share, TNED ids, name rejections and the TNED-based time per direction/period."""
    tg = shapely.line_merge(shapely.force_2d(tned.geometry.values))
    tree = shapely.STRtree(tg)
    lg = shapely.line_merge(shapely.force_2d(links.geometry.values))
    if (shapely.get_type_id(lg) != 1).any():
        sys.exit("ERROR: some Bike_Net link geometry is not a single line.")
    ids = tned["HWYCOV0_ID"].astype(int).to_numpy()
    names = tned["NM"].to_numpy()
    pace = {f"{d}_{p}": tned[f"p{d}_{p}"].to_numpy() for d in ("AB", "BA") for p in PERIODS + ["FF"]}
    out = []
    for geom, length_ft, name in zip(lg, links["LENGTH"].to_numpy(float), links["RD20FULL"].to_numpy()):
        L = shapely.length(geom)
        n = max(MIN_POINTS, int(np.ceil(L / SPACING_FT)))
        s = (np.arange(n) + 0.5) / n * L
        pts = shapely.line_interpolate_point(geom, s)
        p2 = shapely.line_interpolate_point(geom, np.minimum(s + 1.0, L))
        p0 = shapely.line_interpolate_point(geom, np.maximum(s - 1.0, 0.0))
        tang = np.c_[shapely.get_x(p2) - shapely.get_x(p0), shapely.get_y(p2) - shapely.get_y(p0)]
        tang /= np.linalg.norm(tang, axis=1, keepdims=True)
        (pi, ti) = tree.query_nearest(pts, max_distance=BUFFER_FT, all_matches=False)
        acc = {k: [] for k in pace}
        used, rejected = set(), set()
        for a, b in zip(pi, ti):
            tgm = tg[b]
            pos = shapely.line_locate_point(tgm, pts[a])
            TL = shapely.length(tgm)
            q1 = shapely.line_interpolate_point(tgm, min(pos + 1.0, TL))
            q0 = shapely.line_interpolate_point(tgm, max(pos - 1.0, 0.0))
            v = np.array([shapely.get_x(q1) - shapely.get_x(q0), shapely.get_y(q1) - shapely.get_y(q0)])
            nv = np.linalg.norm(v)
            if nv == 0:
                continue
            c = float(tang[a] @ (v / nv))
            if abs(c) < PARALLEL_COS:
                continue
            if not names_agree(name, names[b]):  # a parallel street with another name
                rejected.add(int(ids[b]))
                continue
            used.add(int(ids[b]))
            same = c > 0
            for p in PERIODS + ["FF"]:
                acc[f"AB_{p}"].append(pace[f"AB_{p}" if same else f"BA_{p}"][b])
                acc[f"BA_{p}"].append(pace[f"BA_{p}" if same else f"AB_{p}"][b])
        n_par = len(acc["AB_EA"])
        rec = {"match_share": n_par / n, "tned_ids": ",".join(map(str, sorted(used))),
               "name_rejected": ",".join(map(str, sorted(rejected - used)))}
        for k, v in acc.items():
            v = np.asarray(v, float)
            rec[f"tned_{k}"] = float(np.nanmean(v)) * length_ft if np.isfinite(v).any() else np.nan
        out.append(rec)
    return pd.DataFrame(out, index=links.index)


def build_area(name, cfg, out_root, hwyload_dir, overwrite, rule, stat):
    net_dir = os.path.join(out_root, name, "network")
    fp_dir = os.path.join(out_root, name, "fleetpy_network")
    pfx = os.path.join(net_dir, cfg["prefix"])
    roads_f, nodes_f, edges_f, pre_nodes_f = (pfx + "_roads.shp", os.path.join(net_dir, "nodes.csv"),
                                              os.path.join(net_dir, "edges.csv"), os.path.join(net_dir, "pre_nodes.csv"))
    for p in (roads_f, nodes_f, edges_f, pre_nodes_f):
        if not os.path.exists(p):
            sys.exit(f"ERROR: {p} not found (run build_bikenet_networks.py and build_fleetpy_network_mapping.py first).")
    tt_out, shp_out = pfx + "_link_period_tt.csv", pfx + "_roads_period_tt.shp"
    for p in (tt_out, shp_out, fp_dir):
        if os.path.exists(p) and not overwrite:
            sys.exit(f"ERROR: {p} exists. Use --overwrite.")

    print(f"\n== {name} (unmatched links: rule '{rule}', class {stat})")
    tned, n_blk = read_tned(cfg["mgra15_shp"], hwyload_dir)
    print(f"  TNED street links (FC {TNED_STREET_FC[0]}-{TNED_STREET_FC[1]}) near the area: {len(tned)}; "
          f"blocked link-periods: {n_blk}")
    links = gpd.read_file(roads_f)
    # link fields from the raw-edges CSV (the shapefile stores numbers with less precision)
    raw = pd.read_csv(pfx + "_raw_edges.csv").set_index("ROADSEGID")
    if set(raw.index) != set(links["ROADSEGID"]):
        sys.exit("ERROR: the roads layer and the raw-edges CSV have different links.")
    for c in ("FNODE", "TNODE", "LENGTH", "SPEED", "Func_Class"):
        links[c] = raw.loc[links["ROADSEGID"], c].to_numpy()
    defaults = {"LINK_KIND": "street", "ONEWAY": 0, "PARENT_SEGID": None, "TNED_ID": 0}
    for c, v in defaults.items():
        links[c] = raw.loc[links["ROADSEGID"], c].to_numpy() if c in raw else v
    links["PARENT_SEGID"] = links["PARENT_SEGID"].fillna(links["ROADSEGID"]).astype(int)
    # free flow exactly as create_network_from_raw_edges: (ft / 3.28084) m / (mph / 2.23694) m/s
    links["tt_free"] = (links["LENGTH"] / 3.28084) / (links["SPEED"].clip(lower=SPEED_FLOOR_MPH) / 2.23694)

    # freeway links take their times straight from hwyload; street links split at a ramp are matched as their whole
    # Bike_Net link (the parent) and the parent's times are shared out by length, so street times do not change
    fwy = links[links["LINK_KIND"].isin(FWY_KINDS)].copy()
    street = links[~links["LINK_KIND"].isin(FWY_KINDS)]
    pieces = street[street["PARENT_SEGID"] != street["ROADSEGID"]].copy()
    parents = []
    for par, g in pieces.groupby("PARENT_SEGID"):
        g = g.assign(k=g["ROADSEGID"] % 10).sort_values("k")
        lines = [shapely.line_merge(shapely.force_2d(x)) for x in g.geometry]
        coords = np.vstack([shapely.get_coordinates(x)[(1 if i else 0):] for i, x in enumerate(lines)])
        if list(g["TNODE"].iloc[:-1]) != list(g["FNODE"].iloc[1:]):
            sys.exit(f"ERROR: split pieces of link {par} do not join up.")
        r = g.iloc[0].copy()
        r["ROADSEGID"], r["TNODE"], r["LENGTH"] = int(par), g["TNODE"].iloc[-1], g["LENGTH"].sum()
        r["geometry"] = shapely.linestrings(coords)
        r["tt_free"] = (r["LENGTH"] / 3.28084) / (max(r["SPEED"], SPEED_FLOOR_MPH) / 2.23694)
        parents.append(r)
    links = pd.concat([street[street["PARENT_SEGID"] == street["ROADSEGID"]],
                       gpd.GeoDataFrame(parents, crs=street.crs) if parents else None])
    links = gpd.GeoDataFrame(links, geometry="geometry", crs=street.crs)
    print(f"  links: {len(street)} street ({len(pieces)} split pieces of {len(parents)} links, matched as whole "
          f"links), {len(fwy)} freeway/connector/ramp (times from hwyload)")
    m = match_links(links, tned)
    links = links.join(m)
    links["matched"] = links["match_share"] >= MIN_SHARE
    links["FC_GROUP"] = links["Func_Class"].replace({0: 7})

    # blocked periods on matched links: use the link's slowest other period
    n_fill = 0
    for d in ("AB", "BA"):
        cols = [f"tned_{d}_{p}" for p in PERIODS]
        miss = links.loc[links.matched, cols].isna()
        if miss.any().any():
            n_fill += int(miss.sum().sum())
            slow = links.loc[links.matched, cols].max(axis=1)
            for c in cols:
                links.loc[links.matched & links[c].isna(), c] = slow
    if n_fill:
        print(f"  blocked period times filled with the link's slowest other period: {n_fill}")

    # per-class factor from the matched links (both directions pooled), as num / den per link-direction:
    #   ratio: period time / Bike_Net free flow;  delay: (period time - TNED free flow) / link length, s per ft
    # stat "median": median of num/den; "mean": sum(num) / sum(den), i.e. the class's overall ratio, or its
    # length-weighted mean delay per ft (the median understates it: TNED's delay sits at the downstream end).
    mm = links[links.matched]
    if rule == "delay" and mm[["tned_AB_FF", "tned_BA_FF"]].isna().any().any():
        sys.exit("ERROR: a matched link has no TNED free-flow time (TNED SPD <= 0).")

    def num_den(d, p):
        if rule == "ratio":
            return mm[f"tned_{d}_{p}"], mm.tt_free
        return mm[f"tned_{d}_{p}"] - mm[f"tned_{d}_FF"], mm.LENGTH

    fc2 = pd.concat([mm.FC_GROUP, mm.FC_GROUP])
    fac, overall = {}, {}
    for p in PERIODS:
        (na, da), (nb, db) = num_den("AB", p), num_den("BA", p)
        num, den = pd.concat([na, nb]), pd.concat([da, db])
        if stat == "median":
            fac[p] = (num / den).groupby(fc2).median()
            overall[p] = float((num / den).median())
        else:
            fac[p] = num.groupby(fc2).sum() / den.groupby(fc2).sum()
            overall[p] = float(num.sum() / den.sum())
    fac = pd.DataFrame(fac)
    missing_fc = sorted(set(links.FC_GROUP) - set(fac.index))
    for fc in missing_fc:
        fac.loc[fc] = pd.Series(overall)
    label = "median" if stat == "median" else ("overall (sum / sum)" if rule == "ratio" else "length-weighted mean")
    if rule == "ratio":
        print(f"  class ratio (period time / free flow), {label}, matched links; used for unmatched links:")
        show = fac.round(3)
    else:
        print(f"  class delay (period time - TNED free flow), {label}, s per 100 ft, matched links; added to the "
              "free flow of unmatched links:")
        show = (fac * 100).round(3)
    print("    " + show.assign(matched_links=mm.groupby("FC_GROUP").size()).to_string().replace("\n", "\n    "))
    if missing_fc:
        print(f"    (no matched links in Func_Class {missing_fc}: the area-wide value is used)")

    for d in ("AB", "BA"):
        for p in PERIODS:
            f = links.FC_GROUP.map(fac[p]).to_numpy()
            unm = links.tt_free * f if rule == "ratio" else links.tt_free + f * links.LENGTH
            links[f"{d}_{p}"] = np.where(links.matched, links[f"tned_{d}_{p}"], unm)
    links["source"] = np.where(links.matched, "tned", UNMATCHED_SOURCE[rule])
    tab = links.groupby("Func_Class").agg(links=("matched", "size"), matched=("matched", "sum"))
    print("  matched links by Func_Class:", {int(k): f"{int(r.matched)}/{int(r.links)}" for k, r in tab.iterrows()},
          f"| total {int(links.matched.sum())}/{len(links)}, "
          f"{links.LENGTH[links.matched].sum() / links.LENGTH.sum():.1%} of length")
    n_rej = int((links.name_rejected != "").sum())
    print(f"  links running along a TNED street with a different name (those points not used): {n_rej}; "
          f"of them unmatched now: {int(((links.name_rejected != '') & ~links.matched).sum())}")
    nr = links[links.name_rejected != ""]
    tnm = dict(zip(tned["HWYCOV0_ID"].astype(int), tned["NM"]))
    print("    " + str(sorted({(str(a), "/".join(sorted({str(tnm[int(x)]) for x in r.split(",")})))
                               for a, r in zip(nr.RD20FULL, nr.name_rejected)})))

    # ---- split pieces: the parent's values, times shared by length; freeway links: hwyload times
    tcol = [f"{d}_{p}" for d in ("AB", "BA") for p in PERIODS]
    keep_cols = ["source", "match_share", "tned_ids", "matched"]
    if len(pieces):
        pl = links.set_index("ROADSEGID").loc[pieces["PARENT_SEGID"]]
        share = pieces["LENGTH"].to_numpy() / pl["LENGTH"].to_numpy()
        for c in tcol:
            pieces[c] = pl[c].to_numpy() * share
        for c in keep_cols:
            pieces[c] = pl[c].to_numpy()
    if len(fwy):
        n_blk_f = 0
        for p in PERIODS:
            h = pd.read_csv(os.path.join(hwyload_dir, f"hwyload_{p}.csv"), usecols=["ID1", "AB_Time", "BA_Time"]
                            ).set_index("ID1").reindex(fwy["TNED_ID"])
            ab, ba = h["AB_Time"].to_numpy(float), h["BA_Time"].to_numpy(float)
            two = fwy["ONEWAY"].to_numpy() == 0
            if np.isnan(ab).any() or (ab <= 0).any() or (two & (np.isnan(ba) | (ba <= 0))).any():
                sys.exit(f"ERROR: a freeway link has no {p} time in hwyload.")
            n_blk_f += int((ab > BLOCKED_MIN).sum() + (two & (ba > BLOCKED_MIN)).sum())
            fwy[f"AB_{p}"] = np.where(ab > BLOCKED_MIN, BLOCKED_TT_S, ab * 60.0)
            fwy[f"BA_{p}"] = np.where(~two, np.nan, np.where(ba > BLOCKED_MIN, BLOCKED_TT_S, ba * 60.0))
        fwy["source"], fwy["match_share"], fwy["matched"] = "tned_freeway", np.nan, True
        fwy["tned_ids"] = fwy["TNED_ID"].astype(int).astype(str)
        print(f"  freeway links: blocked direction-periods {n_blk_f} (set to {BLOCKED_TT_S:.0e} s); "
              "AM time / free flow by kind (sum / sum): "
              + str({k: round(float(g.AB_AM.sum() / g.tt_free.sum()), 2) for k, g in fwy.groupby("LINK_KIND")}))
    links = pd.concat([links[~links["ROADSEGID"].isin(pieces["PARENT_SEGID"])], pieces, fwy])
    links = gpd.GeoDataFrame(links, geometry="geometry", crs=street.crs)

    # ---- per directed edge, in the order of edges.csv
    pre = pd.read_csv(pre_nodes_f)
    raw_of = dict(zip(pre["node_in_fleetpy"], pre["node_index"]))
    edges = pd.read_csv(edges_f)
    edges["raw_from"] = edges["from_node"].map(raw_of)
    edges["raw_to"] = edges["to_node"].map(raw_of)
    edges["ROADSEGID"] = edges["source_edge_id"].astype(int)
    li = links.set_index("ROADSEGID")
    if not li.index.is_unique or not edges.ROADSEGID.isin(li.index).all():
        sys.exit("ERROR: edges.csv and the roads layer do not have the same links.")
    lk = li.loc[edges.ROADSEGID]
    ab = (edges["raw_from"].to_numpy() == lk["FNODE"].to_numpy())
    if not (ab | (edges["raw_from"].to_numpy() == lk["TNODE"].to_numpy())).all():
        sys.exit("ERROR: an edge does not start at either end of its link.")
    out = pd.DataFrame({
        "from_node": edges.from_node, "to_node": edges.to_node, "raw_from": edges.raw_from, "raw_to": edges.raw_to,
        "ROADSEGID": edges.ROADSEGID, "RD20FULL": lk["RD20FULL"].fillna("").to_numpy(),
        "Func_Class": lk["Func_Class"].to_numpy(), "distance": edges.distance,
        "tt_free": lk["tt_free"].to_numpy(),
    })
    if not np.allclose(out.tt_free, edges.travel_time, rtol=1e-6, atol=1e-6):
        sys.exit("ERROR: free-flow time differs from edges.csv travel_time.")
    out["tt_free"] = edges.travel_time
    for p in PERIODS:
        out[f"tt_{p}"] = np.where(ab, lk[f"AB_{p}"].to_numpy(), lk[f"BA_{p}"].to_numpy())
    out["source"] = lk["source"].to_numpy()
    out["match_share"] = lk["match_share"].round(3).to_numpy()
    out["tned_ids"] = lk["tned_ids"].to_numpy()
    tcols = ["tt_free"] + [f"tt_{p}" for p in PERIODS]
    if not (np.isfinite(out[tcols]).all().all() and (out[tcols] > 0).all().all()):
        sys.exit("ERROR: a travel time is missing or not positive.")

    tot = out[tcols].sum()
    print("  sum of edge times / free flow:", {p: round(float(tot[f"tt_{p}"] / tot.tt_free), 3) for p in PERIODS})
    for src in ("tned", UNMATCHED_SOURCE[rule], "tned_freeway"):
        if not (out.source == src).any():
            continue
        s = out[out.source == src][tcols].sum()
        print(f"    {src:11s} edges {int((out.source == src).sum()):5d}:",
              {p: round(float(s[f"tt_{p}"] / s.tt_free), 3) for p in PERIODS})
    print("  edge time s, median free / EA / AM / MD / PM / EV:",
          " / ".join(f"{out[c].median():.1f}" for c in tcols))

    # ---- write
    out.to_csv(tt_out, index=False)
    shp = links[["ROADSEGID", "RD20FULL", "Func_Class", "LINK_KIND", "SPEED", "LENGTH", "tt_free"] + [f"{d}_{p}" for d in ("AB", "BA") for p in PERIODS]
                + ["source", "match_share", "tned_ids", "geometry"]].copy()
    shp["match_share"] = shp["match_share"].round(3)
    shp = shp.rename(columns={"match_share": "share"})  # shapefile field names: max 10 characters
    gpd.GeoDataFrame(shp, geometry="geometry", crs=links.crs).to_file(shp_out)
    if os.path.exists(fp_dir):
        shutil.rmtree(fp_dir)
    os.makedirs(os.path.join(fp_dir, "base"))
    shutil.copyfile(nodes_f, os.path.join(fp_dir, "base", "nodes.csv"))
    shutil.copyfile(edges_f, os.path.join(fp_dir, "base", "edges.csv"))
    with open(os.path.join(fp_dir, "base", "crs.info"), "w") as f:
        f.write(CRS_INFO)
    for t_sim, p in FLEETPY_FOLDERS.items():
        d = os.path.join(fp_dir, str(t_sim))
        os.makedirs(d)
        out[["from_node", "to_node", f"tt_{p}"]].rename(columns={f"tt_{p}": "edge_tt"}).to_csv(
            os.path.join(d, "edges_td_att.csv"), index=False)
    # scenario config nw_dynamic_f,network_dynamics.csv -> NetworkBasic loads the folder of each row at that time
    pd.DataFrame({"simulation_time": list(FLEETPY_FOLDERS), "travel_time_folder": [str(t) for t in FLEETPY_FOLDERS]}
                 ).to_csv(os.path.join(fp_dir, "network_dynamics.csv"), index=False)
    print(f"  wrote {tt_out}\n  wrote {shp_out}\n  wrote {fp_dir}\\ (base + folders {list(FLEETPY_FOLDERS)} "
          f"+ network_dynamics.csv)")


def main():
    ap = argparse.ArgumentParser(description="Period link travel times (ABM3 hwyload via TNED) for the MGRA15 networks.")
    ap.add_argument("--areas", nargs="+", choices=list(AREAS), default=list(AREAS))
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--hwyload-dir", default=DEFAULT_HWYLOAD)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--unmatched-rule", choices=list(UNMATCHED_SOURCE),
                    help="override the area's rule for unmatched links (default: AREAS[...]['unmatched_rule'])")
    ap.add_argument("--class-stat", choices=["mean", "median"],
                    help="override the area's class statistic (default: AREAS[...]['class_stat'])")
    args = ap.parse_args()
    for name in args.areas:
        build_area(name, AREAS[name], args.out_root, args.hwyload_dir, args.overwrite,
                   args.unmatched_rule or AREAS[name]["unmatched_rule"], args.class_stat or AREAS[name]["class_stat"])


if __name__ == "__main__":
    main()
