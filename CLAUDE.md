# TRA input data processing

This folder builds the FleetPy **demand (trip-request) files** for microtransit studies in **Lemon Grove** and **Downtown San Diego**. The scripts were copied out of `D:/Ritun/Siwei_Micro_Transit/FleetPy_SanDiego` on 2026-11-17. **Work only on the copies here.** Leave the originals in `Siwei_Micro_Transit` as they are.

## Layout

```
generate_demand_files.py               legacy demand script (copy; data paths use DATA_ROOT)
process_network.py                     network helpers imported as process_network (copy; one change: IS_DEMAND, see below)
rebuild_mgra_road_overlays.py          geopandas rebuild of the QGIS MGRA/road overlay CSVs (written in this session)
build_mgra15_study_areas.py            MGRA15 study-area lists + shapefiles for LG and DT by area overlay (default share_in >= 0.5)
build_bikenet_networks.py              MGRA15-era LG/DT road networks from Bike_Net_2022_Build (raw edges, roads.shp, nodes, zone access)
build_fleetpy_network_mapping.py       FleetPy nodes/edges + zone -> pickup node mapping from those networks
build_period_travel_times.py           5-period link times from the ABM3 assignment (TNED hwyload) + FleetPy network folder
build_abm3_demand_files.py             ABM3 demand files (trip requests with betas) on the MGRA15 networks
build_frt_network.py                   MTSM FRT inputs (frt_stops/frt_edges/frt_routes) from the gdb transit tables; see "FRT network"
build_super_network.py                 copy of MTSM pre_process/build_super_network.py (MTSM 008c6ac, unchanged); see "Open items"
Cupertino_Network_fullnetwork.ipynb    unrelated Cupertino notebook; still points to C:\Users\11643\... and D:\UCI Research\...
data_current_script/                   copy of the data; its subfolders mirror D:/Ritun/Siwei_Micro_Transit
data_new/                              new data added by the user (ABM3 demand, MGRA15 layer, DT/LG GIS); see "data_new/"
outputs_MGRA15/                        outputs of the MGRA15 network/demand build; see "Road network redo"
```

The data folders and `outputs_MGRA15/` are gitignored. The GitHub repo (public) holds code only.

The two copied scripts were in `pre_process/` (as in the original repo) until 2026-11-20, when they were moved to the root and the folder was deleted. The import is now `import process_network as p_n`, and `DATA_ROOT` goes one folder up from the script instead of two.

- `generate_demand_files.py` defines `DATA_ROOT = <this folder>/data_current_script`, and every active data path is built from it. To find a file's original location, swap `DATA_ROOT` for `D:/Ritun/Siwei_Micro_Transit`.
- `process_network.py` still has hardcoded `D:/Ritun/Siwei_Micro_Transit/...` paths in about 10 functions. The demand pipeline doesn't call them. Change them to `DATA_ROOT` only if those functions start being used.
- Not copied (only legacy, commented-out code uses them): `Data/0719_input/demand_folder` and `Data/0719_input/trips_nodes_study_area_with_beta.csv`. The second doesn't exist in the original either.

## Running

Use the conda env `shftn_trvlrs`. Run from this folder (the scripts import `process_network` from it), and **put the env's DLL folders first on PATH**, as `conda activate` would. If you call python.exe alone, any numpy BLAS/LAPACK call (matmul bigger than a few elements, `np.dot`, `np.linalg`, `corrcoef`) kills the process silently with exit 127 / Windows error `0xc06d007f`. The cause is EMME 25 (`C:\Program Files\Bentley\OpenPaths\EMME 25.00.01\programs`, on the system PATH): it supplies its own `libiomp5md.dll`/`tbb12.dll`, which shadow the env's copies, and MKL loads them. `np.random`, pandas I/O and geopandas overlays worked even without the fix.

