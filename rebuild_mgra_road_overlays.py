"""
Rebuild the MGRA <-> road overlay CSVs used by
FleetPy_SanDiego/pre_process/generate_demand_files.py::map_mgra_to_nodes().

The original files were exported by hand from QGIS ("Join attributes by location"):

  MGRA_Roads_contains.csv    base layer = MGRA polygons, join layer = roads,
                             predicate = contains, one row per match
                             (roads lying entirely inside an MGRA)

  Roads_MGRA_intersects.csv  base layer = roads, join layer = MGRA polygons,
                             predicate = intersects, one row per match
                             (every MGRA a road touches/crosses)

This script reproduces both with geopandas, keeping the QGIS column layout:
base-layer fields first, then join-layer fields; join fields whose name already
exists in the base layer get a "_2" suffix (OBJECTID_2, Shape_Leng_2).

Requirements:  pip install geopandas   (brings pandas, shapely, pyogrio)

Usage (defaults are set for Lemon Grove; outputs go to a NEW folder so the
existing files are not overwritten, and are then compared against them):

  python rebuild_mgra_road_overlays.py
  python rebuild_mgra_road_overlays.py --out-dir "<folder>" --overwrite
  python rebuild_mgra_road_overlays.py --mgra-shp ... --roads-shp ... --compare-dir ""
"""

import argparse
import os
import sys
from collections import Counter

import pandas as pd
import geopandas as gpd


# ---------------------------------------------------------------------------
# Defaults (Lemon Grove)
# ---------------------------------------------------------------------------
LG_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data_current_script", "Ritun", "Lemon Grove").replace("\\", "/")
DEFAULT_MGRA_SHP = os.path.join(LG_FOLDER, "QGiS_new", "MGRA13.shp")
DEFAULT_ROADS_SHP = os.path.join(LG_FOLDER, "QGiS_new", "Roads_All_(gissd.sandag.org).shp")
DEFAULT_MGRA_LIST = os.path.join(LG_FOLDER, "Lemon_grove_MGRA.csv")
DEFAULT_OUT_DIR = os.path.join(LG_FOLDER, "MGRA_Node_Mapping_rebuilt")
DEFAULT_COMPARE_DIR = os.path.join(LG_FOLDER, "MGRA_Node_Mapping")

CONTAINS_NAME = "MGRA_Roads_contains.csv"
INTERSECTS_NAME = "Roads_MGRA_intersects.csv"

# Keys used to compare rebuilt vs. existing files (row multiset comparison)
COMPARE_KEYS = ["MGRA", "ROADSEGID", "FNODE", "TNODE"]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def fix_invalid(gdf, label):
    """Repair invalid geometries (self-intersections etc.) with buffer(0)."""
    invalid = ~gdf.geometry.is_valid
    n_bad = int(invalid.sum())
    if n_bad:
        print(f"  {label}: repairing {n_bad} invalid geometries with buffer(0)")
        gdf = gdf.copy()
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].buffer(0)
    return gdf


def load_layers(mgra_shp, roads_shp, mgra_list_csv):
    print(f"Reading MGRA layer : {mgra_shp}")
    mgra = gpd.read_file(mgra_shp)
    print(f"Reading road layer : {roads_shp}")
    roads = gpd.read_file(roads_shp)

    if mgra.crs is None or roads.crs is None:
        sys.exit("ERROR: a layer has no CRS (.prj missing); cannot overlay safely.")
    if roads.crs != mgra.crs:
        print(f"  Reprojecting roads {roads.crs} -> {mgra.crs}")
        roads = roads.to_crs(mgra.crs)

    if mgra_list_csv:
        keep = set(pd.read_csv(mgra_list_csv)["MGRA"].astype(int))
        before = len(mgra)
        mgra = mgra[mgra["MGRA"].astype(int).isin(keep)]
        print(f"  Filtered MGRAs with {os.path.basename(mgra_list_csv)}: {before} -> {len(mgra)}")

    roads = roads[roads.geometry.notna() & ~roads.geometry.is_empty]
    mgra = mgra[mgra.geometry.notna() & ~mgra.geometry.is_empty]
    mgra = fix_invalid(mgra, "MGRA")
    roads = fix_invalid(roads, "roads")

    print(f"  MGRA polygons: {len(mgra)}   road segments: {len(roads)}")
    return mgra, roads


