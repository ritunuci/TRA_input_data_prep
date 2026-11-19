# TRA input data processing

This folder builds the FleetPy **demand (trip-request) files** for microtransit studies in **Lemon Grove** and **Downtown San Diego**. The scripts were copied out of `D:/Ritun/Siwei_Micro_Transit/FleetPy_SanDiego` on 2026-11-17. **Work only on the copies here.** Leave the originals in `Siwei_Micro_Transit` as they are.

## Layout

```
pre_process/generate_demand_files.py   main script (copy; data paths use DATA_ROOT)
pre_process/process_network.py         network helpers imported as pre_process.process_network (copy, unchanged)
rebuild_mgra_road_overlays.py          geopandas rebuild of the QGIS MGRA/road overlay CSVs (written in this session)
Cupertino_Network_fullnetwork.ipynb    unrelated Cupertino notebook; still points to C:\Users\11643\... and D:\UCI Research\...
data_current_script/                   copy of the data; its subfolders mirror D:/Ritun/Siwei_Micro_Transit
```

- `generate_demand_files.py` defines `DATA_ROOT = <this folder>/data_current_script`, and every active data path is built from it. To find a file's original location, swap `DATA_ROOT` for `D:/Ritun/Siwei_Micro_Transit`.
- `process_network.py` still has hardcoded `D:/Ritun/Siwei_Micro_Transit/...` paths in about 10 functions. The demand pipeline doesn't call them. Change them to `DATA_ROOT` only if those functions start being used.
- Not copied (only legacy, commented-out code uses them): `Data/0719_input/demand_folder` and `Data/0719_input/trips_nodes_study_area_with_beta.csv`. The second doesn't exist in the original either.

## Running

Use the conda env `shftn_trvlrs`. Run from this folder (the `pre_process.*` import needs that), and **put the env's DLL folders first on PATH**, as `conda activate` would. If you call python.exe alone, any numpy BLAS/LAPACK call (matmul bigger than a few elements, `np.dot`, `np.linalg`, `corrcoef`) kills the process silently with exit 127 / Windows error `0xc06d007f`. The cause is EMME 25 (`C:\Program Files\Bentley\OpenPaths\EMME 25.00.01\programs`, on the system PATH): it supplies its own `libiomp5md.dll`/`tbb12.dll`, which shadow the env's copies, and MKL loads them. `np.random`, pandas I/O and geopandas overlays worked even without the fix.

Bash:
```
E=C:/Users/ritun_uci/.conda/envs/shftn_trvlrs; W="$(cygpath -u "$E")"
export PATH="$W:$W/Library/mingw-w64/bin:$W/Library/usr/bin:$W/Library/bin:$W/Scripts:$W/bin:$PATH"
$E/python.exe -m pre_process.generate_demand_files
$E/python.exe rebuild_mgra_road_overlays.py
```
PowerShell:
```
$E="C:\Users\ritun_uci\.conda\envs\shftn_trvlrs"; $env:PATH="$E;$E\Library\mingw-w64\bin;$E\Library\usr\bin;$E\Library\bin;$E\Scripts;$E\bin;$env:PATH"
& "$E\python.exe" -m pre_process.generate_demand_files
```

- Env as of 2026-11-17: Python 3.14.7, numpy 2.5.2, **pandas 3.0.5**, pyproj 3.7.2 (PROJ 9.5.1), geopandas 1.1.4, pyogrio 0.13.0 (GDAL 3.12.4), shapely 2.1.2, colorama 0.4.6. colorama was the only package added (by pip). `pip check` is clean.
- The `conda` CLI on this machine is broken: the libmamba solver fails with "DLL load ... Access is denied". Add packages with `python.exe -m pip install --dry-run ...` first, then install for real.
- The scripts were probably written for pandas 1.x/2.x. A grep found no pandas 3 problem patterns, and the demand pipeline mostly uses the `csv` module. Still, check the first outputs against the existing files.
- The rebuild script has been run (see "Where the data came from"). `generate_demand_files.py` has not been run yet.
- scipy isn't installed. pandas `corr(method="spearman")` and the like need it.

## Current `__main__` setup in generate_demand_files.py

- `SEED = 42`. `random.seed` and `np.random.seed` are set at the top. All steps share one random stream, so changing an input or the order of steps changes everything that comes after.
- `create_from_scratch = True`, `test_area = ["lemon_grove"]`. Only the Lemon Grove from-scratch pipeline runs.
- The `TRPartA_demand_files` loop and the `sample_20_percent` call are commented out. The old legacy block further down stays commented: it has broken indentation, and its Downtown branch is broken.

