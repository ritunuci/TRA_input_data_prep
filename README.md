# TRA input data processing

Builds the **road networks**, **period travel times** and **trip-request (demand) files** for microtransit
studies in **Lemon Grove** and **Downtown San Diego**. The outputs feed:

- [FleetPy](https://github.com/TUM-VT/FleetPy), the fleet simulation;
- the Multimodal Transportation System Model
  ([`ritunuci/Multimodal_Transportation_System_Model`](https://github.com/ritunuci/Multimodal_Transportation_System_Model), MTSM), which runs FleetPy.

The inputs are SANDAG **ABM3** (ActivitySim) model outputs, which use the **MGRA Series 15** zone system.

The repository holds **code only**. No raw data or output file is committed, because the repo is public. To
run the pipeline, put your own copies of the raw files in the folders listed below.

---

## 1. Pipeline at a glance

```
 MGRA13 study area (old polygons)   mgra15.shp
             │                          │
             └────────┬─────────────────┘
                      ▼
 [1] build_mgra15_study_areas.py     → data_new/<area>/<prefix>_MGRA15.csv / .shp   (study-area zones)
                      │
 EMMEOutputs.gdb (Bike_Net_2022_Build, Bike_Node_2022_Build; TNED_HwyNet, TNED_HwyNodes for freeways)
                      ▼
 [2] build_bikenet_networks.py       → outputs_MGRA15/<area>/network/<prefix>_raw_edges.csv, _raw_nodes.csv,
                      │                 _mgra_access.csv, _roads.shp
                      ▼
 [3] build_fleetpy_network_mapping.py → network/pre_nodes.csv, pre_edges.csv, nodes.csv, edges.csv,
                      │                  <prefix>_edge_kind.csv, _node_kind.csv
                      │                  mapping/Nodes_MGRA_mapping.csv, MGRA_Nodes_mapping.csv
 EMMEOutputs.gdb (TNED_HwyNet) + hwyload_{EA,AM,MD,PM,EV}.csv
                      ▼
 [4] build_period_travel_times.py    → network/<prefix>_link_period_tt.csv, _roads_period_tt.shp
                      │                 fleetpy_network/ (base/ + 6 period folders + network_dynamics.csv)
 final_trips.csv, final_households.csv, final_persons.csv, mgra15.shp
                      ▼
 [5] build_abm3_demand_files.py      → demand/<area>_trips_nodes_study_area_with_beta.csv (+ debug, trace, dropped)
```

Each step reads the outputs of the step before it, so run them in this order. Every script:

- builds **both areas** by default (`--areas lemon_grove downtown_sd`);
- **refuses to overwrite** existing outputs unless you pass `--overwrite`;
- stops with an `ERROR:` message when an input check fails.

Steps 2–5 accept `--out-root` (default `outputs_MGRA15/`).

---

## 2. Environment

The scripts were run with **Python 3.14.7** and these packages:

| Package | Version |
|---|---|
| numpy | 2.5.2 |
| **pandas** | **3.0.5** |
| geopandas | 1.1.4 |
| pyogrio | 0.13.0 (GDAL 3.12.4) |
| shapely | 2.1.2 |
| pyproj | 3.7.2 |

Earlier pandas 2.x versions should work, but they have not been tested. scipy is **not** needed.

```
pip install numpy pandas geopandas pyogrio shapely pyproj
```

**Run every script from the repository root.** `build_fleetpy_network_mapping.py` imports
`process_network.py` from the same folder.

### Windows pitfall: numpy crashing with exit code 127 / `0xc06d007f`

On a machine where another program puts its own Intel OpenMP/TBB DLLs on the system `PATH`
(`libiomp5md.dll`, `tbb12.dll`; here it was EMME 25 / Bentley OpenPaths), numpy's MKL loads those DLLs instead of the
environment's. The process then dies **silently** at the first BLAS/LAPACK call.

The fix is to put the conda environment's DLL folders first on `PATH`, which is what `conda activate` does:

```bash
# Git Bash
E=/path/to/conda/envs/<env>; W="$(cygpath -u "$E")"
export PATH="$W:$W/Library/mingw-w64/bin:$W/Library/usr/bin:$W/Library/bin:$W/Scripts:$W/bin:$PATH"
"$E/python.exe" build_bikenet_networks.py
```
```powershell
# PowerShell
$E="C:\path\to\conda\envs\<env>"; $env:PATH="$E;$E\Library\mingw-w64\bin;$E\Library\usr\bin;$E\Library\bin;$E\Scripts;$E\bin;$env:PATH"
& "$E\python.exe" build_bikenet_networks.py
```

---

## 3. Raw inputs

All paths are relative to the repository root. `data_new/` and `data_current_script/` are git-ignored, so
create them and copy your files in.

| File | Source | Used by | Fields the code relies on |
|---|---|---|---|
| `data_new/mgra15/mgra15.shp` | SANDAG ABM3 input, MGRA Series 15 polygons, **EPSG:2230 (US ft)** | 1, 5 | `MGRA` (unique, 1..24,321), `TAZ`, `City` |
| `data_new/lemon_grove/QGiS_new/MGRA13.shp` | MGRA13 polygons of the old Lemon Grove study area | 1 | `MGRA` |
| `data_current_script/Ritun/Lemon Grove/Lemon_grove_MGRA.csv` | old Lemon Grove MGRA13 list (239 IDs) | 1 | `MGRA` |
| `data_new/Downtown_SD/DT_SD_mgra.shp` | MGRA13 polygons of the old Downtown study area | 1 | `MGRA` |
| `data_new/Downtown_SD/Downtown_SD_MGRA.csv` | old Downtown MGRA13 list (364 IDs) | 1 | `MGRA` |
| `data_new/network/EMMEOutputs.gdb`, layer `Bike_Net_2022_Build` | SANDAG ABM3 network export (bike network built from Roads_All) | 2 | `A`, `B`, `ROADSEGID`, `RD20FULL` (street name), `Func_Class`, `SPEED` (mph), `AB_Lanes`, `BA_Lanes`, `Distance`, geometry (EPSG:2230) |
| same gdb, layer `Bike_Node_2022_Build` | same | 2 | `NodeLev_ID`, `XCOORD`, `YCOORD`, `MGRA` (set only on MGRA centroid nodes) |
| same gdb, layer `TNED_HwyNet` | ABM3 highway network | 2 (freeways), 4 | `HWYCOV0_ID`, `NM` (name), `FC`, `LENGTH` (**miles**), `WAY`, `SPD`, `AN`, `BN`, `BALN*` (BA lanes per period), `HOV`, `TOLL*`, geometry (digitised AN→BN) |
| same gdb, layer `TNED_HwyNodes` | same | 2 | `HNODE`, `X_COORD`, `Y_COORD` |
| `hwyload_EA.csv`, `hwyload_AM.csv`, `hwyload_MD.csv`, `hwyload_PM.csv`, `hwyload_EV.csv` | ABM3 run `report/` folder: assigned (loaded) link times per period (streets and freeways) | 4 (`--hwyload-dir`) | `ID1` (= `HWYCOV0_ID`), `AB_Time`, `BA_Time` (**minutes**) |
| `data_new/demand_data/final_trips.csv` | ABM3 resident model output (12 GB, 12.8 M rows) | 5 | `trip_id`, `person_id`, `household_id`, `origin`, `destination` (MGRA15), `depart` (half-hour period 1–48), `trip_period`, `trip_mode`, `purpose` |
| `data_new/demand_data/final_households.csv` | ABM3 output | 5 | `household_id`, `income`, `num_workers`, `auto_ownership` |
| `data_new/demand_data/final_persons.csv` | ABM3 output | 5 | `person_id`, `transit_pass_ownership` (0/1) |

Notes on the inputs:

- **The `--hwyload-dir` default is a local path on the original machine.** Always pass your own folder. Use the
  `report/` folder of the same ABM3 run that produced the trips, if you have it.
- **Only resident trips are used.** ABM3's crossborder, visitor and airport models write separate trip files, and
  this pipeline does not read them.
- **Read the 12 GB trip file in chunks.** Step 5 reads only the columns above, 2 M rows at a time. A full pass
  takes about 2–5 minutes.
- **Step 1 is only needed to carry an old MGRA13 study area over to MGRA15.** If you already have an MGRA15 zone
  list, see section 8.

---

## 4. The steps

### Step 1: study areas in MGRA15 (`build_mgra15_study_areas.py`)

**Why:** ABM3 uses MGRA15 zone IDs. The old study areas were MGRA13 lists, and MGRA13 IDs point to **different
places** in MGRA15, so they can't be reused as they are.

**Method:**

- study area = the union of the area's MGRA13 polygons;
- for each MGRA15 polygon, `share_in` = area inside the study area ÷ polygon area;
- keep zones with `share_in ≥ --min-share` (default 0.5).

The script checks that the MGRA13 polygon IDs equal the MGRA13 list.

**Options:** `--areas`, `--min-share`, `--exclude <MGRA15 ...>`, `--mgra15-shp`, `--overwrite`.

**Outputs:** `data_new/<area dir>/<prefix>_MGRA15.csv` (`MGRA, TAZ, City, share_in`) and `<prefix>_MGRA15.shp`
(EPSG:2230), where the prefix is `Lemon_grove` or `Downtown_SD`. The `share_in` column is required by step 5.

The current outputs were built with:
```
python build_mgra15_study_areas.py
python build_mgra15_study_areas.py --areas downtown_sd --exclude 8745 --overwrite
```
MGRA15 8745 is part of San Diego International Airport. It has 0 households and its jobs are almost all
transportation/warehousing. It enters through an MGRA13 zone that lies 99% inside it, so it is excluded by hand.
**If you rerun without `--exclude 8745`, it comes back.**

Result: Lemon Grove 225 zones (the smallest share_in is 0.778), Downtown 387 zones.

### Step 2: road networks (`build_bikenet_networks.py`)

**Source:** `Bike_Net_2022_Build` for the streets. Bike_Net is built from SANDAG's Roads_All street centrelines,
the same source as the older MGRA13-era networks, but it matches MGRA15. (Do **not** combine MGRA13-era Roads_All
extracts with MGRA15 zones.) Bike_Net has no freeways or ramps; those are added from TNED (see "Freeways and ramps"
below).

**Link rules:**

| Rule | Detail |
|---|---|
| Extent | links that intersect the union of the area's MGRA15 polygons; then only the **largest connected component** is kept |
| Kept | `Func_Class` 3–7 with `AB_Lanes + BA_Lanes > 0` (bike-only links have no lanes) |
| Dropped | Func_Class 10 (TAZ/MGRA connectors), Func_Class 0/−1 (paths, walkways, ped bridges, platform links), `PERSHING BIKEWAY` |
| Exceptions | FC 0/−1 links named `02ND AV`…`06TH AV`, `W GRAPE ST`, `W HAWTHORN ST` (`RESCUED_STREETS`; short miscoded pieces of real Downtown streets) are kept as normal streets. FC 0/−1 links named `ALLEY` are kept as alleys |
| Pickup (demand) links | streets with Func_Class 4–7 (major arterials, collectors, locals), including `PRIVATE RD`. Func_Class 3 (prime arterial) is routing only |
| Alleys / driveways | names containing `ALLEY` or ending in `DY`: **routing only**, never pickup links. A node where an alley meets a street is still a pickup node through the street |
| One-way | **ignored for streets**: every street link is driven both ways, as in the earlier pipeline. Bike_Net's one-way coding is incomplete. Freeways and ramps are one-way (below) |
| Speed | Bike_Net `SPEED` (mph); step 3 applies a 10 mph floor |
| Cleaning | self-loops dropped; of two links that join the same two nodes, the faster one is kept, because FleetPy cannot hold parallel edges |

**Freeways and ramps** (from `TNED_HwyNet`; added after the streets, which they don't change; `--no-freeways`
builds the streets-only network without the new columns):

| Rule | Detail |
|---|---|
| Links | TNED FC 1 (freeway), 8 (freeway-to-freeway connector), 9 (ramp) touching the area's MGRA15 union. HOV, toll and reversible lanes are included (there are none near these areas) |
| Direction | as in TNED: one edge (`ONEWAY = 1`) unless the link has BA lanes |
| Joining the streets | only at **ramp ends**: TNED nodes of these links that also end a TNED street (FC 2–7). Never where lines cross (over/underpasses) |
| Ramp end → our node | our nearest street node within **100 ft** whose street name agrees (same name rule as step 4) |
| Mid-block ramp | if there is no such node but one of our links with an agreeing name passes within **30 ft**, that link is **split** at the ramp point (new street node, not a pickup node); otherwise the ramp end is not joined |
| Extent | only freeway links in the **strongly connected** part that holds the streets: you can get on and off inside the area. Freeway beyond the area's last off-ramp or before its first on-ramp is dropped |
| Cleaning | a freeway link whose ends join the same node is dropped; of two links between the same two nodes (e.g. two branches of one ramp) the faster is kept |
| IDs | freeway node = 300,000,000 + TNED node; split node = 310,000,000 + TNED node of the ramp end; freeway link `ROADSEGID` = 900,000,000 + `HWYCOV0_ID`; split piece = 800,000,000 + 10 × parent `ROADSEGID` + piece number. All above the Bike_Net IDs, so the FleetPy IDs of street nodes don't change |
| Speed | TNED `SPD`; `LENGTH` converted from miles to ft |

Streets, pickup nodes, the zone mapping and the demand files come out exactly as without freeways.

**Node → zone:**

- A node belongs to the MGRA15 polygon it lies in (point in polygon). A node on a zone boundary goes to the lowest
  MGRA ID.
- Every zone also gets an **access node**: the street node that its Bike_Net `MGRA Connector` link (Func_Class 10)
  attaches to.
- If that node is not in the final network, the nearest network node is used instead. In practice this moves it
  up to about 1,200 ft.
- The script stops if a zone has no MGRA Connector.

**Outputs** (`outputs_MGRA15/<area>/network/`):

- `<prefix>_raw_edges.csv`: one row per link, in the Roads_All-like layout that
  `create_network_from_raw_edges` reads (`FNODE, TNODE, FRXCOORD, FRYCOORD, TOXCOORD, TOYCOORD, L_TRACT, R_TRACT,
  ROADSEGID, LENGTH (ft), SPEED (mph), RD20FULL`), plus `Func_Class, LINK_KIND, IS_DEMAND` and, with freeways,
  `ONEWAY, PARENT_SEGID, TNED_ID`. `LINK_KIND` is `street`, `alley`, `driveway`, `freeway`, `fwy_connector` or
  `ramp`. Freeway rows have `Func_Class` = TNED FC (1/8/9) and `RD20FULL` = TNED `NM`. `PARENT_SEGID` is the
  Bike_Net `ROADSEGID` of a split piece's whole link (= `ROADSEGID` for other streets, 0 for freeway links);
- `<prefix>_roads.shp`: the same links, for QGIS (`PARENT_SEGID` is shortened to `PARENT_SEG`);
- `<prefix>_raw_nodes.csv`: `NODE, X, Y, IS_DEMAND, MGRA, ON_BOUNDARY`, and with freeways `NODE_KIND`
  (`street`, `freeway`, `split`);
- `<prefix>_mgra_access.csv`: `MGRA, CONNECTOR_NODE, ACCESS_NODE, ACCESS_DIST_FT, N_PICKUP_NODES`.

### Step 3: FleetPy network and zone → node mapping (`build_fleetpy_network_mapping.py`)

This step uses the helpers in `process_network.py`:

- **`create_network_from_raw_edges`** writes `pre_nodes.csv`/`pre_edges.csv`:
  - two directed edges per link, one for a link with `ONEWAY = 1` (files without the column: always two);
  - distance converted from ft to m;
  - `travel_time` = distance ÷ max(SPEED, 10 mph), i.e. free flow;
  - pickup nodes = the end nodes of `IS_DEMAND == 1` links (the function falls back to the old `SPEED == 20` rule
    when the column is missing, and prints which rule it used);
  - FleetPy node IDs 0..N−1 in ascending order of Bike_Net node ID.
- The function marks a random 0.35% of nodes as stop-only. **The script resets every node to
  `is_stop_only = False`**, because FleetPy can't route through stop-only nodes. It also removes the pickup flag
  from split nodes (they lie on pickup streets but are not pickup nodes).
- **`write_node_edge_from_pre_node_edge`** writes `nodes.csv`/`edges.csv` with FleetPy IDs.

The mapping (`mapping/Nodes_MGRA_mapping.csv`) has the columns `node, MGRA, raw_node, source`, with FleetPy IDs,
sorted by MGRA and node:

- **`inside`** rows: the zone's own pickup nodes;
- **`access`** rows: for a zone with no pickup node inside, its access node from step 2. That node may lie in
  another zone, so a node can appear in two rows.

`mapping/MGRA_Nodes_mapping.csv` has `MGRA, nodes` (a list of node IDs).

For MTSM, which must not walk on freeways or put stops on freeway nodes, the step also writes (FleetPy IDs):

- `network/<prefix>_edge_kind.csv`: `from_node, to_node, ROADSEGID, link_kind` per edge of `edges.csv`;
  `street`/`alley`/`driveway` are walkable, `freeway`/`fwy_connector`/`ramp` are car only;
- `network/<prefix>_node_kind.csv`: `node, raw_node, kind` (`street` or `freeway`), `split` (1 for a split node,
  which is a street node).

**Checks** (the script stops on failure):

- node IDs are 0..N−1;
- edges = 2 × two-way links + 1 × one-way links, with no duplicates;
- one connected network, and **strongly connected** (every node can reach and be reached from every other);
- no stop-only nodes;
- the pipeline's pickup nodes equal the raw `IS_DEMAND` nodes;
- every zone has at least one node;
- every mapped node is a pickup node.

### Step 4: period travel times (`build_period_travel_times.py`)

Bike_Net has a single posted `SPEED`. TNED's own `ABTM*` time fields are free flow and the same in every period,
so they hold no period information either. The only period information is in ABM3's **assigned** link times
(`hwyload_<period>.csv`), which are attached to TNED links. This step transfers them onto the Bike_Net links.

**ABM3 periods:**

| Period | Time of day |
|---|---|
| EA | 3:00–6:00 |
| AM | 6:00–9:00 |
| MD | 9:00–15:30 |
| PM | 15:30–19:00 |
| EV | 19:00–3:00 |

**Method:**

1. **TNED streets:** TNED links with FC 2–7 near the area. FC 10 are connectors. Freeways and ramps (FC 1/8/9)
   are not matched: they are TNED links themselves (point 6).
2. **Pace per period and direction:** time × 60 ÷ (LENGTH × 5,280), in s/ft.
   - One-way links have `BA_Time` 0; the AB pace is used for both directions.
   - Times above 1,000 min mean a link is closed in that period; they are treated as missing.
3. **Matching:** each Bike_Net link is sampled every 10 ft. A sample point matches a TNED street if it lies
   within **30 ft**, the two lines are roughly parallel (|cos| ≥ 0.8), and the street names don't clearly differ.
   - Names are compared after removing direction words, suffixes and leading zeros. `UNKNOWN`, `DRIVEWAY`,
     `PRIVATE` and `ALLEY` never count as a mismatch.
   - A link is **matched** when at least 80% of its points match.
   - Direction: Bike_Net A→B takes TNED AB when the two run the same way, and BA otherwise.
   - Link time = mean pace over the matched points × link length.
4. **Unmatched links (additive rule):** free-flow time + the class delay per foot × link length.
   - Class delay = for the matched links of the same area and Func_Class (FC 0 counted as 7), both directions
     pooled: sum of (period time − TNED's own free-flow time, LENGTH ÷ SPD) ÷ sum of link lengths. This is the
     length-weighted mean delay per foot.
   - So unmatched links keep Bike_Net's posted speed and get ABM3's intersection delay and congestion, in seconds.
   - Why a length-weighted mean and not a median: TNED puts its delay at the downstream end of each link, so
     per-link values are very skewed and the median understates the class's delay.
   - The script also has the earlier rule (`--unmatched-rule ratio`: free flow × the class's period time ÷ free
     flow ratio, which carries TNED's speeds) and `--class-stat median`. Defaults are set per area in `AREAS`.
5. **Closed periods:** a period in which a matched link is closed gets that link's slowest other period time.
   (Near these two areas only two HOV links are ever closed, and no Bike_Net link matches them.)
6. **Freeways, connectors and ramps:** `AB_Time`/`BA_Time` straight from hwyload for their own TNED link
   (`source` = `tned_freeway`). A closed period would get 1,000,000 s (none occur). Ramp times include ABM3's
   large fixed ramp delays (probably ramp meters; up to about 2 min on one link).
7. **Split street links:** matched and timed as the whole Bike_Net link; each piece gets the whole link's times
   × its share of the length. So street times are exactly as without freeways.

**Outputs:**

- `network/<prefix>_link_period_tt.csv`: one row per directed edge, in `edges.csv` order. Columns: FleetPy and
  Bike_Net node IDs, `ROADSEGID`, name, `Func_Class`, distance, `tt_free, tt_EA, tt_AM, tt_MD, tt_PM, tt_EV` (s),
  `source` (`tned`, `class_delay` or `tned_freeway`; `class_ratio` with `--unmatched-rule ratio`),
  `match_share` (empty for freeway edges), `tned_ids`.
- `network/<prefix>_roads_period_tt.shp`: AB/BA period times per link, with `LINK_KIND`, for QGIS (`BA_*` empty
  on one-way links).
- `fleetpy_network/`: a ready FleetPy network folder:

```
fleetpy_network/
  base/nodes.csv, base/edges.csv      (edges travel_time = free flow)
  base/crs.info                       EPSG:2230
  0/edges_td_att.csv                  EV  (0:00–3:00 is still ABM3's EV period)
  10800/edges_td_att.csv              EA  (3:00)
  21600/edges_td_att.csv              AM  (6:00)
  32400/edges_td_att.csv              MD  (9:00)
  55800/edges_td_att.csv              PM  (15:30)
  68400/edges_td_att.csv              EV  (19:00)
  network_dynamics.csv                simulation_time,travel_time_folder (one row per folder)
```

`edges_td_att.csv` has the columns `from_node, to_node, edge_tt` (s). `base/edges.csv` keeps free flow, because
MTSM reads it as the free-flow time.

### Step 5: demand files (`build_abm3_demand_files.py`)

1. **Trips:** all ABM3 resident trips with both origin and destination in the area's `<prefix>_MGRA15.csv`. All
   purposes and modes are kept, including walk, bike and school bus.
2. **Household join:**
   - trips from households with **income 0** are dropped;
   - `income` = household income ÷ max(`num_workers`, 1);
   - `auto_available` = `auto_ownership ≥ 1`;
   - `num_autos` = `auto_ownership`.
3. **Person join:** `transit_pass` = `transit_pass_ownership`. The script stops if it is not 0/1.
4. **Random draws:** trips are sorted by `trip_id`, and each area gets its own `numpy.random.default_rng(42)`.
   The draws are made in a fixed order:
   1. **`rq_time`** = 10,800 + (depart − 1) × 1,800 + a random 0–1,799 s. Period 1 starts at 3:00, and periods
      43–48 (after midnight) run past 86,400 s rather than wrapping.
   2. **Nodes:**
      - `start` = a random node of the origin zone;
      - `end` = a random node of the destination zone **other than `start`**;
      - if the destination's only node is the start node, `start` is redrawn from the origin's other nodes;
      - the trip is **dropped** only when both zones have just that one node. FleetPy drops requests whose start
        equals their end anyway.
   3. **Betas:** normal draws, clipped at a floor:

      | Beta | Mean | SD | Floor |
      |---|---|---|---|
      | `bt_c_0` | 0 | 0 | 0 |
      | `bt_c_ivt` | 0.2 | 0.047 | 0.01 |
      | `bt_c_gas` | 0.5 × mean_income ÷ max(income, **5,000**) | 0.377 | 0.05 |
      | `bt_t_0` | 0.242 | 0.04 | 0 |
      | `bt_t_wk` | 0.3 | 0.14 | 0.01 |
      | `bt_m_wt`, `bt_f_wt` | 0.15 | 0.022 | 0.01 |
      | `bt_m_ivt` | 0.15 | 0.022 | 0.01 |
      | `bt_f_ivt` | 0.15 | 0.029 | 0.01 |
      | `bt_f_trfer` | 0.5 | 0.022 | 0.01 |

      - `bt_t_fr` = the same draw as `bt_c_gas`.
      - `mean_income` = the mean per-worker income of the trips written.
      - The **$5,000 floor** (`INCOME_FLOOR`) applies only inside the cost-beta formula; the `income` column keeps
        the real value.
   4. **Downtown only:** `parking_dur_min` = max(0, −23.6235 + lognormal(μ = ln 95.4435, σ = 0.53411)) minutes.
      This is the same distribution as `scipy.stats.lognorm(s=0.5341, loc=-23.62, scale=95.44)`.
   5. **Debug sample:** 1% of the rows, drawn without replacement.
5. **Order and IDs:** rows are sorted by numeric `rq_time`, then `trip_id`, and `request_id` is set to 0..N−1.

**Outputs** (`outputs_MGRA15/<area>/demand/`):

| File | Content |
|---|---|
| `<area>_trips_nodes_study_area_with_beta.csv` | 21 columns: `rq_time, start, end, request_id, bt_c_0, bt_c_ivt, bt_c_gas, bt_t_0, bt_t_wk, bt_m_wt, bt_f_wt, bt_m_ivt, bt_f_ivt, bt_f_trfer, bt_t_fr, income, transit_pass, auto_available, num_autos, purpose, trip_mode`. Downtown adds a 22nd, `parking_dur_min` |
| `<area>_debug_trips_nodes_study_area_with_beta.csv` | the 1% sample, keeping the full file's `request_id`s |
| `<area>_trips_trace.csv` | `request_id → trip_id, household_id, person_id, origin, destination, depart, trip_mode, purpose` |
| `<area>_dropped_trips.csv` | the dropped trips, with `reason` = `income_0` or `no_distinct_end_node` |

**Checks** (the script stops on failure): no request has start = end; every node belongs to its trip's zone
mapping; no NaN; `rq_time` is sorted; `transit_pass` is 0/1.

---

## 5. Output layout

```
outputs_MGRA15/
  lemon_grove/   (prefix Lemon_grove)
  downtown_sd/   (prefix Downtown_SD)
    network/      <prefix>_raw_edges.csv, _raw_nodes.csv, _mgra_access.csv, _roads.shp      (step 2)
                  pre_nodes.csv, pre_edges.csv, nodes.csv, edges.csv,
                  <prefix>_edge_kind.csv, _node_kind.csv                                    (step 3)
                  <prefix>_link_period_tt.csv, _roads_period_tt.shp                         (step 4)
    mapping/      Nodes_MGRA_mapping.csv, MGRA_Nodes_mapping.csv                            (step 3)
    fleetpy_network/  base/, 0/, 10800/, 21600/, 32400/, 55800/, 68400/, network_dynamics.csv (step 4)
    demand/       <area>_trips_nodes_study_area_with_beta.csv, _debug_..., _trips_trace.csv,
                  _dropped_trips.csv                                                        (step 5)
```

All coordinates are **EPSG:2230** (NAD83 / California zone 6, US ft). Node IDs in every FleetPy-facing file are
FleetPy IDs (0..N−1); the `raw_*` columns hold Bike_Net node IDs.

Also in the local `outputs_MGRA15/`, but not written by the scripts:

- `lemon_grove/backup_option_i_ratio/` and `downtown_sd/backup_ratio_median/`: the period-time outputs of the
  first build (unmatched links by class ratio, median). `build_period_travel_times.py --unmatched-rule ratio
  --class-stat median` reproduces them (on a `--no-freeways` network).
- `<area>/backup_streets_only/`: `network/`, `mapping/`, `demand/` and `fleetpy_network/` as they were before
  freeways were added (2026-11-20). `build_bikenet_networks.py --no-freeways` and steps 3–5 reproduce the CSVs
  byte for byte (the `.dbf` header date differs; the period shapefile now also has `LINK_KIND`).
- `<area>/network/<prefix>_TNED_streets.gpkg`: QGIS comparison layers. They hold the TNED links touching the area,
  without connectors, freeways and ramps, with `in_ours` (share of length within 30 ft of our network) and `covered`
  (`in_ours` ≥ 0.8).

### Units

Travel times are in **seconds** and distances in **meters** in every file FleetPy or MTSM reads. Only the
Bike_Net-style link files keep feet and mph.

| File | Column | Unit |
|---|---|---|
| `network/edges.csv`, `fleetpy_network/base/edges.csv` | `distance` | meters (= `LENGTH` ft ÷ 3.28084) |
| | `travel_time` (free flow) | seconds (= distance ÷ max(SPEED, 10 mph)) |
| `fleetpy_network/<t>/edges_td_att.csv` | `edge_tt` (period time) | seconds |
| `network/<prefix>_link_period_tt.csv` | `distance` | meters |
| | `tt_free`, `tt_EA` … `tt_EV` | seconds |
| `network/<prefix>_roads_period_tt.shp` | `tt_free`, `AB_*`, `BA_*` | seconds |
| | `LENGTH` | feet |
| `network/<prefix>_raw_edges.csv`, `<prefix>_roads.shp` | `LENGTH` | feet (freeway rows: TNED miles × 5,280) |
| | `SPEED` | mph |
| `nodes.csv`, `pre_nodes.csv`, `<prefix>_raw_nodes.csv` | coordinates | feet (EPSG:2230) |
| `<prefix>_mgra_access.csv` | `ACCESS_DIST_FT` | feet |
| demand files | `rq_time` | seconds after midnight (3:00 = 10,800; trips after midnight run past 86,400) |
| | `parking_dur_min` (Downtown) | minutes |
| | `income` | dollars per year, per worker |

The ABM3 inputs use other units: `hwyload_*.csv` `AB_Time`/`BA_Time` are in minutes, TNED `LENGTH` is in miles
and `SPD` in mph, and `final_trips.csv` `depart` is a half-hour period (1–48).

### Reference numbers (build of 2026-11-19; period times and freeways 2026-11-20), to check a rebuild against

| | Lemon Grove | Downtown SD |
|---|---|---|
| MGRA15 zones | 225 | 387 |
| street links / nodes (`--no-freeways`: directed edges) | 1,285 / 1,045 (2,570) | 1,071 / 724 (2,142) |
| freeway links kept (miles): freeway / connector / ramp | 27 (5.74): 11 / 1 / 15 | 100 (14.90): 31 / 8 / 61 |
| ramp ends joined at a node / by a split / not joined | 5 / 2 / 1 (of 13; the rest lead to dropped freeway) | 24 / 6 / 1 (of 39) |
| with freeways: links / nodes / directed edges | 1,314 / 1,067 / 2,601 | 1,177 / 800 / 2,255 |
| pickup nodes | 1,032 | 709 |
| zones with a pickup node inside / using the access node | 201 / 24 | 294 / 93 |
| links matched to TNED (share of length) | 455 (34.7%) | 769 (73.2%) |
| sum of edge times ÷ free flow, EA / AM / MD / PM / EV (streets only) | 1.26 / 1.36 / 1.27 / 1.35 / 1.26 | 1.94 / 2.15 / 2.02 / 2.16 / 1.95 |
| the same with freeways | 1.26 / 1.37 / 1.28 / 1.37 / 1.26 | 1.90 / 2.13 / 1.98 / 2.14 / 1.91 |
| freeway edges alone, AM | 2.46 | 1.62 |
| ABM3 trips with both ends in the area | 43,737 | 208,327 |
| dropped: income 0 / no distinct node | 512 / 527 | 6,911 / 16,665 |
| **requests written** / debug | **42,698** / 427 | **184,751** / 1,848 |
| mean per-worker income | 64,785.7 | 81,410.0 |

With the same inputs and seed, a rebuild reproduces these numbers and files exactly.

---

## 6. Using the outputs in FleetPy / MTSM

- **Network:** point FleetPy's `network_name` at a copy of `fleetpy_network/`, renamed as you like, under
  `data/networks/`.
- **Period travel times:** set `nw_dynamic_f,network_dynamics.csv` in the scenario config.
  - FleetPy's `NetworkBasic` reads that file and loads `<folder>/edges_td_att.csv` when
    `update_network(sim_time)` is called with **exactly** a listed time.
  - Before the first listed time it uses `base/edges.csv`. With `start_time 0` and a time step that divides 10,800
    and 1,800 (e.g. 60 s), every folder is picked up. A run that starts at e.g. 5:00 uses free flow until 6:00.
  - Without `nw_dynamic_f`, FleetPy scans the network folder for integer-named subfolders itself.
- **pandas ≥ 2:** upstream FleetPy (commit `7019960`, as vendored in MTSM) calls `Series.iteritems()` in
  `src/routing/NetworkBasic.py` `load_tt_file`. pandas 2 removed it, so change it to `.items()`, or loading the
  first period folder crashes.
- **MTSM's runner:** `fleetpy_runner.stage_inputs` copies only the `base/` files (`LG_STAGED_INPUTS`). Add the
  period folders and `network_dynamics.csv` to that list.
- **Freeways in MTSM's super-network:** `pre_process/build_super_network.py` (MTSM b38d7ba) puts a walk link on
  every road edge, draws virtual stops from all road nodes, joins FRT stops to the nearest road node, and assumes
  two edges per road segment. With freeways in the network, use `<prefix>_edge_kind.csv` and `_node_kind.csv` to
  keep walking, virtual stops and stop joining on streets. Car (`auto_edges.csv`) and microtransit driving can use
  every edge.
- **Demand file in MTSM:**
  - MTSM reads the demand file with `read_request`, which picks columns by name and ignores the extra ones.
  - It then writes FleetPy's own request file with only `rq_time, start, end, request_id`.
  - `parking_dur_min`, `transit_15min_acc` and `transit_pass` are optional in MTSM since its commit 7d7008c.
    Older MTSM versions require all three, and this pipeline does not write `transit_15min_acc`.
- **Demand file read directly by FleetPy:**
  - every extra column becomes a request attribute (`TravelerModels.py`, `setattr`). Don't add a column named
    `mode`;
  - FleetPy's loader uses `iterrows()`, so test once that the text columns (`purpose`, `trip_mode`) cause no
    trouble.

---

## 7. Pitfalls

**Zones and time**

- **MGRA13 vs MGRA15.** The IDs overlap numerically but mean different places. A study-area list in the wrong
  zone system still "works" and silently selects scattered trips (an MGRA13 Lemon Grove list picks 9,599
  meaningless trips from ABM3). Step 5 stops if the zone list has no `share_in` column or holds IDs not in
  `mgra15.shp`.
- **`depart` is a half-hour period (1–48) starting at 3:00, not an hour.** Older code that does
  `depart * 3600` gives wrong times on ABM3 data.

**Income and cost betas**

- **Income 0 and tiny incomes.** 3.4% of ABM3 households have income 0; their trips are dropped. Some per-worker
  incomes are a few dollars; without the $5,000 floor, `bt_c_gas` reaches the thousands.

**Nodes and zones**

- **Same-zone trips in zones with a single node are dropped.** Downtown has many small zones with one node, so it
  loses 8.3% of its trips, mostly within-block walks. Lemon Grove loses 1.2%. `_dropped_trips.csv` lists them.

**Network**

- **One-way streets are not modelled.** Every street link is driven both ways; only freeways and ramps are
  one-way.
- **Only part of the freeways is in the network.** Freeway is kept only between an on-ramp and an off-ramp inside
  the area (Lemon Grove 5.7 of 17.9 mi touching the area: SR-94 between its Lemon Grove interchanges and a short
  piece of SR-125 SB; Downtown 14.9 of 25.7 mi, mostly I-5). Trips can't use interchanges just outside the area,
  because those streets aren't in the network either. In the demand files, freeways shorten the mean AM trip by
  0.1% (Lemon Grove) and 1.7% (Downtown); 0.3% / 3.2% of trips use them in AM.
- **Disconnected pieces are dropped.** A few street pieces inside the area connect to the network only through
  streets outside it, and the largest-component rule removes them: Lemon Grove 29 nodes, Downtown 16.

**Period travel times**

- **Matched and unmatched links run at different speed bases.** Matched links carry ABM3's speeds: TNED often
  has 25–30 mph where Bike_Net has 20. Unmatched links keep Bike_Net's posted speed plus ABM3's delay. In Lemon
  Grove, where 65% of the length is unmatched, unmatched local streets therefore run at about 13 mph in AM
  against about 18 mph on matched ones. Arterials and collectors line up closely. Over the whole network, AM
  times are about 1.36 × free flow in Lemon Grove and 2.15 × in Downtown, including intersection delay.
- **An earlier rule made unmatched streets too fast.** Scaling free flow by the class ratio (the first build)
  carried TNED's higher speeds onto unmatched local streets, so they came out *faster* than their posted speed
  (Lemon Grove 0.84 × free flow), and shortest paths moved onto them. The additive rule avoids this.
- **The two directions of a link can differ a lot.** TNED puts its delay at the downstream end of each link, and
  the script spreads it over the link's length.

**Running the scripts**

- **Every script overwrites only with `--overwrite`.** Steps 3–5 depend on the previous step's outputs, so after
  rebuilding one step, rebuild everything after it.
- **Randomness:** steps 3 and 5 are seeded (42). Changing an input, the order of the draws, or the trip filter
  changes every later draw.
- **Numbers on Windows:** see the DLL issue in section 2.

---

## 8. Adapting to your own data or another area

1. **New ABM3 run, same areas:** replace the files in `data_new/demand_data/`, the gdb, and the hwyload folder
   (`--hwyload-dir`). Then rerun steps 2–5 with `--overwrite`.
2. **New area:**
   - Add an entry to `AREAS` in each script: a name, a file prefix, the zone list / polygon paths, and whether
     to draw parking (step 5).
   - Then either:
     - provide `data_new/<dir>/<prefix>_MGRA15.csv` (`MGRA, TAZ, City, share_in`) and `<prefix>_MGRA15.shp`
       yourself (any `share_in` works, e.g. 1.0), and skip step 1; or
     - give step 1 the old MGRA13 polygons and list.
   - Check `RESCUED_STREETS` and `DROP_NAMES` in step 2. They are specific to these two areas, so look at the
     FC 0/−1 links of your area in QGIS before reusing them.
3. **Another zone system or network source:** steps 2–5 assume Bike_Net/TNED field names and EPSG:2230. Check the
   field names in section 3 against your data first.

---

## 9. Code layout

```
build_mgra15_study_areas.py        step 1
build_bikenet_networks.py          step 2
build_fleetpy_network_mapping.py   step 3
build_period_travel_times.py       step 4
build_abm3_demand_files.py         step 5
process_network.py                 network helpers used by step 3 (create_network_from_raw_edges,
                                   write_node_edge_from_pre_node_edge; IS_DEMAND and ONEWAY columns are
                                   optional); other functions there are legacy and contain hard-coded local paths
generate_demand_files.py           legacy MGRA13/ABM2-era demand pipeline (select_trips,
                                   map_mgra_to_nodes, TRPartA_demand_files, synthesize_requests_with_trend_time,
                                   ...). Not used by the steps above; kept for reference and older studies
rebuild_mgra_road_overlays.py      legacy: rebuilds the MGRA13 QGIS overlay CSVs used by generate_demand_files.py
build_super_network.py             copy of MTSM's pre_process/build_super_network.py (MTSM 008c6ac, unchanged):
                                   builds an MTSM super-network (walk, FRT, microtransit) from a road network and
                                   an FRT stop/edge network. Not yet run here; it does not keep walking, virtual
                                   stops or FRT stops off the freeways (MTSM I-33)
CLAUDE.md                          detailed working notes: every decision, check and count behind the pipeline
```

`CLAUDE.md` records the reasoning behind each rule (why a threshold, which alternatives were rejected, and the
counts at each stage), the known issues in the legacy code, and where each raw file came from.
