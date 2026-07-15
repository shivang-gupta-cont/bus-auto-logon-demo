# Bus Stop Grid Explorer — Codebase Reference

---

## 1. SQL Queries

| # | Query | Used In | Purpose |
|---|-------|---------|---------|
| Q1 | `SELECT DISTINCT b.stoppointid, d.latitude, d.longitude FROM bidb.SA_STOPPOINTS b INNER JOIN dsdb.ds_stoppointhistory d ON b.stoppointid = d.stoppointid WHERE serviceday = :serviceday` | startup (module level) | Load all stop points for the service day |
| Q2 | `SELECT routeid, patternid FROM dsdb.ds_routepathhistory WHERE stoppointid = :stoppointid` | `api_stop_routes()` | Get routes serving a specific stop |
| Q3 | `SELECT psid, fromshapeid shapepointid, fromlatitude latitude, fromlongitude longitude FROM dsdb.ds_routegeometry WHERE routeid = :routeid AND patternid = :patternid AND baseversion = :baseversion ORDER BY psid, fromshapeid` | `api_route_geometry()` | Get ordered shape points for a route/pattern |
| Q4 | `SELECT DISTINCT fromshapeid AS shapepointid, fromlatitude AS latitude, fromlongitude AS longitude FROM dsdb.ds_routegeometry WHERE fromlatitude BETWEEN :south AND :north AND fromlongitude BETWEEN :west AND :east AND baseversion = :baseversion ORDER BY fromshapeid` | `api_grid_shapepoints()` | Get all shape points within a cell's geographic bounds |

---

## 2. Python — `app.py`

### 2.1 Config Constants

| Name | Value | Purpose |
|------|-------|---------|
| `SERVICE_DAY` | `"18-may-2026"` | Filter date for stop points query |
| `BASE_VERSION` | `"20260503"` | Route geometry version filter |
| `KM_LAT` | `1 / 111.0` | Degrees of latitude per km (fixed) |
| `DSDB_CONFIG` | `{user, password, dsn}` | dsdb Oracle connection params |
| `TECHVHNO` | `2125` | Vehicle number to filter from CSV |
| `BLOCKID` | `1858` | Block ID to filter from CSV |
| `TRIPID` | `41074` | Trip ID to filter from CSV |
| `TARGET_DATETIME` | `"18-May-2026 05.08.10"` | Earliest timestamp to include from CSV |
| `CSV_PATH` | Windows path string | Path to vehicle positions CSV |

### 2.2 Module-level Variables

| Name | Stores | Notes |
|------|--------|-------|
| `_cell_shapepoints` | `{cell, total, points:[{id,lat,lon}]}` | Last queried cell's shape points; updated by `api_grid_shapepoints()` |
| `_veh_df` | pandas DataFrame | Filtered, sorted vehicle positions loaded from CSV at startup |
| `_df` | pandas DataFrame | All stop points with CELL_ROW / CELL_COL columns; loaded from Oracle at startup |
| `_mean_lat` | float | Mean latitude of all stop points; used to compute `KM_LON` |
| `_center_lon` | float | Mean longitude; used as map center |
| `KM_LON` | float | Degrees of longitude per km at `_mean_lat` (cos-corrected) |
| `_cell_counts` | pandas DataFrame | `(CELL_ROW, CELL_COL, COUNT)` — one row per occupied grid cell |
| `_max_count` | int | Max stops in any single cell; used for colour scale |

### 2.3 Functions

#### `count_to_color(count)`
- **Purpose:** Linear colour interpolation from light teal → dark teal based on stop density
- **Input:** `count` — number of stops in a cell
- **Output:** hex colour string e.g. `"#2a7a8c"`

#### `cell_bounds(row, col)`
- **Purpose:** Convert integer cell indices back to geographic degree bounds
- **Input:** `row`, `col` — integer cell indices
- **Output:** `(south, north, west, east)` in degrees

### 2.4 Routes (Flask endpoints)

#### `GET /` → `index()`
- **Purpose:** Serve the HTML page
- **Input:** none
- **Output:** rendered `index.html` with template vars: `service_day`, `total_stops`, `total_cells`, `center_lat`, `center_lon`, `veh_techvhno`, `veh_blockid`, `veh_tripid`, `veh_points`

#### `GET /api/vehicle-route` → `api_vehicle_route()`
- **Purpose:** Return filtered, sorted vehicle GPS positions
- **Input:** none (data pre-loaded at startup into `_veh_df`)
- **Output:** `{vehicle, block, trip, total, points:[{id, time, lat, lon, compass, routeid, blockid, tripid, techvhno}]}`
- **Query used:** none (reads from `_veh_df`)