Bash:
```
E=C:/Users/ritun_uci/.conda/envs/shftn_trvlrs; W="$(cygpath -u "$E")"
export PATH="$W:$W/Library/mingw-w64/bin:$W/Library/usr/bin:$W/Library/bin:$W/Scripts:$W/bin:$PATH"
$E/python.exe generate_demand_files.py
$E/python.exe rebuild_mgra_road_overlays.py
$E/python.exe build_bikenet_networks.py            # then:
$E/python.exe build_fleetpy_network_mapping.py     # then:
$E/python.exe build_period_travel_times.py         # then:
$E/python.exe build_abm3_demand_files.py           # all four refuse to overwrite without --overwrite
$E/python.exe build_frt_network.py                 # after build_fleetpy_network_mapping.py; also needs --overwrite
```
PowerShell:
```
$E="C:\Users\ritun_uci\.conda\envs\shftn_trvlrs"; $env:PATH="$E;$E\Library\mingw-w64\bin;$E\Library\usr\bin;$E\Library\bin;$E\Scripts;$E\bin;$env:PATH"
& "$E\python.exe" generate_demand_files.py
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
2. `p_n.create_network_from_raw_edges`: `LG/Network_folder/Lemon_grove_raw_edges.csv` (1,408 links) to `pre_nodes.csv`/`pre_edges.csv`. Every link becomes **two edges, ignoring ONEWAY**. Distance is ft converted to m. Speed is in mph with a 10 mph floor. **Demand nodes are the end nodes of links with SPEED == 20.** (Since 2026-11-19 the function uses the `IS_DEMAND` column instead when the file has one, and prints which rule it used; the old LG file still gives 814 demand nodes.) About 0.35% of nodes become stop-only at random. FleetPy IDs are 0..N-1 in order of raw node ID.
3. `p_n.write_node_edge_from_pre_node_edge`: writes `nodes.csv`/`edges.csv` with FleetPy IDs. Returns the raw→FleetPy ID dict and the demand nodes (as FleetPy IDs).
4. `map_mgra_to_nodes`: `LG/MGRA_Node_Mapping/MGRA_Roads_contains.csv` (pass 1: a node gets the first MGRA it appears with) plus `Roads_MGRA_intersects.csv` (pass 2: nodes not yet placed get their most frequent MGRA; MGRA <= 0 is ignored). Writes `Nodes_MGRA_mapping.csv` and `MGRA_Nodes_mapping.csv`.
5. `create_tt_demand_file`: writes `LG/demand_folder/lemon_grove_trips_in_nodes.csv` (`rq_time,start,end,request_id`). Each end is a random demand node in the trip's MGRA. **If the MGRA has no demand node, a random demand node anywhere in Lemon Grove is used**, with no warning.
6. `create_user_profile`: writes `demand_profile_lemon_grove_with_beta.csv` (all rows) and `debug_demand_profile_lemon_grove_with_beta.csv` (about 1% of rows). These are in the **old 14-column format**: fixed generic beta means, a single `bt_t_wt`, no income columns, and `request_id` written as a float.

### Other functions

- `TRPartA_demand_files(area, debug)`: the newer format, **18 columns**. Reads `TR_PartA/Data/demand_data/<area>/<area>_trips.csv` (columns `depart, origin, destination, income, emp_15min`). Cost beta mean = 0.5 × mean_income / income (mean_income LG 70,679.3, DT 88,894.5). Since commit 768dca4 (2026-11-18), both areas share the same beta means: c_ivt 0.2, t_0 0.242, t_wk 0.3, m_wt/f_wt 0.15, m_ivt/f_ivt 0.15, f_trfer 0.5. Before that they were area-specific, and the cost coef was LG 0.579 / DT 0.994. The SDs are unchanged, and draws are clipped with `np.maximum`. `bt_t_fr` is the same draw as `bt_c_gas`. Separate `bt_m_wt`/`bt_f_wt`. `transit_pass` is 1 for 10% of rows. Debug mode keeps 5%. Writes `_old`, then a sorted, renumbered `<area>_[debug_]trips_nodes_study_area_with_beta.csv`. **It also rewrites nodes.csv/edges.csv.** It crashes if any income is 0. The Downtown MGRA path was fixed to include `Ritun/` (2026-11-16).
- `sample_20_percent`: actually takes **25%** (`frac=0.25, random_state=1`) of `<area>_..._with_beta_old.csv` and writes `..._with_beta.csv`. Rows come out shuffled with their original IDs, not renumbered. It ignores its `folder_dir` argument.
- `modify_ind_parameter`: reads an existing beta file and **draws all betas again** (15 columns, split waiting betas). Despite the name, it doesn't adjust the existing values.
- `synthesize_requests_with_trend_time` (`syn_rq`; the user added the upscaling section): imported by the `run_examples_new*.py` drivers, and actively called in `_parking.py` and `_TRC_R1.py`. New rows are appended at the end, not sorted. A suggested fix (not applied) is to sort by `["rq_time","request_id"]` after the concat and not renumber IDs. New rows have NaN betas when the input has beta columns.

## Open items

- **MTSM must keep walking and stops off the freeways** (opened 2026-11-20, user asked to keep it open). Since commit eed31f6 the LG/DT networks contain TNED freeways, connectors and ramps (one-way edges). MTSM's `pre_process/build_super_network.py` (checked at b38d7ba) puts a walk link on every road edge, draws virtual stops from all road nodes, joins FRT stops to the nearest road node, and assumes two edges per road segment. Before MTSM uses the new networks, its builder must use `outputs_MGRA15/<area>/network/<prefix>_edge_kind.csv` (`link_kind` street/alley/driveway = walkable; freeway/fwy_connector/ramp = car only) and `<prefix>_node_kind.csv` (`kind` street/freeway) for walk links, virtual stops and stop joining. The car network (`auto_edges.csv`) and microtransit driving can use every edge. Not started; the MTSM work and tests wait for the user ("we will do it later"). Details: "Freeways and ramps from TNED", MTSM consequence; README section 6. (MTSM tracks this as its I-33 in `docs/open_issues.md`.)
- **MTSM must work for any study area and for the new FRT network** (opened 2026-11-23; the user adds it to MTSM's `docs/open_issues.md` as **I-34**, text written in this session; the user will generalize MTSM). Checked at MTSM `008c6ac`:
  1. FRT line length, duration, cost, VMT and ridership output (`output_performance_metrics.py`) are Lemon Grove only. A Downtown run crashes at `scenarios.py:90` (`UnboundLocalError`). The line length = sum of *unique distance values* per route, which works only because the old network had identical distances in both directions.
  2. The FRT fare and microtransit start fare are charged only when `study_area == "lemon_grove"` (`network_algorithms.py:352, :372`). Other areas pay $0.
  3. `read_super_network` uses `nx.DiGraph`, so a second link with the same node pair silently overwrites the first.
  4. With stop nodes per route, a route change becomes alight (0 s) + board (headway/2). There is no new fare (`transit_fare` is not set after a waiting link), and `bt_f_trfer` applies if the penalty is on. This changes results against the reference.
  5. 70 lines name Lemon Grove (`"lemon_grove"` / `LG_`).
- **`build_super_network.py`** (root) was placed here by the user on 2026-11-23. It is a byte-identical copy of MTSM `pre_process/build_super_network.py` at `008c6ac`; the file was last changed in MTSM at b38d7ba. It does not yet apply the freeway rule (I-33). It was test-run (scratchpad output only) on the FRT build; see "FRT network".

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
- `MGRA_Roads_contains.csv` and `Roads_MGRA_intersects.csv` were **made by hand in QGIS** ("Join attributes by location") from `LG/QGiS_new/MGRA13.shp` and `Roads_All_(gissd.sandag.org).shp`, both EPSG:3857 and already cut to Lemon Grove. No code generates them. The contains file uses MGRA as the base layer; the intersects file uses roads as the base layer. 49 roads that touch no MGRA have `MGRA = 0`. `rebuild_mgra_road_overlays.py` reproduces them into `LG/MGRA_Node_Mapping_rebuilt/` and compares the result with the existing files. That folder is not on disk now (deleted 2026-11-18); rerun the script to recreate it.
  - **Rebuild run on 2026-11-17 (about 2 s).** It read 239 MGRAs (none removed by the list filter) and 1,408 roads, with no invalid geometries. contains: 805 rows, 73 MGRAs contain no road. intersects: 2,124 rows, 49 roads touch no MGRA. Both files have **the same 85 columns and identical row multisets on MGRA/ROADSEGID/FNODE/TNODE** as the QGIS files.
  - Formatting-only differences: POSTDATE/ADDSEGDT are written as `2013-06-15` (QGIS: `6/15/2013`). In contains, RIGHTWAY (15 rows) and SUBDIVID (59 rows) are `0` where QGIS leaves them blank; these are the unmatched-MGRA rows. The pipeline reads only MGRA/FNODE/TNODE.
  - **The row order is completely different, and that matters.** QGIS order isn't a simple sort. contains is grouped by MGRA in roughly reverse shapefile order. intersects isn't even grouped by road (1,408 roads fall into 2,122 non-contiguous runs). It looks like spatial-index iteration order. A replay of `map_mgra_to_nodes` pass 1/pass 2 on both versions maps the same 1,062 nodes, and pass 1 is identical (no node is contained in more than one MGRA). However, **51 nodes get a different MGRA**. All 51 are among the 84 pass-2 nodes where two MGRAs tie for most frequent, and `most_frequent` breaks ties by row order. The order of rows in `Nodes_MGRA_mapping.csv` also changes, which changes later `random.choice` picks. Both mappings are valid, but switching to the rebuilt files will not reproduce outputs made from the QGIS files.
- In `data_current_script`, Downtown SD has no from-scratch inputs: no raw edges, MGRA list or SANDAG trip file. `data_new/Downtown_SD/` now adds an MGRA13 list and polygons, plus `DT_SD_roadnetworks.shp`, `Downtown_SD_Road_network.csv` and `Downtown_SD_microtransit_trips.csv`. The two road files are MGRA13-era Roads_All (1,186 links; not to be used, see "Road network redo"). `Downtown_SD_microtransit_trips.csv` has not been examined. Its overlays (`Data/0719_input/MGRA_nodes_mapping/MGRA_Contains_nodes.csv`, `MGRA_Nodes_match.csv`) also come from QGIS (`0718_Ritun_Downtown_SD/`).

## data_new/ (examined 2026-11-18; used by build_mgra15_study_areas.py, build_bikenet_networks.py, build_fleetpy_network_mapping.py)

### demand_data/: SANDAG ABM3 (ActivitySim), resident model, full region

- These files are byte-identical copies of `E:/sanmith/abm_runs/output_full_model/resident/` (full ABM3 run of 2026-03-13; the same files are also in `abm_runs/shaqa_new_result/` and `test_abm_runs/submodel_skip_test_1/`). They hold **resident trips only**. The run's crossborder and visitor `final_trips.csv` were not copied.
- `final_households.csv`: 0.5 GB, 1,276,883 rows, 55 cols, including `mgra` (= `home_zone_id`), `TAZ`, `income`, `microtransit`, `nev`, `household_id`.
- `final_persons.csv`: 1.0 GB, 3,283,069 rows, 71 cols.
- `final_tours.csv`: 1.0 GB, 4,512,367 rows, 33 cols, including `tour_mode`.
- `final_trips.csv`: 12.1 GB, 12,834,519 rows, 140 cols. Key columns are `origin`/`destination` (MGRA15; IDs up to 24,333, so above 24,321 are probably external), `otaz`/`dtaz`, `depart`, `trip_period`, `trip_mode`, `purpose`, `weight_trip`, `weight_person_trip`, `trip_id`. Read it in chunks with `usecols`. A full pass takes about 5 min.
- `sample_rate` = 1 everywhere, so this is a 100% sample.
- **The zones are MGRA Series 15, not MGRA13.** `Lemon_grove_MGRA.csv` and the LG/DT QGIS overlays use MGRA13 IDs, and those IDs point to different places in MGRA15. Of 556 LG MGRA pairs that share a TAZ13, only 28 share a TAZ15. 147 of the 239 LG IDs have no households in the new data. Filtering with the old list picks 9,599 scattered, meaningless trips.
- **`depart` is a half-hour period from 1 to 48, not an hour.** Period p starts at 3:00 + (p−1)×30 min. `trip_period`: EA 1–6 (3:00–6:00), AM 7–12, MD 13–25 (to 15:30), PM 26–32 (to 19:00), EV 33–48 (to 3:00). Periods 43–48 fall after midnight. `select_trips`'s `depart*3600` would be wrong for this data.
- Income is only in households (join on `household_id`). **43,940 households (3.4%) have income 0**, and `TRPartA_demand_files` crashes on that. There is no `emp_15min`.
- Modes use ActivitySim names (`DRIVEALONE`, `SHARED2/3`, `WALK_LOC/PRM/MIX`, `TNC_SINGLE`, …). Shared modes have `weight_trip` 0.5 or 0.3003. Every household has `microtransit` = 0. NEV zones are 10 and 14.

### mgra15/: MGRA Series 15 polygons

- Copied on 2026-11-18 from `E:/sanmith/sandag_abm_input/mgra15.*` (md5 verified). The copies in `E:/sanmith/abm_runs/*/input/` and `E:/sanmith/ABM/input/geography/mgra/` are identical. `TAZ15.shp` and the land-use table `mgra15_based_input2022.csv` (83 cols, including `emp_total`, `microtransit`, `nev`) are next to it in `E:/sanmith/sandag_abm_input/`. They were not copied.
- 24,321 polygons, MGRA IDs 1 to 24,321 (unique). CRS is **EPSG:2230** (feet), while the LG layers are EPSG:3857. Columns: MGRA, CT20, CT20BG, SRA, MSA, City, ZIP, Sphere, CPA, CPASG, TAZ, Council, Super, LUZ, Elem, Unif, High, Coll, Transit, Mil.
- It matches the ABM3 output: all 18,632 household MGRAs are present, every TAZ agrees, and the land-use TAZ agrees for all 24,321 MGRAs.
- City codes follow the MGRA13 codes: 10 = Lemon Grove, 14 = San Diego, 9 = La Mesa, 19 = unincorporated.
- `D:/Ritun/Siwei_Micro_Transit` has only MGRA13 layers. There's no MGRA15 layer and no MGRA13→MGRA15 crosswalk.

### Study areas in MGRA15

**Built on 2026-11-18 with `build_mgra15_study_areas.py`, cut-off share_in ≥ 0.5 (the user's choice).** The study area is the union of the area's MGRA13 polygons, and share_in = the share of an MGRA15 polygon's area that lies inside it. The script checks that the MGRA13 polygon IDs equal the MGRA13 list. It refuses to overwrite its outputs unless you pass `--overwrite`. It runs in about 4 s.

| Area | List (`select_trips` reads column `MGRA`) | QGIS layer (EPSG:2230) | MGRA15 | min share_in | Households |
|---|---|---|---|---|---|
| Lemon Grove | `data_new/lemon_grove/Lemon_grove_MGRA15.csv` | `data_new/lemon_grove/Lemon_grove_MGRA15.shp` | 225 | 0.778 | 11,896 |
| Downtown SD | `data_new/Downtown_SD/Downtown_SD_MGRA15.csv` | `data_new/Downtown_SD/Downtown_SD_MGRA15.shp` | 387 (388 − 8745) | 0.516 | 34,798 |

- **Downtown excludes MGRA15 8745** (user decision, 2026-11-18). Rebuild command: `--areas downtown_sd --exclude 8745 --overwrite`. Running the script without `--exclude` brings 8745 back.
  - 8745 is the east part of San Diego International Airport: TAZ 1457, the same TAZ as the ABM3 SAN airport MGRA 11249 (`E:/sanmith/ABM/src/asim/configs/airport.SAN/preprocessing.yaml`), which it borders. It is CPA 1483 / SRA 2; every other DT zone is SRA 1. It has 0 households, and 871 of its 889 jobs are in transportation/warehousing.
  - It entered the list through MGRA13 2995 in the old `Downtown_SD_MGRA.csv`, which lies 99% inside 8745. **The MGRA13 list still contains 2995.**
  - Air-passenger trips are in ABM3's separate airport model, not in `data_new/demand_data`.
- The DT set is not only the Downtown community plan area. 335 zones are CPA 1404, which likely is Downtown; the other 52 come from 6 neighbouring CPAs.

Columns in both: MGRA, TAZ, City, share_in. In LG, no zone has a share between 50% and 77.8%, so any cut-off in that range gives the same list. All 387 DT zones are City 14.

- ABM3 trips with both ends in `Lemon_grove_MGRA15.csv`: **43,737** (the old ABM2 file gave 21,074). 3,425 have the same origin and destination MGRA.

Overlay counts for other cut-offs (from before the build):

- **Lemon Grove**: the current 239 MGRA13 polygons cover 5.20 sq mi (City 10: 190, 14: 32, 19: 12, 9: 5), so `City == 10` is not the study area. MGRA15 zones with at least the given share of their area inside the study area: ≥99% 206, ≥90% 219, **≥50% 225** (5.17 sq mi), any overlap 267. 42 zones are partial.
- **Downtown SD**: `data_new/Downtown_SD/Downtown_SD_MGRA.csv` (364 IDs) = the IDs in `DT_SD_mgra.shp` (EPSG:2230, MGRA column only). These are **MGRA13**: same-ID IoU with MGRA13 is above 0.9 for all of them, and 0 against MGRA15. The area is 3.14 sq mi. MGRA15 zones: ≥99% 366, ≥90% 381, **≥50% 388**, any overlap 435. 47 are partial. The ≥50% set holds 34,798 households.
- `D:/Ritun/Siwei_Micro_Transit/MGRA13.shp` has only 10,802 polygons, so it isn't the full region.

## Road network redo for MGRA15 (decisions and build 2026-11-19, including period travel times)

On 2026-11-19 everything done after commit d9b9675 was rolled back: a TNED-based network build, its script, and an LG overlay on the MGRA13-era roads. The network is being redone. **Rule: the MGRA13-era Roads_All networks must not be used with MGRA15.** That covers `LG/Network_folder/Lemon_grove_raw_edges.csv` and, in `data_new/Downtown_SD/`, `DT_SD_roadnetworks.shp` / `Downtown_SD_Road_network.csv` (1,186 links, Roads_All schema).

### Decisions confirmed by the user

- **N1, source: `Bike_Net_2022_Build`** in `data_new/network/EMMEOutputs.gdb`, for **both** areas.
- **Pickup (demand) streets, same rule in both areas: Func_Class 4–7.**
  - These are major roads, collectors and local streets.
  - Before alleys/DY are handled, this gives LG 1,072 nodes in 206 of 225 zones and DT 730 nodes in 291 of 387 zones. (These counts are all end nodes of the links, including nodes just outside the zones; inside the zones it is LG 984, DT 639.)
- **Private roads ("PRIVATE RD") stay in as pickup streets.**
- **Alleys ("ALLEY") and private driveways ("PRIVATE DY") are not pickup links.**
  - LG has 10 + 10 such links and DT 14 + 10, all Func_Class 7 at 10 mph.
  - The exclusion is per link. A node where an alley or driveway meets a street stays a pickup node through the street link, as the old SPEED == 20 rule did.
  - **They stay in the network for routing**, driven both ways, as in the old work. The old LG raw edges had 17 alley links and 12 "PRIVATE DY" links, all driven both ways and none of them demand links. In Bike_Net, 6 LG and 5 DT alleys join two streets; the rest are mostly dead ends.
- **Bike-only links are removed** from the network.
- **One-way is ignored: every link is driven both ways**, as in the old pipeline. `create_network_from_raw_edges` writes AB and BA for every link; the old LG file had 124 `ONEWAY = F` links, all driven both ways. (Since 2026-11-20 this holds for streets only; the TNED freeways and ramps are one-way, see "Freeways and ramps from TNED".)
- **Routing links:**
  - Func_Class 3–7 with `AB_Lanes + BA_Lanes > 0`, touching the area's MGRA15 union (the `<prefix>_MGRA15.shp` polygons), keeping the largest connected component.
  - Drop Func_Class 10 (connectors), 0 and −1 (paths, walkways, "platfrom connection") and "PERSHING BIKEWAY".
  - **FC 0/−1 links, checked 2026-11-19, decided by the user:**
    - **DT, kept as normal streets** (routing and pickups): FC 0 links named `02ND AV`, `03RD AV`, `04TH AV`, `05TH AV`, `06TH AV`, `W GRAPE ST`, `W HAWTHORN ST`. That is 17 links, about 3,700 ft, at 20 or 35 mph; most match old Roads_All street classes L/C. They are probably miscoded short pieces of those streets.
    - **LG, kept as alleys** (routing only, not pickups): the 2 FC 0 links named `ALLEY` (355 ft; old FUNCLASS W, in the old network).
    - **Dropped:**
      - all other FC 0/−1 links: PATH, MLK Path, Embarcadero Path, walkpaths, ped bridges, `I-5 Bridge`, "platfrom connection", NEW NETWORK LINK, …;
      - `EL PRADO` (Balboa Park, 10 mph);
      - `CAMTO CENTRO` (FC −1);
      - `L ST`, `W E ST`, `CALIFORNIA ST` (FC −1, speed 0, old FUNCLASS P).
- **Node → zone:**
  - A street node belongs to the MGRA15 polygon that contains it (point-in-polygon). This replaces the QGIS contains/intersects overlays and the row-order tie rule.
  - A zone with no pickup node gets the street node its "MGRA Connector" attaches to. Every LG and DT zone has exactly one, so no trip uses the random-anywhere fallback.
- **Travel time in `edges.csv`:** posted `SPEED` with the pipeline's 10 mph floor (free flow), as before. Period travel times come on top; see "Period travel times" below.
- **Code:**
  - a new build script (raw-edges CSV + shapefile with a pickup flag);
  - `create_network_from_raw_edges` reads the flag instead of `SPEED == 20`;
  - a mapping step that writes `Nodes_MGRA_mapping.csv`, so `create_tt_demand_file` can stay unchanged.

- **No nearest-node fallback** is needed; the connector fallback covers every zone.
- **Connector node outside the largest component:** use the nearest node in the component instead.
- **Stop-only nodes: none** (user decision, 2026-11-19). `create_network_from_raw_edges` still draws its random 0.35% (LG 5, DT 4); `build_fleetpy_network_mapping.py` resets `is_stop_only` to False for every node before writing `nodes.csv`. FleetPy can't route through a stop-only node.
- **Disconnected pieces stay out** (user decision, 2026-11-19). The pieces dropped by the largest-component rule (LG 25 links / 29 nodes in 6 pieces, DT 14 links / 16 nodes in 3 pieces) all join the main network through drivable streets just outside the study area, which the "touching the area" rule leaves out. Adding those streets would bring back about 17 LG / 7 DT pickup nodes inside zones and 5 LG zones; the user chose not to. The pieces: LG golf-course streets (Trophy Dr, Turf Ln, Par Dr, Fairway Dr, Link Dr), Lime St / West St / Corona St, Carlsbad St/Ct, College Grove Wy, 2 Sweetwater Rd links; DT Balboa Park edge (6th Av, Balboa Dr, Juniper Rd, 8th Avenue Dr, Ivy St, El Prado FC 7), 26th St, A St, Cobblestone Dr.

### Build (2026-11-19)

Commands: `build_bikenet_networks.py`, then `build_fleetpy_network_mapping.py` (each about 1 min; both areas by default; `--areas`, `--out-root`, `--overwrite`). Outputs in `outputs_MGRA15/<area>/`:

- `network/<prefix>_raw_edges.csv` (Bike_Net node IDs; Roads_All-like columns + `Func_Class, LINK_KIND, IS_DEMAND`), `<prefix>_roads.shp` (QGIS), `<prefix>_raw_nodes.csv` (`NODE, X, Y, IS_DEMAND, MGRA, ON_BOUNDARY`), `<prefix>_mgra_access.csv` (`MGRA, CONNECTOR_NODE, ACCESS_NODE, ACCESS_DIST_FT, N_PICKUP_NODES`).
- `network/pre_nodes.csv, pre_edges.csv, nodes.csv, edges.csv`: the pipeline's FleetPy files (FleetPy IDs 0..N−1 = Bike_Net IDs in ascending order; `pre_nodes.csv` holds both). Coordinates are EPSG:2230 ft. `edges.csv` travel_time is free flow.
- `mapping/Nodes_MGRA_mapping.csv` (`node, MGRA, raw_node, source`; FleetPy IDs; sorted by MGRA, node). `source` = `inside` (pickup node in the zone) or `access` (zone without a pickup node: its connector's street node, which may lie in another zone, so a node can appear in two rows). `mapping/MGRA_Nodes_mapping.csv` (`MGRA, nodes`).
- Checks in the script: IDs 0..N−1, edges = 2 × links without duplicates, one connected network, no stop-only node, pipeline pickup nodes = raw `IS_DEMAND` nodes, every zone has a node, every mapped node is a pickup node. All passed.

| | LG | DT |
|---|---|---|
| links touching the area → kept by link rules | 1,587 → 1,312 | 1,811 → 1,086 (17 rescued FC 0) |
| dropped as self-loop / parallel | 1 PRIVATE RD / 1 PRIVATE DY (kept TROY ST) | – / MARKET PL (kept W HARBOR DR) |
| **final links / nodes** | **1,285 / 1,045** | **1,071 / 724** |
| Func_Class 0 / 3 / 4 / 5 / 6 / 7 | 2 / 6 / 111 / 50 / 94 / 1,022 | 17 / 5 / 158 / 218 / 42 / 631 |
| street / alley / driveway links | 1,264 / 12 / 9 | 1,047 / 14 / 10 |
| links below 10 mph (floored) | 16 | 8 |
| **pickup nodes** (inside zones) | **1,032** (956) | **709** (633) |
| nodes outside the zones (routing only) | 77 | 82 |
| zones with a pickup node inside | 201 of 225 | 294 of 387 |
| zones using the access node | 24 | 93 |
| access nodes not in the network → nearest node | 5 (425–921 ft) | 14 (median 239, max 1,191 ft) |
| edges; travel time s min / median / max | 2,570; 0.4 / 11.3 / 101.4 | 2,142; 0.4 / 9.6 / 84.3 |
| mapping rows / zones with a single node | 980 / 53 | 726 / **228** |

- **Compared with the MGRA13-era networks** (SPEED == 20 rule): LG old 1,408 links, 1,099 nodes, **814** demand nodes → new **1,032**. DT old (`Data/0719_input/downtown_sd_Network_folder/pre_nodes.csv`, same node set as `data_new/Downtown_SD/Downtown_SD_Road_network.csv`) 1,186 links, 732 nodes, **497** demand nodes (495 inside zones; 228 of 364 MGRA13 zones, 259 of 387 MGRA15 zones) → new **709** (633 inside; 294 zones). The new rule adds arterials and collectors (25–35 mph), which serve much of Downtown.
- **From the earlier FC 4–7 count to the build:** LG 1,072 → 1,061 (alley/DY-only nodes) → 1,032 (self-loop/parallel/disconnected pieces). DT 730 → 715 → 699, then +10 from the rescued FC 0 streets = 709.
- **Watch for the demand step:** 228 of 387 DT zones (53 of 225 LG) have a single mapped node, so under the same-zone rule, same-zone trips in those zones are dropped. Count (2026-11-19, read-only pass over `final_trips`, kept trips with income > 0):

  | | LG | DT |
  |---|---|---|
  | kept trips | 43,225 | 201,416 |
  | same-zone trips | 3,394 | 23,431 |
  | … in a single-node zone → dropped | 527 (35 zones) | 15,563 (182 zones) |
  | … in a zone with ≥ 2 nodes → kept | 2,867 | 7,868 |
  | different zones whose only node is the same node → dropped | 0 | 1,102 |
  | different zones sharing a node (end redrawn, kept) | 90 | 1,639 |
  | **total dropped** | **527 (1.2%)** | **16,665 (8.3%)** |
  | dropped by mode | WALK 226, DRIVEALONE 129, SHARED3 102, SHARED2 64 | WALK 14,027, DRIVEALONE 1,218, BIKE 1,019, SHARED3 286, SHARED2 72 |

### Period travel times (decided 2026-11-19, option "B"; built 2026-11-19, see "Period travel time build" at the end)

- **Bike_Net has one `SPEED` only.** `TNED_HwyNet` (same gdb) has EA/A/MD/P/EV fields, but its times `ABTM*` = LENGTH (mi) ÷ SPD, free flow, identical in all 5 periods (2 DT links differ). No period information there.
- **Loaded (assigned) times:** `E:/sanmith/abm_runs/shaqa_new_result/report/hwyload_{EA,AM,MD,PM,EV}.csv` (2026-03-17). One row per TNED link (`ID1` = `HWYCOV0_ID`), `AB_Time`/`BA_Time` in minutes, `AB_Speed`/`BA_Speed` mph. `output_full_model` has no `report/`; `shaqa_new_result` holds the same resident trip files. Not verified that the assignment is from the same run. Other runs with reports: `Base_HF_3iter`, `scn_0000*`, `v260223_full_model_run`, …
- **What they contain** (TNED FC 2–7 links touching the area): even in EA the time includes a fixed delay (median 0, p75 12.6 s, p90 about 21–24 s per link; on LG FC 7 links median +12.6 s), probably signal/intersection delay. Sum of link times ÷ free flow: LG EA 1.52, AM 1.70, MD 1.57, PM 1.80, EV 1.53; DT 2.29 / 2.55 / 2.40 / 2.61 / 2.30. Congestion relative to EA: AM/PM median about 1.0, p90 1.2–1.4, max about 3.5; MD p90 1.1; EV = EA. 2 DT links have about 1e17 (blocked) in some periods.
- **TNED vs Bike_Net:** TNED streets (FC 2–7) lie on Bike_Net (98.6% LG / 98.8% DT of their length within 30 ft; about 95% within our networks). TNED freeways/ramps (FC 1, 8, 9) and connectors (FC 10) are not in Bike_Net and aren't needed. Rough match (≥ 80% of a Bike_Net link's length within 30 ft of a TNED street): LG 462 of 1,285 links, DT 776 of 1,071; FC 3–6 86–100%, FC 7 LG 21%, DT 58%.
- **Decided (user, 2026-11-19):**
  1. Source: the `shaqa_new_result` hwyload files (read on E:, not copied).
  2. Matching (Claude's call, report first): Bike_Net link vs TNED street links by geometry (30 ft buffer, ≥ 80% of length), length-weighted over several TNED pieces; direction from Bike_Net A→B vs TNED AN→BN (AB or BA time); if a direction has no time, use the other. Period link time = Bike_Net length ÷ (TNED length ÷ TNED period time). Name agreement (`RD20FULL` vs `NM`) only reported. First a test in the scratchpad: match rate by area and Func_Class, a QGIS layer.
  3. Unmatched links: free flow × the median period ÷ free-flow ratio of matched links in the same area and Func_Class, per period. **Superseded 2026-11-20: additive rule with the length-weighted mean in both areas** (see "Period travel time build").
  4. Blocked links: the link's slowest finite period time (look at them first).
  5. `edges.csv` keeps free flow (MTSM reads it as `free_flow_travel_time`).
  6. Outputs: `network/<prefix>_link_period_tt.csv` (per directed edge: FleetPy and Bike_Net IDs, `tt_free, tt_EA, tt_AM, tt_MD, tt_PM, tt_EV` in s, source, TNED IDs, overlap share) for MTSM; and a FleetPy folder `fleetpy_network/` with `base/{nodes.csv, edges.csv, crs.info}` and `0/` (EV), `10800/` (EA), `21600/` (AM), `32400/` (MD), `55800/` (PM), `68400/` (EV), each with `edges_td_att.csv` (`from_node, to_node, edge_tt`). `crs.info` = `EPSG:2230` (the old LG FleetPy network says `EPSG:32632`, a leftover from the FleetPy example).
  7. New script `build_period_travel_times.py`; the FleetPy folder written after it.
- **FleetPy:** `NetworkBasic` reads integer-named folders in the network dir (or a network-dynamics file), loads the latest folder ≤ `scenario_time` at start and switches in `update_network(sim_time)` when the clock equals a folder time. Check how a run that starts at 0 picks up `0/` before relying on it. `FleetPy_SanDiego`'s `load_tt_file` uses `Series.iteritems()`, removed in pandas 2.
- **MTSM:** `read_super_network` reads one `travel_time` per edge; periods are used only for fare/parking factors, and MTSM's periods (AM to 10:00, MD to 15:00, PM to 20:00, then EV) differ from ABM3's. The user will add the period lookup and choose which ABM3 column feeds which MTSM period.

### Period travel time build (2026-11-19)

- **Matching test** (scratchpad, before the build): TNED geometry runs AN→BN and Bike_Net geometry runs FNODE→TNODE (100% checked). One-way TNED links (WAY 1) have `BA_Time` 0. The only blocked TNED links near the areas are two DT `5TH HOV` links (FC 3, one-way, open only in PM), and no Bike_Net link matches them. A 60 ft buffer adds only 4 LG / 7 DT matched links, so 30 ft stays.
- **TNED speeds differ from Bike_Net speeds:** on matched links most Bike_Net 20 mph links are 25–30 mph in TNED (both areas). Period times therefore carry ABM3's speeds. Relative to TNED's own free flow (delay + congestion only): LG FC 4 1.18–1.32, other LG classes about 1.0; DT 1.9–2.7 (FC 6 1.0).
- **Unmatched links, first build (2026-11-19), user's option (i):** free flow × the median (period time ÷ Bike_Net free flow) of matched links of the same Func_Class. Consequence: LG local streets got faster than the old free flow (ratio about 0.84). Option (ii), a delay-only ratio on Bike_Net speeds, was previewed on 2026-11-20 and not chosen.
- **Current rule, both areas: additive + length-weighted mean (user, LG and DT, 2026-11-20).** Unmatched link time = free flow + class delay per ft × length. Class delay = sum over matched link-directions of the same area and Func_Class (FC 0 counted as 7) of (period time − TNED free flow LENGTH/SPD) ÷ the sum of their lengths. Unmatched links keep Bike_Net's posted speed and get ABM3's delay + congestion in seconds; `source` = `class_delay`. Settings `AREAS[...]["unmatched_rule"]` (`delay` / `ratio`) and `["class_stat"]` (`mean` / `median`); overrides `--unmatched-rule`, `--class-stat`.
  - Why the mean: per-link values are skewed (TNED's delay sits at the downstream end of each TNED link), so the median understated the class delay. AM, s per 100 ft, median → mean: LG FC 3 0.19 → 0.71, FC 4 0.66 → 1.77, FC 5 0.05 → 1.80, FC 6 0.02 → 0.90, FC 7 0.70 → 1.41; DT FC 3 2.16 → 2.38, FC 4 3.35 → 3.25, FC 5 3.75 → 4.23, FC 6 0.78 → 2.01, FC 7 4.54 → 5.04 (DT is less skewed).
  - **Backups of the first-build outputs (ratio + median):** `outputs_MGRA15/lemon_grove/backup_option_i_ratio/` and `outputs_MGRA15/downtown_sd/backup_ratio_median/` (each `network/<prefix>_link_period_tt.csv`, `_roads_period_tt.*`, `fleetpy_network/`). `--unmatched-rule ratio --class-stat median` reproduces both byte for byte (checked). Matched edges, `base/` and `network_dynamics.csv` are the same in all versions.
  - **LG results**, first build (ratio/median) → additive/median (trial) → additive/mean (current): network sum ÷ free flow AM 0.98 → 1.22 → 1.36; unmatched FC 7 (1,624 edges) AM 0.85 → 1.18 → 1.37; AM speed of unmatched local streets 21.1 → 15.2 → 13.1 mph (matched local 17.9, posted 18.0); unmatched FC 4/5 now 17.8/16.4 mph vs matched 19.5/18.5. Demand trips' AM shortest-path time, mean: 2.69 → 3.04 → 3.20 min (free flow 2.12); route share on unmatched links 46% → 28% → 24% (free flow 21%).
  - **DT results**, first build → additive/mean: network sum ÷ free flow EA / AM / MD / PM / EV 1.89 / 2.13 / 1.98 / 2.15 / 1.90 → 1.94 / 2.15 / 2.02 / 2.16 / 1.95; unmatched edges (604) AM 2.03 → 2.15 (matched 2.16); AM speed unmatched vs matched, mph: FC 4 13.9 → 11.6 (12.4), FC 5 14.4 → 10.2 (10.4), FC 6 25.0 → 17.2 (15.6), FC 7 7.3 → 7.2 (8.9). Demand trips' AM time 2.79 → 2.82 min (mean +1.3%, p95 +8%), 91% keep their route.
  - The results table below is the first build (ratio/median).
- **Name rule (Claude's call):** points along a parallel TNED street with a clearly different name are not used (spelling variants, direction prefixes, suffixes and leading zeros are allowed; UNKNOWN / DRIVEWAY / PRIVATE / ALLEY never reject). This cut matched links from 464 to 455 (LG) and 780 to 769 (DT). Rejected examples: IMPERIAL AV / LEMON GROVE, CADMAN ST / LISBON, PARK BL / 08TH, S 19TH ST / KEARNEY, TONY GWYNN DR / 07TH.
- **Results:**

| | LG | DT |
|---|---|---|
| matched links (share of length) | 455 of 1,285 (34.7%) | 769 of 1,071 (73.2%) |
| matched by FC 0 / 3 / 4 / 5 / 6 / 7 | 0/2, 6/6, 107/111, 41/50, 89/94, 212/1,022 | 15/17, 5/5, 152/158, 200/218, 40/42, 357/631 |
| class ratio EA / AM / MD / PM / EV, FC 4 | 1.16 / 1.17 / 1.16 / 1.21 / 1.16 | 1.72 / 1.87 / 1.79 / 1.87 / 1.72 |
| FC 5 | 1.00 / 1.02 / 1.00 / 1.02 / 1.00 | 1.61 / 1.90 / 1.75 / 1.88 / 1.62 |
| FC 6 | 1.17 / 1.18 / 1.17 / 1.17 / 1.17 | 1.40 (all) |
| FC 7 (and FC 0) | 0.84 / 0.85 / 0.84 / 0.84 / 0.84 | 1.85 / 2.10 / 1.93 / 2.12 / 1.87 |
| **sum of edge times ÷ free flow** EA / AM / MD / PM / EV | **0.92 / 0.98 / 0.93 / 0.97 / 0.92** | **1.89 / 2.13 / 1.98 / 2.15 / 1.90** |
| median edge time s: free / AM | 11.3 / 10.5 | 9.6 / 21.4 |

- The two directions of a link can differ a lot (e.g. LG HIGH ST 8.5 s one way, 18.4 s the other): TNED's delay sits at the downstream end of each TNED link and is spread over its length.
- **Outputs** (`outputs_MGRA15/<area>/`): `network/<prefix>_link_period_tt.csv` (per directed edge in `edges.csv` order: FleetPy and Bike_Net node IDs, ROADSEGID, name, Func_Class, distance m, `tt_free, tt_EA, tt_AM, tt_MD, tt_PM, tt_EV` s, `source` = tned / class_delay (class_ratio in the first build), `match_share`, `tned_ids`); `network/<prefix>_roads_period_tt.shp` (QGIS; AB_/BA_ period times, `share`); `fleetpy_network/` = `base/{nodes.csv, edges.csv, crs.info}` + `0/ 10800/ 21600/ 32400/ 55800/ 68400/edges_td_att.csv` + `network_dynamics.csv` (`simulation_time,travel_time_folder`, one row per folder; added 2026-11-19, period files unchanged by that rerun). `tt_free` equals `edges.csv` travel_time (checked).
- **MTSM's FleetPy and network_dynamics (checked 2026-11-19 against `ritunuci/calibration_project`, which uses `nw_dynamic_f,network_dynamics.csv`):**
  - The user will use only the network_dynamics mechanism, with no other FleetPy change (no stochastic times, no peak multipliers as in calibration_project).
  - MTSM's `fleetpy/src/routing/NetworkBasic.py` already reads the file (`_load_tt_folder_path`) and loads `<folder>/edges_td_att.csv` (`edge_tt`) when `update_network(sim_time)` hits a listed time exactly. `NetworkBasicWithStore` clears its travel-time cache on each load.
  - Needed in MTSM:
    1. `.iteritems()` → `.items()` at `NetworkBasic.py:249` (pandas 2);
    2. `nw_dynamic_f,network_dynamics.csv` in the constant config;
    3. the period folders and `network_dynamics.csv` added to `fleetpy_runner.LG_STAGED_INPUTS`, which copies only `base/`.
  - With MTSM's `start_time 0` and `time_step 60`, `0/` loads at the first step.
  - `PoolingIRSOnly` logs "inform_network_travel_time_update not implemented" at each load. It is harmless: the operator shares the routing engine.
- **FleetPy timing:** the routing engine is created without `scenario_time` (`FleetSimulationBase.py:230`), so it starts on `base/edges.csv` (free flow). A period folder loads only when `update_network(sim_time)` is called with exactly its time. A run from 0 with a time step dividing the folder times picks up every folder. A run that starts at e.g. 5:00 uses free flow until 6:00 (AM).

### Freeways and ramps from TNED (decided and built 2026-11-20; see "Freeway build" at the end of this section)

Bike_Net has no freeways or ramps. The user wants them in the network, taken from `TNED_HwyNet` (FC 1 freeway, 8 freeway-to-freeway connector, 9 ramp).

- **Decided (user, 2026-11-20):**
  - Add TNED FC 1/8/9 links that touch the area's MGRA15 union, **driven in their real direction only** (TNED one-way links get one edge). Streets stay driven both ways.
  - **Include HOV, toll and reversible lanes.** Step 0 found none: every link has HOV 1, no tolls, no period-dependent lanes, no blocked times. If a blocked time ever turns up, use a very large time in that period, not the slowest-finite rule.
  - Ramps join streets only at ramp ends (TNED nodes that also end a TNED FC 2–7 street), never where geometries cross.
  - **Ramp end → our network:** snap to the nearest node of ours **within 100 ft whose street name agrees** (`names_agree`). If none, and a same-name link of ours lies within 30 ft (mid-block ramp), **split that link at the ramp point** (user, 2026-11-20).
  - **The street network stays exactly as built** (same links, nodes, pickups, mapping). **No reconnect step** (user, 2026-11-20): letting freeways reconnect the dropped street pieces joins none of them in either area (College Grove Wy has an on-ramp only).
  - Keep only freeway links in the largest **strongly** connected part (you can get on and off inside the area).
  - Freeway and split nodes are not pickup nodes and get IDs above Bike_Net's maximum (200,004,947; TNED HNODE max 33,493), so existing FleetPy IDs, the mapping and the demand files should stay unchanged (to be checked byte for byte).
  - Freeway edges take free flow (LENGTH/SPD) and period times straight from TNED/hwyload; they are left out of street matching and the class-delay statistics. Split street pieces take the parent link's times, shared by length.
- **Plan (code):** `build_bikenet_networks.py` (add freeways, snap/split, direction column, SCC); `process_network.create_network_from_raw_edges` (one edge for one-way links when the direction column exists; old files unchanged); `build_fleetpy_network_mapping.py` (edge-count and strong-connectivity checks); `build_period_travel_times.py` (freeway edges from hwyload; split pieces). Back up current outputs first; update README.
- **Step 0 check (2026-11-20, read-only; scratchpad `fwy_step0.py`, `fwy_step0_<prefix>.gpkg`):**

  | | LG | DT |
  |---|---|---|
  | FC 1/8/9 links touching the area | 68 (17.9 mi): 29 / 7 / 32 | 150 (25.7 mi): 51 / 21 / 78 |
  | freeways | SR-94, SR-125 | I-5, SR-94, SR-163 |
  | one-way | 68 | 149 (1 two-way) |
  | ramp ends meeting a TNED street | 13 | 39 |
  | snapped to a node (≤ 100 ft, name agrees) | 9 (median 2 ft) | 33 (median 10 ft; one at 105 ft slipped past the bbox query; the build must test distance strictly) |
  | mid-block, link split | 3: Massachusetts Av, Federal Bl, Jamacha Rd (0–12 ft from the link, 150–360 ft from a node) | 5: State, Cedar, Pershing, B St, G St (0–19 ft; 120–180 ft) |
  | left out | College Grove Wy (dropped piece, on-ramp only) | Ash St / 11th Av (ambiguous; check in QGIS) |
  | **kept, snap + split** | **27 links, 5.7 mi** (snap only 17, 2.5 mi) | **101 links, 15.0 mi** (snap only 93, 13.2 mi) |
  | new freeway nodes / split links (2 of 3 LG splits used) | 20 / 2 | 70 / 5 |
  | dropped: after the area's last off-ramp / before its first on-ramp | 23 links 6.7 mi / 15 links 4.9 mi | 21 links 4.1 mi / 27 links 6.5 mi |

  - LG keeps SR-94 between its LG interchanges and a short piece of SR-125 SB; most of SR-125 and the SR-94/SR-125 connectors lead to ramps outside the area. DT keeps most of I-5; most of SR-163 and SR-94 drop out.
  - All street nodes stay in the strongly connected part (LG 1,045, DT 724).
  - hwyload AM time ÷ TNED free flow: freeway mainline median 1.17–1.18, p90 1.37 (LG) / 1.85 (DT). Ramps carry large fixed delays, probably ramp meters: LG ramp links median +25 s, max +158 s; DT max +121 s (one 80 ft DT ramp takes 2 min in AM).
  - DT oddities: a ramp named "5TH HOV" coded HOV 1; 13 links with `MINMODE 600` (meaning unknown; HOV and toll fields show no restriction). 65 LG / 95 DT TNED rows (FC 99, two FC 10) have no ID/AN/BN; skip them.
- **MTSM consequence (checked in `ritunuci/Multimodal_Transportation_System_Model` b38d7ba, `pre_process/build_super_network.py`):** it puts a walk link on every road edge, draws virtual stops from all road nodes and joins FRT stops to the nearest road node, and assumes every road segment has two edges. With freeways in, MTSM must use only street edges/nodes for walking, virtual stops and stop joining; the car network (`auto_edges.csv`) and microtransit driving can use all edges. Plan: write a file that marks freeway edges and nodes for MTSM (format to agree with the user).
- **Freeway build (2026-11-20)**, all four scripts rerun with `--overwrite`:
  - **Backup first:** `outputs_MGRA15/<area>/backup_streets_only/{network,mapping,demand,fleetpy_network}` (md5 verified). `build_bikenet_networks.py --no-freeways` + steps 3–5 reproduce every CSV byte for byte (checked in the scratchpad); `.dbf` files differ only in the header date, and `_roads_period_tt.shp` now also has `LINK_KIND`.
  - **Code:** `build_bikenet_networks.py` `add_freeways()` + `scc_labels()` (imports `names_agree` from `build_period_travel_times.py`), `--no-freeways`; `process_network.create_network_from_raw_edges` writes one edge for `ONEWAY == 1` when the column exists; `build_fleetpy_network_mapping.py` `fix_pre_nodes()` (stop-only reset + pickup flag off for split nodes), new checks (edge count with one-way links, strong connectivity), writes `<prefix>_edge_kind.csv` (`from_node, to_node, ROADSEGID, link_kind`) and `<prefix>_node_kind.csv` (`node, raw_node, kind` street/freeway, `split`) for MTSM (Claude's format; the user approved the build); `build_period_travel_times.py`: freeway edges from hwyload (`source` = `tned_freeway`, `match_share` empty, `BLOCKED_TT_S` = 1e6 s), split pieces timed as their whole parent and shared by length.
  - **New columns:** raw edges `ONEWAY, PARENT_SEGID, TNED_ID`; raw nodes `NODE_KIND` (street/freeway/split). Freeway rows: `Func_Class` = TNED FC, `RD20FULL` = TNED NM, `LINK_KIND` freeway/fwy_connector/ramp. IDs: freeway node 300,000,000 + HNODE, split node 310,000,000 + HNODE, freeway ROADSEGID 900,000,000 + HWYCOV0_ID, split piece 800,000,000 + 10 × parent + k.

  | | LG | DT |
  |---|---|---|
  | ramp ends: snapped / split candidates / not joined | 9 / 3 / 1 (College Grove) | 32 / 6 / 1 (Ash St) |
  | splits used (links split) | 2: Massachusetts Av, Jamacha Rd (Federal Bl unused) | 6 on 5 links: State, Cedar, Pershing ×2, B St, G St |
  | dropped as parallel (duplicate ramp branch, faster kept) | 37235 (SR-94 WB off-ramp branch to Lemon Grove Av) | 30890 (5th Av on-ramp to I-5 SB; "5TH HOV" 50795 kept) |
  | **freeway links kept** | **27, 5.74 mi** (11 freeway, 1 connector, 15 ramp; all one-way) | **100, 14.90 mi** (31 / 8 / 61; 99 one-way) |
  | links / nodes / edges | 1,314 / 1,067 (20 freeway + 2 split) / 2,601 | 1,177 / 800 (70 + 6) / 2,255 |
  | sum of edge times ÷ free flow EA / AM / MD / PM / EV | 1.256 / 1.369 / 1.275 / 1.365 / 1.258 | 1.903 / 2.129 / 1.981 / 2.142 / 1.907 |
  | freeway edges alone (AM; by kind) | 2.46 (freeway 1.17, connector 2.0, ramp 3.75) | 1.62 (1.35 / 1.18 / 1.80) |

  - **Unchanged (checked):** first N rows of `nodes.csv`; `mgra_access`; mapping files and all demand files byte-identical to the backup; street period times on unsplit edges exactly equal; split pieces sum to the parent's times (≤ 3e-14 s); class delays and match counts as before.
  - **Effect on demand trips** (shortest paths over all OD pairs, scratchpad `compare_fwy.py`): no trip slower. Mean time LG free / EA / AM / MD / PM / EV −0.2 / −0.7 / −0.1 / −0.5 / −0.1 / −0.6%, trips using a freeway 0.8 / 3.2 / 0.3 / 2.4 / 0.5 / 3.1%; DT −1.3 / −2.7 / −1.7 / −2.6 / −1.8 / −2.7%, trips using a freeway 2.3 / 4.7 / 3.2 / 4.5 / 3.3 / 4.7%. Trips that use one save 10–21% on average (min ratio 0.27). Fewer use them in AM/PM because of the ramp delays.
  - **Extent stays as is (user, 2026-11-20).** LG keeps only 5.7 of the 14.9 freeway miles inside the area, because its interchanges sit near the edges (on-ramps that lead out, off-ramps reached only from outside). A scratchpad test (`lg_extend_test.py`) widened the LG street + freeway extent by 0.5 mi / 1 mi: freeway kept 10.3 / 24.8 mi, but freeway use did not rise (AM 0.32% → 0.27% of trips); mean trip time −0.5 to −0.9% in every period, all from about 2% of trips cutting across streets just outside the boundary (both buffers identical); no trip slower. Not adopted: 2–3.4× the nodes, vehicles outside the area, and the dropped street pieces would reconnect (pickups, mapping and demand would change).
  - The first real run failed: QGIS (three `qgis-bin.exe`) held `Lemon_grove_roads.shp`. `Lemon_grove_raw_edges.csv` had been rewritten; it was restored from the backup (outputs checked equal to the backup) before the rerun.

## Demand file redo for ABM3 (decisions 2026-11-19; built 2026-11-19, see "Demand build")

### Confirmed by the user

- **Trips:** from `data_new/demand_data/final_trips.csv`, with both ends in the area's `<prefix>_MGRA15.csv`.
  - **All purposes and all modes are kept**, including school bus (`SCH_BUS`), as in the old work. The old ABM2 file had only `depart, origin, destination, trip_mode`, and `select_trips` filtered only by zone.
- **Time:** period p starts at 3:00 + (p−1)×30 min, and `rq_time` = start of the period in seconds + a random 0–1799 s.
  - Claude's call: periods 43–48 (after midnight) go past 86,400 s instead of wrapping, so time keeps increasing. That is 200 LG and 1,497 DT trips.
- **Income:**
  - `income` = household `income` / `num_workers`, with `num_workers` 0 treated as 1. Both columns are in `final_households.csv`, joined on `household_id`.
  - **Trips from households with income 0 are removed.**
- **Cars:**
  - `auto_available` = True if the household's `auto_ownership` ≥ 1;
  - `num_autos` = `auto_ownership` (0–4).
- **Output format: the TRPartA layout without `transit_15min_acc`, plus four columns.** That gives 21 columns:
  `rq_time, start, end, request_id, bt_c_0, bt_c_ivt, bt_c_gas, bt_t_0, bt_t_wk, bt_m_wt, bt_f_wt, bt_m_ivt, bt_f_ivt, bt_f_trfer, bt_t_fr, income, transit_pass, auto_available, num_autos, purpose, trip_mode`.
  - **`transit_15min_acc` was dropped** (user decision, 2026-11-19). TRPartA filled it from `emp_15min` in the old TR_PartA trip file; ABM3 has nothing equivalent. The user will make the MTSM reader treat it as optional (see "MTSM reader" below).
  - **`parking_dur_min`, Downtown only, added as a 22nd column** (user decision, 2026-11-19):
    - One value for every DT request, whatever its mode.
    - Lemon Grove files have no such column.
    - Values come from the user's distribution, previously `gen_parking_dur()`: `scipy.stats.lognorm(s=0.5341062374951262, loc=-23.623502968177117, scale=95.44354175433033)`, in minutes.
    - **scipy is not installed**, so it is drawn with numpy as `loc + rng.lognormal(mean=ln(scale), sigma=s)`. That is the same distribution, drawn from the seeded stream, so values are reproducible but don't equal a scipy run.
    - **Clipped at 0.** About 0.45% of draws are negative (the minimum is −23.6). MTSM charges 0 for them either way.
    - Distribution: median 71.8, mean 86.5, 5th/95th/99th percentiles 16 / 206 / 308 min.
  - **Betas:**
    - TRPartA means and SDs (768dca4): c_ivt 0.2, t_0 0.242, t_wk 0.3, m_wt/f_wt 0.15, m_ivt/f_ivt 0.15, f_trfer 0.5; c_0 0.
    - Separate `bt_m_wt` and `bt_f_wt`.
    - `bt_t_fr` = the same draw as `bt_c_gas`.
    - Draws are clipped as in TRPartA.
  - **Cost beta mean** = 0.5 × mean_income / income. **mean_income is recomputed** from the new per-worker income, as the mean over the area's kept trips (2026-11-19 check: LG 64,775, DT 81,797). The old constants LG 70,679.3 and DT 88,894.5 were ABM2-era household income.
  - **`transit_pass`** = the person's `transit_pass_ownership` from `final_persons.csv`, joined on `person_id`. It replaces TRPartA's random 10%. Its values have not been checked yet.
  - **`request_id`:**
    - written as an integer;
    - rows sorted by numeric `rq_time`, then renumbered 0..N−1.
  - **Seed:** 42.
  - **Files:** the full file plus a **1% debug sample**, as in the old from-scratch pipeline.
  - **Same-zone trips:**
    - kept, with the end node drawn from the zone's pickup nodes other than the start node;
    - dropped if the zone has only one pickup node, and the count is reported.
    - The same rule applies whenever start and end would otherwise be the same node.
    - **Confirmed after the count (user, 2026-11-19): drop them** (option (a)), rather than moving the end to the nearest other node. LG 527, DT 16,665 (84% WALK).
- **Purpose and mode in the trip file:** FleetPy turns every extra column into a request attribute (`src/demand/TravelerModels.py:74`, `setattr`). The names above don't clash with any FleetPy attribute. **Don't name a column `mode`.** FleetPy's loader warns that `iterrows()` with mixed column types can behave oddly (`demand.py:108`), so test a file with text columns in FleetPy once one exists.
- **Trace file** keyed by `request_id`: `trip_id, household_id, person_id, origin MGRA, destination MGRA, depart, trip_mode, purpose`.
- **Outputs:**
  - go to `outputs_MGRA15/<area>/{network,mapping,demand}/` in this repo, added to `.gitignore` (the repo is public);
  - the new code does not rewrite `nodes.csv`/`edges.csv` as a side effect (TRPartA did).
- **Zone-system guard (Claude's call):** stop with an error if the study-area list lacks `share_in` or has IDs not in `mgra15.shp`, or if the trip file lacks `trip_id` / `trip_period`. Print the trip count (LG should be 43,737).

### Demand build (2026-11-19, `build_abm3_demand_files.py`, about 3 min)

- **How it works:** one pass over `final_trips.csv` for both areas. Trips are sorted by `trip_id`, then each area gets its own `np.random.default_rng(42)`, so running one area alone gives the same file. Draws are made in a fixed order: rq_time seconds, start/end uniforms, betas (column by column), DT parking, then the debug sample. Values are therefore repeatable but differ from a row-by-row TRPartA run.
- **Node draw:**
  - `start` is a random node of the origin zone.
  - `end` is a random node of the destination zone other than `start`.
  - If the destination's only node is the start node, `start` is redrawn from the origin's other nodes (the first run lacked this and dropped LG 9 / DT 350 trips too many).
  - A trip is dropped only when both zones have just that one node.
- **Choices (Claude's call):**
  - mean_income is taken over the trips written (LG 64,785.7, DT 81,410.0).
  - The debug file keeps the full file's `request_id`s (exactly 1% of rows, random), so one trace file serves both.
- **Outputs** in `outputs_MGRA15/<area>/demand/`:
  - `<area>_trips_nodes_study_area_with_beta.csv` (LG 21 columns; DT 22 with `parking_dur_min`);
  - `<area>_debug_trips_nodes_study_area_with_beta.csv`;
  - `<area>_trips_trace.csv`;
  - `<area>_dropped_trips.csv` (with `reason` = `income_0` / `no_distinct_end_node`).
  - `auto_available` is written as True/False.
- **Checks** (all passed): start ≠ end; every node belongs to its trip's zone mapping; no NaN; rq_time sorted; transit_pass in {0, 1}.

| | LG | DT |
|---|---|---|
| both ends in area | 43,737 | 208,327 |
| dropped income 0 / no distinct node | 512 / 527 | 6,911 / 16,665 |
| **requests written** / debug | **42,698** / 427 | **184,751** / 1,848 |
| EA / AM / MD / PM / EV | 281 / 10,171 / 17,343 / 11,006 / 3,897 | 1,589 / 26,206 / 92,027 / 43,098 / 21,831 |
| transit_pass 1 | 5,103 (12%) | 68,315 (37%) |
| no auto | 1,328 | 31,617 |
| parking_dur_min | – | 771 negative draws set to 0; median 71.8, p95 205.7 |

- `transit_pass_ownership` in the region is 0/1 only (548,901 of 3,283,069 persons = 1).
- **Cost-beta income floor $5,000 (user decision, 2026-11-19; `INCOME_FLOOR` in the script).**
  - Cost beta mean = 0.5 × mean_income / max(income, 5,000).
  - The `income` column and mean_income keep the real, unfloored values (Claude's call).
  - Floored rows: LG 366, DT 3,271.
  - After the floor: `bt_c_gas` mean / p99 / max LG 0.85 / 5.8 / 7.6, DT 1.12 / 8.1 / 9.7. No row is above 10.
  - Rerun on 2026-11-19. Trips, nodes, times and all other betas are unchanged (same draws).
- *Before the floor* (first build): some per-worker incomes are tiny (minimum LG $49, DT $12), and cost beta mean = 0.5 × mean_income / income made them huge:

  | | LG | DT |
  |---|---|---|
  | `bt_c_gas` max | 661 | 3,392 |
  | `bt_c_gas` 99th / 99.9th percentile | 5.9 / 28.7 | 15.2 / 118 |
  | rows with `bt_c_gas` > 10 | 192 | 2,761 |
  | income < $1,000 / < $5,000 | 20 / 366 | 642 / 3,271 |

  `bt_t_fr` is the same draw.
- Not yet tested in FleetPy (text columns `purpose`, `trip_mode`). **Wait for the user's go-ahead.**

### ABM3 trip check (2026-11-19, read-only pass over final_trips, about 2 min)

- **Households overall:** 43,940 with income 0, none negative; 325,237 with 0 workers.

| | LG | DT |
|---|---|---|
| trips with both ends in area | 43,737 (14,666 households) | 208,327 (67,832 households) |
| **removed, household income 0** | **512 (1.2%), 190 households** | **6,911 (3.3%), 2,107 households** |
| kept | 43,225 | 201,416 |
| kept trips from 0-worker households (divisor 1) | 5,020 | 28,345 |
| per-worker income: mean / median | 64,775 / 53,025 | 81,797 / 61,809 |
| household income, mean over kept trips | 104,122 | 141,100 |
| auto_ownership 0 / 1 / 2 / 3 / 4 | 1,350 / 7,586 / 18,514 / 9,409 / 6,366 | 35,032 / 54,506 / 75,152 / 23,309 / 13,417 |
| same origin and destination MGRA (kept) | 3,394 | 23,431 |
| depart periods 43–48 | 200 | 1,497 |

- **LG modes:** DRIVEALONE 16,164, WALK 12,496, SHARED2 7,811, SHARED3 6,047, BIKE 237, TAXI 230, WALK_LOC 99, TNC_SINGLE 60, EBIKE 35, WALK_PRM 30, SCH_BUS 8, KNR_PRM 6, PNR_PRM 2.
- **DT modes:** WALK 136,786 (68%), DRIVEALONE 43,627, BIKE 6,509, SHARED3 6,365, SHARED2 3,758, TAXI 1,141, TNC_SINGLE 1,125, WALK_PRM 864, TNC_SHARED 550, EBIKE 327, WALK_LOC 203, KNR_PRM 70, SCH_BUS 60, others under 15.
- **Purposes:** home, escort, shopping, othdiscr, othmaint, work, school, eatout, atwork, social; DT also has univ.
- `final_persons.csv` has `transit_pass_ownership` and `transit_pass_subsidy`. TRPartA instead set `transit_pass` = 1 at random for 10% of rows.

### MTSM reader (the final model; the user will make `transit_15min_acc` and `parking_dur_min` optional)

- **The final model is `ritunuci/Multimodal_Transportation_System_Model`**, checked at b38d7ba (2026-11-17).
  - **Parking units are minutes.** The column is `parking_dur_min`. `choice.py:93-94` computes `ceil((parking_dur / 2) / parking_charge_interval_min)`, and `mtsm_data/input_parameter/mtsm_constant_configurations.csv` sets `parking_charge_interval_min,30` with the note "(min)".
  - `mtsm_model/helpers/network_algorithms.py` `read_request` (L79–117) reads these columns and raises a KeyError if any is missing: `rq_time, start, end, request_id`, the 11 betas (with `bt_m_wt`/`bt_f_wt`), `income, transit_15min_acc, transit_pass, parking_dur_min`.
  - **`transit_15min_acc`** is only stored (`iteration.py:66`) and written to results (`results.py:130, 213`). No choice uses it, but leaving the column out crashes the reader.
  - **`parking_dur_min`** is used in the car cost (`choice.py:93–98`: half the duration, per started 30 min, × `bt_c_gas` × the peak/off-peak factor). In MTSM's own LG demand file (`mtsm_data/lemon_grove/demand_data/lemon_grove_trips_nodes_study_area_with_beta.csv`, 3,742 rows, 19 columns) it is 0 in every row, so there is no parking cost at present.
  - `transit_pass` and `income` are only read and stored; nothing in `mtsm_model` uses them in a calculation.
  - The old `FleetPy_SanDiego/network_algorithms.read_request` (used by `run_examples_new_TRC_R1.py`) also needs `transit_15min_acc` and uses it in thresholds (L706–720). It doesn't read `parking_dur_min`.

### Bike_Net facts (checked 2026-11-19)

- **Layers:** `Bike_Net_2022_Build` has 183,886 links: ROADSEGID, RD20FULL, A, B, Distance, AB_Lanes, BA_Lanes, Func_Class, SPEED, … `Bike_Node_2022_Build` has 154,569 nodes: NodeLev_ID (unique), MGRA, TAZ, XCOORD, YCOORD. Both are EPSG:2230.
- **Built from Roads_All:** 1,121 of the 1,408 old LG ROADSEGIDs are in Bike_Net, and 1,113 of them have the same SPEED. Old FUNCLASS L corresponds to Func_Class 7 (783 of 789). The old SPEED == 20 rule meant local streets: 851 of 862 FUNCLASS L links are 20 mph.
- **Func_Class meaning**, taken from street names compared with TNED FC (there is no code table):

  | Func_Class | Meaning | TNED FC |
  |---|---|---|
  | 3 | prime arterial (a few short pieces) | 2 |
  | 4 | major arterial | 3 |
  | 5–6 | collectors | 4–5 |
  | 7 | local | 7 |
  | 10 | TAZ / MGRA connectors | 10 |
  | 0, −1 | paths, alleys, odd links | – |

  There are no freeways or ramps (TNED FC 1, 8, 9).
- **Node MGRA field:** only the 24,320 MGRA **centroid** nodes carry an MGRA value; street nodes do not.
- **Connectors:** every LG and DT zone has exactly one "MGRA Connector" street node. 216 of the 225 LG ones are on a local street, and 298 of the 387 DT ones.
- **SPEED:** has odd low values (1, 5, 8, 10, 15, 18), inherited from Roads_All.
- **Lanes:** 1/1 almost everywhere, including Broadway and Market, so they are not real car lanes.
- **One-way:** only about 100 DT links have lanes in just one direction, so one-way coding looks incomplete. TNED has 789 of 1,343 DT links as WAY 1.
- **Links touching the area:** LG 1,587 (1,312 drivable), DT 1,811 (1,122 drivable).
- **Pickup coverage by rule** (drivable links; nodes = all end nodes of links touching the area, including some just outside the zones; zones = zones with such a node inside):

  | Rule | LG nodes / zones (of 225) | DT nodes / zones (of 387) |
  |---|---|---|
  | FC 7 | 1,001 / 204 | 582 / 254 |
  | SPEED == 20 | 878 / 198 | 597 / 281 |
  | FC 4–7 | 1,072 / 206 | 730 / 291 |

  TNED FC 2–7 gives LG 122 zones and DT 329 zones. TNED's DT count is higher because its streets are split mid-block where TAZ connectors attach.

## FRT network (decisions 2026-11-23; built 2026-11-23 with `build_frt_network.py`, about 5 s)

### Decisions (user, 2026-11-23)

- **Source:** `data_new/network/EMMEOutputs.gdb` (`trrt`, `TransitRoute`, `trstop`; stop coordinates from `TNED_HwyNodes`/`TNED_RailNodes`). It equals the ABM3 run's transit input (`E:/sanmith/abm_runs/shaqa_new_result/input/{trrt,trlink,trstop}.csv` and its gdb, numeric compare; scenarioYear 2022). `data_new/gtfs_transit_net/` is SANDAG "Transit Routes 2025" (scenario 462): no stops and more frequent headways. Not used.
- **Outputs** = `build_super_network.py` inputs: `frt_stops.csv` (`stop_id,pos_x,pos_y`) and `frt_edges.csv` (`from_stop,to_stop,distance,travel_time,route`; directed rows), plus `frt_routes.csv` (reference: ABM3 headways, fare, length for MTSM's line cost).
- **Stop nodes:** one per TNED stop node × route × direction; `stop_id` = (route × 10 + dir) × 100,000 + TNED node.
- **`route`** = route number (both directions). MTSM's cost code treats it as a line run both ways (× 2).
- **Run time** = distance along the route ÷ 30 mph, rail included (not TNED `RUNTIME`). One headway for all routes (set in `build_super_network.py`).
- **One variant per route and direction:** Downtown keeps `510108/208`, `20102/202`, `120116/216` and drops `510119/219`, `20114/214`, `120112/212` and the peak-only 110, 280, 290 (`AREAS["downtown_sd"]["exclude"]`). LG excludes nothing.
- **Inside rule: a stop's TNED node within 50 ft of the MGRA15 union** (user, 2026-11-23; strict point-in-polygon would drop stops on boundary streets, e.g. route 4 at Jamacha Rd/Beacon). Links only between consecutive stops of a variant with both stops inside (a variant that leaves and comes back gets no link across the gap).
- **Routes 851 (LG) and 994 (DT) come back** with the 50 ft tolerance (user). Under strict point-in-polygon 851 had no stop inside and 994 one.
- **Join:** to the nearest street node of ours (`node_kind` street, split allowed, never freeway; tie → smaller node). `pos_x/pos_y` = that street node's coordinates, so MTSM's `nearest_walk_nodes` (which considers every node in `nodes.csv`, freeway nodes too, until I-33) picks exactly that node at 0 ft. Checked: with the true TNED locations no stop's nearest node is a freeway node either, so this is a safeguard. **All stops kept** (user); joins over 300 ft are listed.

### Step-0 facts (read-only, 2026-11-23)

- In `trstop`, `Stop_ID` is contiguous per variant and its order = `Milepost` order (strictly increasing, no zero steps); first milepost 0; last milepost = `TransitRoute.SHAPE_Length` / 5,280 exactly. So link distance = milepost difference.
- 76 variants visit a TNED node twice (loops, `Pass_Count` 2, e.g. 4105/4205 at Euclid); never on consecutive stops.
- Every stop node has TNED coordinates; trstop lon/lat vs TNED coordinates median 0.2 ft, max 0.9 ft.

### Build (2026-11-23), outputs in `outputs_MGRA15/<area>/frt/`

`frt_stops.csv`, `frt_edges.csv`, `frt_routes.csv`, `frt_stops.shp` (TNED locations), `frt_links.shp` (route line cut at the mileposts; all links got the route geometry). Column lists in the script docstring and README step 6.

| | LG | DT |
|---|---|---|
| TNED stop nodes in the area (of them ≤ 50 ft outside the zones) | 45 (7) | 115 (9); 112 used (398/598 have stops but no link) |
| routes | 7: 4, 520, 851, 856, 916, 917, 936 | 21: 2, 3, 4, 5, 7, 11, 12, 20, 83, 120, 215, 225, 235, 510, 520, 530, 901, 923, 929, 992, 994 |
| variants / stop nodes / links | 12 / 122 / 110 | 41 / 354 / 326 |
| street nodes with a stop | 45 | 109 (3 street nodes take two TNED stop nodes each) |
| link length sum | 28.46 mi | 58.07 mi |
| link m min / median / max | 86 / 322 / 2,401 | 62 / 224 / 1,594 |
| join ft median / p90 / max | 0 / 146 / 303 (Spring Vista/Sweetwater, one of the 851 stops near the dropped Sweetwater Rd piece) | 0 / 127 / 635 (County Admin Bldg, N Harbor Dr, 923/992) |
| repeated (from, to) pairs dropped | 0 | 0 |

- One-direction variants: LG 916 and 917 (dir 1 only), DT 992 (dir 1, a loop: 14 stop nodes, 26 links).
- Route ÷ straight-line distance: median 1.00; max LG 1.55 (936), DT 3.71 (923205 around a block), 3.07 (11118).
- 994 has fare $0 in trrt; all others $2.50.
- **Route tails inside the area are not in the network (checked 2026-11-23; user: keep as is).** A route line that runs on from its last inside stop to a stop outside has no link for that piece, though the line is inside the area (the both-stops-inside rule). LG (scratchpad `frt_lg_gap.py`):

  | Route | Last stop inside → next stop (ft outside) | Line inside, no link |
  |---|---|---|
  | 520 trolley, SW | Massachusetts Av → Encanto/62nd St (4,256) | 5,060 ft, both directions |
  | 520 trolley, NE | Lemon Grove Depot → Spring Street (3,150) | 1,882 ft |
  | 856/916/917/936, NW | College Pl/Federal → Racine/College Grove (484) | 1,194 ft |
  | 851, east | Blossom/Sweetwater → Tyler/Spring Grove (540) | 535 ft |
  | 916/917, SW | Madera/Primera → Madera/Hilger (748) | 331 ft |

  No effect on trips inside the area: there is no stop on these pieces, and no route loses a connection between two inside stops (in every LG variant, links = stops − 1). Rejected alternatives: add the next outside stop (Encanto would join a street node ~0.8 mi away) or a boundary stop where the line leaves the area (a stop where no station exists). DT not checked the same way.
- **MTSM test** (our copy of `build_super_network.py`, `--variant non_micro --headway 10`, output in the scratchpad only): both areas read fine; LG 122 stops, 45 walk nodes with a stop, links walk 2,601 / FRT 110 / FRT wait 244; DT 354 stops, 109 walk nodes, 2,255 / 326 / 708; largest stop → walk node distance 0.0. Walk links still include freeway edges (I-33).

## Working with this user

- When the user says "no code changes", only analyse and report. Make edits only when asked.
- Ask before making choices that are the user's to make (which areas, which pipeline branch, output format). Point out side effects that overwrite files.
- Check claims against the data on disk (headers, row counts, comparisons) rather than guessing.
