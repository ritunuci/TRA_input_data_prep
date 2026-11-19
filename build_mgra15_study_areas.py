"""
Build MGRA Series 15 (MGRA15) study-area lists for Lemon Grove and Downtown San Diego.

Why: the SANDAG ABM3 trips in data_new/demand_data use MGRA15 zone IDs, but the
existing study-area lists (Lemon_grove_MGRA.csv, Downtown_SD_MGRA.csv) hold
MGRA13 IDs, which point to different places in MGRA15. MGRA15 boundaries do not
line up with MGRA13 boundaries, so the study area is transferred by area overlay:

    study area  = union of the area's MGRA13 polygons
    share_in    = area(MGRA15 polygon AND study area) / area(MGRA15 polygon)
    keep        share_in >= --min-share   (default 0.5)

Outputs, per area (written next to the area's other data_new files):
    <prefix>_MGRA15.csv   columns MGRA, TAZ, City, share_in. select_trips() reads the MGRA column.
    <prefix>_MGRA15.shp   the kept MGRA15 polygons (EPSG:2230, as in mgra15.shp), for QGIS

Usage (from the repo folder; see CLAUDE.md "Running" for the env + PATH setup):
    python build_mgra15_study_areas.py
    python build_mgra15_study_areas.py --areas lemon_grove --min-share 0.9
    python build_mgra15_study_areas.py --overwrite

Current outputs (2026-11-18) were built with:
    python build_mgra15_study_areas.py                       # both areas, share_in >= 0.5
    python build_mgra15_study_areas.py --areas downtown_sd --exclude 8745 --overwrite
MGRA15 8745 is the east part of San Diego International Airport (TAZ 1457, the same TAZ as the
ABM3 SAN airport MGRA 11249; CPA 1483, SRA 2; 0 households, 871 of 889 jobs in transportation/
warehousing). It came in because the old MGRA13 Downtown list includes MGRA13 2995, which lies
99% inside it.
"""

import argparse
import os
import sys

import geopandas as gpd
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_NEW = os.path.join(HERE, "data_new")
MGRA15_SHP = os.path.join(DATA_NEW, "mgra15", "mgra15.shp")

# study area = union of these MGRA13 polygons; outputs go to out_dir/<prefix>_MGRA15.*
AREAS = {
    "lemon_grove": dict(
        mgra13_shp=os.path.join(DATA_NEW, "lemon_grove", "QGiS_new", "MGRA13.shp"),
        mgra13_list=os.path.join(DATA_NEW, "..", "data_current_script", "Ritun", "Lemon Grove", "Lemon_grove_MGRA.csv"),
        out_dir=os.path.join(DATA_NEW, "lemon_grove"),
        prefix="Lemon_grove",
    ),
    "downtown_sd": dict(
        mgra13_shp=os.path.join(DATA_NEW, "Downtown_SD", "DT_SD_mgra.shp"),
        mgra13_list=os.path.join(DATA_NEW, "Downtown_SD", "Downtown_SD_MGRA.csv"),
        out_dir=os.path.join(DATA_NEW, "Downtown_SD"),
        prefix="Downtown_SD",
    ),
}

SQFT_PER_SQMI = 5280.0 ** 2


def read_id_list(path):
    # utf-8-sig: Downtown_SD_MGRA.csv starts with a BOM
    return set(pd.read_csv(path, encoding="utf-8-sig")["MGRA"].astype(int))


def build_area(name, cfg, mgra15, min_share, exclude, overwrite):
    csv_out = os.path.join(cfg["out_dir"], cfg["prefix"] + "_MGRA15.csv")
    shp_out = os.path.join(cfg["out_dir"], cfg["prefix"] + "_MGRA15.shp")
    for p in (csv_out, shp_out):
        if os.path.exists(p) and not overwrite:
            sys.exit(f"ERROR: {p} exists. Use --overwrite.")

    print(f"\n== {name}")
    m13 = gpd.read_file(cfg["mgra13_shp"])
    if m13.crs is None:
        sys.exit(f"ERROR: {cfg['mgra13_shp']} has no CRS.")
    # sanity check: the polygons must be exactly the area's MGRA13 list
    ids13 = read_id_list(cfg["mgra13_list"])
    shp_ids = set(m13["MGRA"].astype(int))
    if shp_ids != ids13:
        sys.exit(f"ERROR: MGRA IDs in {cfg['mgra13_shp']} differ from {cfg['mgra13_list']} "
                 f"({len(shp_ids - ids13)} only in shp, {len(ids13 - shp_ids)} only in list).")
    m13 = m13.to_crs(mgra15.crs)
    area = m13.geometry.make_valid().union_all()
    print(f"  study area: {len(m13)} MGRA13 polygons, {area.area / SQFT_PER_SQMI:.2f} sq mi")

    # only MGRA15 polygons that touch the study area need the exact overlap
    cand = mgra15[mgra15.intersects(area)].copy()
    cand["share_in"] = cand.geometry.intersection(area).area / cand.geometry.area
    keep = cand[cand["share_in"] >= min_share].sort_values("MGRA").reset_index(drop=True)
    partial = cand[(cand["share_in"] > 0.01) & (cand["share_in"] < 0.99)]
    print(f"  MGRA15 touching area: {len(cand)}  partial (1-99%): {len(partial)}  "
          f"kept (share_in >= {min_share:.0%}): {len(keep)}  "
          f"kept area: {keep.union_all().area / SQFT_PER_SQMI:.2f} sq mi")
    if exclude:
        hit = sorted(exclude & set(keep["MGRA"]))
        miss = sorted(exclude - set(keep["MGRA"]))
        keep = keep[~keep["MGRA"].isin(exclude)].reset_index(drop=True)
        print(f"  excluded by --exclude: {hit}  -> {len(keep)} kept"
              + (f"  (not in this area's list, ignored: {miss})" if miss else ""))
    print(f"  kept by City code: {keep['City'].value_counts().to_dict()}")

    keep["share_in"] = keep["share_in"].round(4)
    os.makedirs(cfg["out_dir"], exist_ok=True)
    keep[["MGRA", "TAZ", "City", "share_in"]].to_csv(csv_out, index=False)
    keep[["MGRA", "TAZ", "City", "share_in", "geometry"]].to_file(shp_out)
    print(f"  wrote {csv_out}\n  wrote {shp_out}")


def main():
    ap = argparse.ArgumentParser(description="Build MGRA15 study-area lists by area overlay with MGRA13 study areas.")
    ap.add_argument("--areas", nargs="+", choices=list(AREAS), default=list(AREAS))
    ap.add_argument("--min-share", type=float, default=0.5,
                    help="minimum share of an MGRA15 polygon's area inside the study area (default 0.5)")
    ap.add_argument("--exclude", nargs="+", type=int, default=[], metavar="MGRA15",
                    help="MGRA15 IDs to drop after the share cut-off (applies to every area in --areas)")
    ap.add_argument("--mgra15-shp", default=MGRA15_SHP)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    print(f"Reading {args.mgra15_shp}")
    mgra15 = gpd.read_file(args.mgra15_shp)
    mgra15["geometry"] = mgra15.geometry.make_valid()
    print(f"  {len(mgra15)} MGRA15 polygons, CRS {mgra15.crs.to_string()}")

    for name in args.areas:
        build_area(name, AREAS[name], mgra15, args.min_share, set(args.exclude), args.overwrite)


if __name__ == "__main__":
    main()