#### `GET /api/grid` → `api_grid()`
- **Purpose:** Return all grid cells with bounds and density colour
- **Input:** none
- **Output:** `{cells:[{row, col, count, south, north, west, east, color}], max_count}`

#### `GET /api/stops` → `api_stops()`
- **Purpose:** Return all stop points for map marker layer
- **Input:** none
- **Output:** `{stops:[{id, lat, lon}], total}`

#### `GET /api/cell-stops?row=&col=` → `api_cell_stops()`
- **Purpose:** Return stops inside a specific grid cell — computed on demand
- **Input:** `row`, `col` query params
- **Output:** `{cell:{row,col,count,south,north,west,east,center_lat,center_lon}, stops:[{id,lat,lon}]}`

#### `GET /api/stop-routes?stop_id=` → `api_stop_routes()`
- **Purpose:** Return routes and patterns serving a stop — connects to dsdb per request
- **Input:** `stop_id` query param
- **Output:** `{stop_id, routes:[{ROUTEID, PATTERNID}], total}`
- **Query used:** Q2

#### `GET /api/route-geometry?routeid=&patternid=` → `api_route_geometry()`
- **Purpose:** Return shape points grouped by PSID for a route/pattern
- **Input:** `routeid`, `patternid` query params
- **Output:** `{route_id, pattern_id, psids:[{psid, points:[{shapepointid,lat,lon}]}], total_psids, total_points}`
- **Query used:** Q3
- **Note:** lat/lon divided by 3,600,000 before returning

#### `GET /api/grid-shapepoints?row=&col=` → `api_grid_shapepoints()`
- **Purpose:** Return all shape points within a cell's bounds; updates `_cell_shapepoints`
- **Input:** `row`, `col` query params
- **Output:** `{cell:{row,col,south,north,west,east}, total, points:[{id,lat,lon}]}`
- **Query used:** Q4
- **Note:** bounds converted to scaled integers (`× 3,600,000`) for the DB query

---

## 3. JavaScript — `index.html`

### 3.1 Constants

| Name | Value | Purpose |
|------|-------|---------|
| `KM_LAT` | `1 / 111.0` | Degrees of latitude per km |
| `KM_LON` | computed from `center_lat` | Degrees of longitude per km (cos-corrected) |
| `STYLE_NORMAL` | Leaflet style object | Stop marker — default cluster mode |
| `STYLE_DIMMED` | Leaflet style object | Stop marker — outside selected cell |
| `STYLE_IN_CELL` | Leaflet style object | Stop marker — inside selected cell, clickable |
| `STYLE_SELECTED_ACTIVE` | Leaflet style object | Stop marker — currently shown in panel |
| `STYLE_SELECTED_INACTIVE` | Leaflet style object | Stop marker — selected but panel showing another |
| `CELL_STYLE_NORMAL` | function `c =>` style | Grid rect style — unselected |
| `CELL_STYLE_SELECTED` | function `c =>` style | Grid rect style — selected (dark border) |
| `ROUTE_COLORS` | array of 12 hex strings | Cycled per PSID when drawing route geometry |

### 3.2 Global Variables