### Lemon Grove from-scratch pipeline (LG = DATA_ROOT/Ritun/Lemon Grove)

1. `select_trips`: `LG/demand_folder/larger_SANDAG_trips_in_MGRA.csv` (1.55M trips) filtered by `Lemon_grove_MGRA.csv` (239 MGRAs). Keeps trips with both ends inside, 21,074 of them. `rq_time = depart*3600 + random second`. `depart` holds hours (5–23); this was checked. Writes `lemon_grove_trips_in_MGRA.csv`. Re-running gives the same trips with new seconds.
2. `p_n.create_network_from_raw_edges`: `LG/Network_folder/Lemon_grove_raw_edges.csv` (1,408 links) to `pre_nodes.csv`/`pre_edges.csv`. Every link becomes **two edges, ignoring ONEWAY**. Distance is ft converted to m. Speed is in mph with a 10 mph floor. **Demand nodes are the end nodes of links with SPEED == 20.** About 0.35% of nodes become stop-only at random. FleetPy IDs are 0..N-1 in order of raw node ID.
3. `p_n.write_node_edge_from_pre_node_edge`: writes `nodes.csv`/`edges.csv` with FleetPy IDs. Returns the raw→FleetPy ID dict and the demand nodes (as FleetPy IDs).
4. `map_mgra_to_nodes`: `LG/MGRA_Node_Mapping/MGRA_Roads_contains.csv` (pass 1: a node gets the first MGRA it appears with) plus `Roads_MGRA_intersects.csv` (pass 2: nodes not yet placed get their most frequent MGRA; MGRA <= 0 is ignored). Writes `Nodes_MGRA_mapping.csv` and `MGRA_Nodes_mapping.csv`.
5. `create_tt_demand_file`: writes `LG/demand_folder/lemon_grove_trips_in_nodes.csv` (`rq_time,start,end,request_id`). Each end is a random demand node in the trip's MGRA. **If the MGRA has no demand node, a random node anywhere in Lemon Grove is used**, with no warning.
6. `create_user_profile`: writes `demand_profile_lemon_grove_with_beta.csv` (all rows) and `debug_demand_profile_lemon_grove_with_beta.csv` (about 1% of rows). These are in the **old 14-column format**: fixed generic beta means, a single `bt_t_wt`, no income columns, and `request_id` written as a float.

### Other functions

- `TRPartA_demand_files(area, debug)`: the newer format, **18 columns**. Reads `TR_PartA/Data/demand_data/<area>/<area>_trips.csv` (columns `depart, origin, destination, income, emp_15min`). Cost beta mean = 0.5 × mean_income / income (mean_income LG 70,679.3, DT 88,894.5). Since commit 768dca4 (2026-11-18), both areas share the same beta means: c_ivt 0.2, t_0 0.242, t_wk 0.3, m_wt/f_wt 0.15, m_ivt/f_ivt 0.15, f_trfer 0.5. Before that they were area-specific, and the cost coef was LG 0.579 / DT 0.994. The SDs are unchanged, and draws are clipped with `np.maximum`. `bt_t_fr` is the same draw as `bt_c_gas`. Separate `bt_m_wt`/`bt_f_wt`. `transit_pass` is 1 for 10% of rows. Debug mode keeps 5%. Writes `_old`, then a sorted, renumbered `<area>_[debug_]trips_nodes_study_area_with_beta.csv`. **It also rewrites nodes.csv/edges.csv.** It crashes if any income is 0. The Downtown MGRA path was fixed to include `Ritun/` (2026-11-16).
- `sample_20_percent`: actually takes **25%** (`frac=0.25, random_state=1`) of `<area>_..._with_beta_old.csv` and writes `..._with_beta.csv`. Rows come out shuffled with their original IDs, not renumbered. It ignores its `folder_dir` argument.
- `modify_ind_parameter`: reads an existing beta file and **draws all betas again** (15 columns, split waiting betas). Despite the name, it doesn't adjust the existing values.
- `synthesize_requests_with_trend_time` (`syn_rq`; the user added the upscaling section): imported by the `run_examples_new*.py` drivers, and actively called in `_parking.py` and `_TRC_R1.py`. New rows are appended at the end, not sorted. A suggested fix (not applied) is to sort by `["rq_time","request_id"]` after the concat and not renumber IDs. New rows have NaN betas when the input has beta columns.

## Known issues (not fixed; tell the user before changing them)