# ---------------------------------------------------------------------------
# QGIS-style "Join attributes by location" (one-to-many)
# ---------------------------------------------------------------------------
def qgis_style_join(base, join, predicate, unmatched="zero"):
    """
    base, join : GeoDataFrames in the same CRS
    predicate  : 'contains' or 'intersects' (tested as base <predicate> join)
    unmatched  : what to do with base features that match nothing
                 'zero'  -> keep row, numeric join fields = 0 (matches the existing
                            Roads_MGRA_intersects.csv, where such rows have MGRA = 0)
                 'blank' -> keep row, join fields empty
                 'drop'  -> discard row
    Returns (DataFrame without geometry, number of unmatched base features).
    """
    base = base.reset_index(drop=True).copy()
    join = join.reset_index(drop=True).copy()

    base_cols = [c for c in base.columns if c != "geometry"]

    # QGIS renames clashing join fields with a numeric suffix: OBJECTID -> OBJECTID_2
    rename = {}
    taken = set(base_cols)
    for c in join.columns:
        if c == "geometry":
            continue
        new, k = c, 2
        while new in taken:
            new = f"{c}_{k}"
            k += 1
        taken.add(new)
        if new != c:
            rename[c] = new
    join = join.rename(columns=rename)
    join_cols = [c for c in join.columns if c != "geometry"]
    join_dtypes = join[join_cols].dtypes

    # helper columns to keep a deterministic QGIS-like row order
    base["_base_order"] = range(len(base))
    join["_join_order"] = range(len(join))

    out = gpd.sjoin(base, join, how="left", predicate=predicate)
    matched = out["_join_order"].notna()
    n_unmatched = int(out.loc[~matched, "_base_order"].nunique())

    if unmatched == "drop":
        out = out[matched]

    out = out.sort_values(["_base_order", "_join_order"], na_position="last", kind="mergesort")

    # left join turns int columns into float (NaN); restore types
    for c in join_cols:
        dt = join_dtypes[c]
        if pd.api.types.is_integer_dtype(dt):
            if unmatched == "zero":
                out[c] = out[c].fillna(0).astype("int64")
            else:
                out[c] = out[c].astype("Int64")
        elif pd.api.types.is_float_dtype(dt) and unmatched == "zero":
            out[c] = out[c].fillna(0.0)

    return pd.DataFrame(out[base_cols + join_cols]).reset_index(drop=True), n_unmatched


# ---------------------------------------------------------------------------
# Comparison with existing files
# ---------------------------------------------------------------------------
def _key_counter(df, keys):
    def to_int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0  # blank / NaN treated like QGIS "0"
    return Counter(tuple(to_int(v) for v in row) for row in df[keys].itertuples(index=False))


def compare(new_df, old_csv, label):
    if not os.path.exists(old_csv):
        print(f"  [{label}] no existing file to compare: {old_csv}")
        return
    old = pd.read_csv(old_csv)
    print(f"  [{label}] rows  rebuilt={len(new_df)}  existing={len(old)}")
    if list(new_df.columns) == list(old.columns):
        print(f"  [{label}] columns identical ({len(old.columns)})")
    else:
        only_new = [c for c in new_df.columns if c not in old.columns]
        only_old = [c for c in old.columns if c not in new_df.columns]
        print(f"  [{label}] column layout differs; only in rebuilt={only_new} only in existing={only_old}")

    keys = [k for k in COMPARE_KEYS if k in new_df.columns and k in old.columns]
    a, b = _key_counter(new_df, keys), _key_counter(old, keys)
    if a == b:
        print(f"  [{label}] IDENTICAL matches on {keys}")
    else:
        extra, missing = a - b, b - a
        print(f"  [{label}] DIFFERENT on {keys}: {sum(extra.values())} rows only in rebuilt, "
              f"{sum(missing.values())} rows only in existing")
        for name, cnt in (("only in rebuilt", extra), ("only in existing", missing)):
            for k, n in list(cnt.items())[:10]:
                print(f"      {name}: {dict(zip(keys, k))} x{n}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Rebuild MGRA/road overlay CSVs (QGIS 'Join attributes by location').")
    ap.add_argument("--mgra-shp", default=DEFAULT_MGRA_SHP)
    ap.add_argument("--roads-shp", default=DEFAULT_ROADS_SHP)
    ap.add_argument("--mgra-list", default=DEFAULT_MGRA_LIST,
                    help="CSV with an MGRA column to restrict the MGRA layer to (empty string = no filter)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--compare-dir", default=DEFAULT_COMPARE_DIR,
                    help="folder holding existing CSVs to compare against (empty string = skip)")
    ap.add_argument("--unmatched", choices=["zero", "blank", "drop"], default="zero",
                    help="handling of base features with no match (default mirrors existing files)")
    ap.add_argument("--overwrite", action="store_true", help="allow overwriting existing output files")
    args = ap.parse_args()

    contains_out = os.path.join(args.out_dir, CONTAINS_NAME)
    intersects_out = os.path.join(args.out_dir, INTERSECTS_NAME)
    for p in (contains_out, intersects_out):
        if os.path.exists(p) and not args.overwrite:
            sys.exit(f"ERROR: {p} exists. Use --overwrite or choose another --out-dir.")
    os.makedirs(args.out_dir, exist_ok=True)

    mgra, roads = load_layers(args.mgra_shp, args.roads_shp, args.mgra_list or None)

    print("\nMGRA contains roads ...")
    contains_df, n_un = qgis_style_join(mgra, roads, "contains", args.unmatched)
    print(f"  rows: {len(contains_df)}   MGRAs with no fully-contained road: {n_un}")
    contains_df.to_csv(contains_out, index=False)
    print(f"  wrote {contains_out}")

    print("\nRoads intersect MGRA ...")
    intersects_df, n_un = qgis_style_join(roads, mgra, "intersects", args.unmatched)
    print(f"  rows: {len(intersects_df)}   roads touching no MGRA: {n_un}")
    intersects_df.to_csv(intersects_out, index=False)
    print(f"  wrote {intersects_out}")

    if args.compare_dir:
        print(f"\nComparing with existing files in {args.compare_dir}")
        compare(contains_df, os.path.join(args.compare_dir, CONTAINS_NAME), "contains")
        compare(intersects_df, os.path.join(args.compare_dir, INTERSECTS_NAME), "intersects")


if __name__ == "__main__":
    main()