| Name | Stores | Notes |
|------|--------|-------|
| `map` | Leaflet Map instance | Central map object |
| `gridLayer` | `L.layerGroup` | All grid cell rectangles |
| `vehicleLayer` | `L.layerGroup` | Full GPS route polyline + all position dots |
| `nearestLayer` | `L.layerGroup` | Top-3 nearest shape point markers + lines |
| `currentPosLayer` | `L.layerGroup` | Current vehicle position marker (always on top) |
| `stopsCluster` | `L.markerClusterGroup` | All stop markers in default cluster mode |
| `individualLayer` | `L.layerGroup` | Stop markers in individual mode (cell selected) |
| `allStopData` | `[{id, lat, lon}]` | All stop points loaded once from `/api/stops` |
| `stopMarkerMap` | `{stopId → L.circleMarker}` | Individual-mode markers; rebuilt per cell selection |
| `cellColorMap` | `{"row_col" → hex color}` | Fill color per cell; also used to check if a cell exists |
| `selectedCell` | `{row,col,south,north,west,east,color,rect}` or `null` | Currently selected grid cell |
| `selectedStops` | `Map(stopId → {stop,marker,routes})` | All currently selected stops and their route selections |
| `activePanelStopId` | `int` or `null` | Which stop's panel is currently displayed |
| `stopsVisible` | `boolean` | Whether stop layers are on the map |
| `vehicleVisible` | `boolean` | Whether vehicle layers are on the map |
| `vehiclePoints` | `[{id,time,lat,lon,compass,routeid,blockid,tripid,techvhno}]` | All vehicle positions, sorted by time |
| `vehicleIndex` | `int` | Index into `vehiclePoints` for the "current" navigator position |
| `vehicleId` | `int` | TECHVHNO from API; used in tooltips |
| `gridShapePoints` | `{cell,total,points:[{id,lat,lon}],byId:Map}` or `null` | Shape points of the vehicle's current grid cell |
| `nearestShapepoint` | `{shapepoint:{id,lat,lon}, distanceM:float}` or `null` | The closest shape point to the vehicle (rank #1) |

### 3.3 Functions

#### `isInCell(stop, cell)`
- **Purpose:** Check if a stop point lies within a cell's geographic bounds
- **Input:** `stop {lat,lon}`, `cell {south,north,west,east}`
- **Output:** `boolean`

#### `nextRouteColor(routesMap)`
- **Purpose:** Pick the next unused colour from `ROUTE_COLORS` for a new route selection
- **Input:** `routesMap` — current stop's `routes` Map
- **Output:** hex colour string

#### `activeStopRoutes()`
- **Purpose:** Return the route selections Map for the currently shown stop panel
- **Input:** none (reads `selectedStops`, `activePanelStopId`)
- **Output:** `Map("rid_pid" → {routeId,patternId,color,layer,data})`

#### `haversineMeters(lat1, lon1, lat2, lon2)`
- **Purpose:** Compute great-circle distance between two lat/lon points
- **Input:** two coordinate pairs in decimal degrees
- **Output:** distance in metres (float)

#### `vehicleGridCell()`
- **Purpose:** Find which displayed grid cell the vehicle's current position is in
- **Input:** none (reads `vehiclePoints[vehicleIndex]`, `cellColorMap`)
- **Output:** `{row, col}` or `null` if not in any cell with stop points

#### `navigateVehicle(delta)`
- **Purpose:** Move the vehicle navigator forward or backward by one step
- **Input:** `delta` — `+1` or `-1`
- **Output:** none; updates `vehicleIndex`, calls `updateCurrentPosition()` and `updateVehicleGridAndNearest()`

#### `updateCurrentPosition()`
- **Purpose:** Redraw the current vehicle position marker and update the navigator UI
- **Input:** none (reads `vehiclePoints[vehicleIndex]`, `vehicleId`)
- **Output:** none; updates `currentPosLayer` and navigator DOM elements

#### `updateVehicleGridAndNearest()`  *(async)*
- **Purpose:** Determine the vehicle's current grid cell, load its shape points if changed, then find nearest
- **Input:** none
- **Output:** none; may update `gridShapePoints`, `nearestShapepoint`, `nearestLayer`

#### `loadGridShapePoints(cell)`  *(async)*
- **Purpose:** Fetch shape points for a given cell from `/api/grid-shapepoints` and store in `gridShapePoints`
- **Input:** `cell {row, col}`
- **Output:** none; sets `gridShapePoints`

#### `findNearestShapepointToVehicle()`
- **Purpose:** Find the 3 shape points closest to the vehicle's current position; draw them with rank badges
- **Input:** none (reads `gridShapePoints.points`, `vehiclePoints[vehicleIndex]`)
- **Output:** none; updates `nearestShapepoint`, updates `nearestLayer`
- **Algorithm:** map all points to `{sp, dist}`, sort ascending, slice top 3, apply `MIN_SEPARATION_M` filter if coinciding

#### `loadGrid()`  *(async)*
- **Purpose:** Fetch all grid cells from `/api/grid` and draw rectangles on the map
- **Input:** none
- **Output:** none; populates `gridLayer`, `cellColorMap`

#### `loadStops()`  *(async)*
- **Purpose:** Fetch all stops from `/api/stops`, build cluster markers
- **Input:** none
- **Output:** none; populates `allStopData`, `stopsCluster`

#### `loadVehicleRoute()`  *(async)*
- **Purpose:** Fetch vehicle GPS positions from `/api/vehicle-route`, draw full route polyline and all dots
- **Input:** none
- **Output:** none; populates `vehiclePoints`, `vehicleId`, `vehicleLayer`; calls `updateCurrentPosition()`

#### `switchToIndividual(cell)`
- **Purpose:** Replace cluster mode with individual per-stop markers; dim stops outside the cell
- **Input:** `cell {south,north,west,east}`
- **Output:** none; clears `stopsCluster`, populates `individualLayer`, `stopMarkerMap`

#### `switchToCluster()`
- **Purpose:** Restore cluster mode; tear down individual markers
- **Input:** none
- **Output:** none; clears `individualLayer`, `stopMarkerMap`, re-adds `stopsCluster`

#### `openCellPanel(cell, rect)`  *(async)*
- **Purpose:** Handle grid cell click — highlight rect, switch stop mode, trigger vehicle nearest, open panel
- **Input:** `cell` object from grid data, `rect` Leaflet rectangle
- **Output:** none

#### `fetchCellPanel(cell)`  *(async)*
- **Purpose:** Fetch stops for a cell from `/api/cell-stops` and render the cell panel
- **Input:** `cell {row, col}`
- **Output:** none; updates `#panel-body`

#### `renderCellPanel(cell, stops)`
- **Purpose:** Build and inject cell info HTML (bounds, stops table) into the panel
- **Input:** `cell` object, `stops []`
- **Output:** none; sets `#panel-body` innerHTML

#### `handleStopClick(stop)`
- **Purpose:** Toggle stop selection — select new, switch panel to another selected, or deselect active
- **Input:** `stop {id, lat, lon}`
- **Output:** none; updates `selectedStops`, `activePanelStopId`, marker styles, panel

#### `setActiveStop(id)`
- **Purpose:** Promote a stop to "active panel" state — update marker styles
- **Input:** `id` — stop ID
- **Output:** none; updates marker styles and `activePanelStopId`

#### `deselectStop(id)`
- **Purpose:** Remove a stop from selection — reset marker, clear its route layers
- **Input:** `id` — stop ID
- **Output:** none; removes from `selectedStops`, clears route layers from map

#### `clearAllSelectedStops()`
- **Purpose:** Deselect every selected stop at once
- **Input:** none
- **Output:** none; calls `deselectStop()` for each, resets `selectedStops`, `activePanelStopId`

#### `openStopPanel(stop)`  *(async)*
- **Purpose:** Open panel for a selected stop — fetch its routes from `/api/stop-routes`
- **Input:** `stop {id, lat, lon}`
- **Output:** none; updates panel

#### `renderStopPanel(stop, data)`
- **Purpose:** Build stop panel HTML — meta cards, route toggle table, geometry section placeholder
- **Input:** `stop`, `data {stop_id, routes[], total}`
- **Output:** none; sets `#panel-body` innerHTML

#### `toggleRoute(routeId, patternId, rowEl)`  *(async)*
- **Purpose:** Toggle a route row — if off, fetch geometry from `/api/route-geometry` and draw; if on, remove
- **Input:** `routeId`, `patternId`, `rowEl` DOM element
- **Output:** none; updates stop's `routes` Map, `rowEl` styles, map layer

#### `drawRouteOnLayer(data, layer)`
- **Purpose:** Draw PSID polylines and shape point dots for a route onto a given Leaflet layer
- **Input:** `data` from `/api/route-geometry`, `layer` L.layerGroup
- **Output:** none

#### `updateGeometrySection()`
- **Purpose:** Re-render the PSID legend below the route table for all active route selections
- **Input:** none (reads `activeStopRoutes()`)
- **Output:** none; updates `#geometry-section` innerHTML

#### `setPanelTitle(title, breadcrumb)`
- **Purpose:** Set panel header title and optional back-breadcrumb
- **Input:** `title` string, `breadcrumb` string or null
- **Output:** none; updates `#panel-title`, `#panel-breadcrumb`

#### `setPanelLoading(msg)`
- **Purpose:** Show a spinner with a message in the panel body
- **Input:** `msg` string
- **Output:** none; sets `#panel-body` innerHTML

#### `openPanel()` / `closePanel()`
- **Purpose:** Slide the side panel in or out; invalidate map size
- **Input:** none
- **Output:** none; toggles `.open` class, resets state on close

#### `backToCell()`
- **Purpose:** Breadcrumb click — clear stop selections, return panel to cell view
- **Input:** none
- **Output:** none; calls `clearAllSelectedStops()`, `fetchCellPanel()`

#### `toggleVehicle()` / `toggleStops()`
- **Purpose:** Show/hide the vehicle layers or stop layers from the map
- **Input:** none (toggle based on `vehicleVisible` / `stopsVisible`)
- **Output:** none; updates layer visibility and button text

#### `boot()`  *(async IIFE)*
- **Purpose:** Entry point — runs `loadGrid()`, `loadStops()`, `loadVehicleRoute()` in parallel; hides loading overlay
- **Input:** none
- **Output:** none