- Sorting `rq_time` compares text (L238, L647). **FleetPy loads requests grouped by rq_time** (`src/demand/demand.py:106`), so file order doesn't affect simulation results. It only matters for other tools that read the files.
- `map_mgra_to_nodes`: a bare `except` reuses the previous loop's `node_id_fleetpy` if a node lookup fails.
- `map_mgra_to_nodes` depends on input row order. Pass-2 ties go to whichever MGRA reaches the top count last when scanning the list in reverse, which is effectively by row order (84 tied nodes in Lemon Grove). A deterministic tie rule, such as the smallest MGRA, would make it independent of row order, but it would change about 51 of the current assignments.
- FleetPy drops requests whose start node equals their end node (`demand.py:98`).
- `select_demand_nodes` fails on files whose `request_id` is a float. `synthesize_requests_with_trend_time` (`dtype int`) does too.

## Where the data came from

- The FleetPy drivers load `<area>_[debug_]trips_nodes_study_area_with_beta.csv` from the folder that `FleetPy_SanDiego/folder_directory.py` picks: `Bayesian_Optimization/demand_data/<area>` if TRPartA and BO are on, otherwise `TR_PartA/Data/demand_data/<area>`. **The from-scratch output has neither that name nor that format.**
- `Bayesian_Optimization/demand_data/lemon_grove/`:
  - `_old` (15,053 rows, 2024-07-29) came from a `TRPartA_demand_files` run.
  - `_original` (3,763 rows) is `sample_20_percent(_old)`, renamed by hand. Every row matches `_old`, and it is a 25% sample.
  - `_final_modified_betas` (2026-03-18) has the same 3,763 trips with betas changed somewhere outside this script.
  - The current `_with_beta.csv` (3,742 rows, 2026-03-24) is `_original` minus the 21 trips whose start equals their end, with newly drawn betas.
- `MGRA_Roads_contains.csv` and `Roads_MGRA_intersects.csv` were **made by hand in QGIS** ("Join attributes by location") from `LG/QGiS_new/MGRA13.shp` and `Roads_All_(gissd.sandag.org).shp`, both EPSG:3857 and already cut to Lemon Grove. No code generates them. The contains file uses MGRA as the base layer; the intersects file uses roads as the base layer. 49 roads that touch no MGRA have `MGRA = 0`. `rebuild_mgra_road_overlays.py` reproduces them into `LG/MGRA_Node_Mapping_rebuilt/` and compares the result with the existing files.
  - **Rebuild run on 2026-11-17 (about 2 s).** It read 239 MGRAs (none removed by the list filter) and 1,408 roads, with no invalid geometries. contains: 805 rows, 73 MGRAs contain no road. intersects: 2,124 rows, 49 roads touch no MGRA. Both files have **the same 85 columns and identical row multisets on MGRA/ROADSEGID/FNODE/TNODE** as the QGIS files.
  - Formatting-only differences: POSTDATE/ADDSEGDT are written as `2013-06-15` (QGIS: `6/15/2013`). In contains, RIGHTWAY (15 rows) and SUBDIVID (59 rows) are `0` where QGIS leaves them blank; these are the unmatched-MGRA rows. The pipeline reads only MGRA/FNODE/TNODE.
  - **The row order is completely different, and that matters.** QGIS order isn't a simple sort. contains is grouped by MGRA in roughly reverse shapefile order. intersects isn't even grouped by road (1,408 roads fall into 2,122 non-contiguous runs). It looks like spatial-index iteration order. A replay of `map_mgra_to_nodes` pass 1/pass 2 on both versions maps the same 1,062 nodes, and pass 1 is identical (no node is contained in more than one MGRA). However, **51 nodes get a different MGRA**. All 51 are among the 84 pass-2 nodes where two MGRAs tie for most frequent, and `most_frequent` breaks ties by row order. The order of rows in `Nodes_MGRA_mapping.csv` also changes, which changes later `random.choice` picks. Both mappings are valid, but switching to the rebuilt files will not reproduce outputs made from the QGIS files.
- Downtown SD has no from-scratch inputs: no raw edges, MGRA list or SANDAG trip file. Its overlays (`Data/0719_input/MGRA_nodes_mapping/MGRA_Contains_nodes.csv`, `MGRA_Nodes_match.csv`) also come from QGIS (`0718_Ritun_Downtown_SD/`).

## Working with this user

- When the user says "no code changes", only analyse and report. Make edits only when asked.
- Ask before making choices that are the user's to make (which areas, which pipeline branch, output format). Point out side effects that overwrite files.
- Check claims against the data on disk (headers, row counts, comparisons) rather than guessing.
