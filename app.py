import math
import numpy as np
import pandas as pd
from datetime import datetime
from flask import Flask, jsonify, render_template, request
from connection import OracleDB

app = Flask(__name__)

# ── config ───────────────────────────────────────────────────────────────────

SERVICE_DAY  = "16-May-2026"
BASE_VERSION = "20260503"
KM_LAT       = 1 / 111.0
DSDB_CONFIG  = {"user": "dsdb", "password": "elcaro", "dsn": "localhost:1521/pdb1"}

# ── vehicle trip-scoring constants ───────────────────────────────────────────

SCORE_MAX_DISTANCE_M   = 200.0
SCORE_MAX_HEADING_DIFF = 45.0
SCORE_SCHEDULE_TAU_SEC = 300.0

SCORE_MULTI_TRIP_BONUS_PER_EXTRA = 0.03
SCORE_MULTI_TRIP_BONUS_CAP       = 0.15

SCORE_WEIGHT_DISTANCE = 0.40
SCORE_WEIGHT_HEADING  = 0.35
SCORE_WEIGHT_SCHEDULE = 0.25

# ── confidence-tracker constants ─────────────────────────────────────────────

CONF_PRESENT_PREV_WEIGHT      = 0.6
CONF_PRESENT_CURRENT_WEIGHT   = 0.4
CONF_NEW_CANDIDATE_SEED       = 0.5
CONF_EVICTION_THRESHOLD       = 0.05
CONF_REVERSAL_ANGLE_THRESHOLD = 120.0
CONF_REVERSAL_PENALTY_FACTOR  = 0.3

# ── Riyadh bounding box ──────────────────────────────────────────────────────

RIYADH_MIN_LAT = 24.45
RIYADH_MAX_LAT = 24.95
RIYADH_MIN_LON = 46.45
RIYADH_MAX_LON = 46.95

# ── vehicle position constants ───────────────────────────────────────────────

TECHVHNO        = 2071
BLOCKID         = 1992
TRIPID          = 43582
TARGET_DATETIME = "16-MAY-2026 05.35.52"
CSV_PATH        = r"C:\Users\ShivangGupta\Downloads\vehicleposition\vehicleposition\16may.csv"

# ── server-side cell shapepoints data structure ──────────────────────────────

_cell_shapepoints = {"cell": None, "total": 0, "groups": []}

# ── server-side confidence-tracker state ─────────────────────────────────────
# blockid -> {confidence, consecutive_hits, consecutive_misses, trip_id,
#             trip_count, last_score}
# Global on purpose (per suggest_connectors decision: server-side, shared
# across reloads/tabs) — same pattern as _cell_shapepoints above.

_confidence_table        = {}
_confidence_last_bearing = {"value": None}

print("Loading vehicle positions from CSV...")

try:
    _veh_df = pd.read_csv(CSV_PATH)

    _veh_df["DATE_TIME"] = pd.to_datetime(
        _veh_df["DATE_TIME"],
        format="%d-%b-%y %H.%M.%S.%f"
    )
    _target_dt = pd.to_datetime(TARGET_DATETIME, format="%d-%b-%Y %H.%M.%S")

    _veh_df = _veh_df[
        (_veh_df["TECHVHNO"] == TECHVHNO) &
        (_veh_df["BLOCKID"]  == BLOCKID)  &
        (_veh_df["TRIPID"]   == TRIPID)   &
        (_veh_df["DATE_TIME"] >= _target_dt)
    ]

    _veh_df = _veh_df[[
        "ID", "DATE_TIME", "TECHVHNO", "BLOCKID",
        "TRIPID", "ROUTEID", "LONGITUDE", "LATITUDE", "COMPASSDIRECT"
    ]]
    _veh_df = _veh_df.sort_values("DATE_TIME")
    _veh_df = _veh_df.dropna(subset=["LATITUDE", "LONGITUDE"])

    print(f"Vehicle positions loaded: {len(_veh_df)} points "
          f"(vehicle={TECHVHNO}, block={BLOCKID}, trip={TRIPID})")
    print(_veh_df)

except Exception as e:
    print(f"[vehicle] CSV load failed: {e}")
    _veh_df = pd.DataFrame()

print("Connecting to Oracle and loading stop points...")

bidb = OracleDB()
bidb.connect()

_df = bidb.query(
    """
    SELECT DISTINCT
        b.stoppointid,
        d.latitude,
        d.longitude
    FROM bidb.SA_STOPPOINTS b
    INNER JOIN dsdb.ds_stoppointhistory d
        ON b.stoppointid = d.stoppointid
    WHERE serviceday = :serviceday and baseversion = :baseversion
    """,
    {"serviceday": SERVICE_DAY, "baseversion": BASE_VERSION},
)

bidb.disconnect()

_df = _df.dropna(subset=["LATITUDE", "LONGITUDE"])
_df["LATITUDE"]  = _df["LATITUDE"].astype(float)
_df["LONGITUDE"] = _df["LONGITUDE"].astype(float)
_df = _df[(_df["LATITUDE"] != 0) | (_df["LONGITUDE"] != 0)]

_mean_lat   = float(_df["LATITUDE"].mean())
_center_lon = float(_df["LONGITUDE"].mean())
KM_LON = 1 / (111.0 * math.cos(math.radians(_mean_lat)))

_df["CELL_ROW"] = (_df["LATITUDE"]  / KM_LAT).apply(math.floor)
_df["CELL_COL"] = (_df["LONGITUDE"] / KM_LON).apply(math.floor)

_row_min = math.floor(RIYADH_MIN_LAT / KM_LAT)
_row_max = math.floor(RIYADH_MAX_LAT / KM_LAT)
_col_min = math.floor(RIYADH_MIN_LON / KM_LON)
_col_max = math.floor(RIYADH_MAX_LON / KM_LON)

_stop_counts = (
    _df.groupby(["CELL_ROW", "CELL_COL"])
    .size()
    .reset_index(name="COUNT")
)
_stop_count_map = {
    (int(r["CELL_ROW"]), int(r["CELL_COL"])): int(r["COUNT"])
    for _, r in _stop_counts.iterrows()
}

_cell_counts = pd.DataFrame(
    [
        {"CELL_ROW": r, "CELL_COL": c, "COUNT": _stop_count_map.get((r, c), 0)}
        for r in range(_row_min, _row_max + 1)
        for c in range(_col_min, _col_max + 1)
    ]
)

_max_count = int(_cell_counts["COUNT"].max()) if len(_cell_counts) and _cell_counts["COUNT"].max() > 0 else 1

print(
    f"Ready — {len(_df)} stops · grid = {len(_cell_counts)} cells "
    f"({_row_max - _row_min + 1} rows × {_col_max - _col_min + 1} cols) "
    f"covering full Riyadh bounding box"
)


# ── helpers ──────────────────────────────────────────────────────────────────

def count_to_color(count):
    light = (204, 233, 240)
    dark  = ( 31,  78,  92)
    t = min(count / _max_count, 1.0)
    r = int(light[0] + t * (dark[0] - light[0]))
    g = int(light[1] + t * (dark[1] - light[1]))
    b = int(light[2] + t * (dark[2] - light[2]))
    return f"#{r:02x}{g:02x}{b:02x}"


def cell_bounds(row, col):
    south = row * KM_LAT
    north = south + KM_LAT
    west  = col  * KM_LON
    east  = west + KM_LON
    return south, north, west, east


# ── vehicle trip scoring ─────────────────────────────────────────────────────

def _score_chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _score_distance(distance_m):
    return float(np.clip(1.0 - distance_m / SCORE_MAX_DISTANCE_M, 0.0, 1.0))


def _score_heading(bearing_deg, compass_deg):
    diff = abs(bearing_deg - compass_deg)
    diff = min(diff, 360 - diff)
    return float(np.clip(1.0 - diff / SCORE_MAX_HEADING_DIFF, 0.0, 1.0))


def _score_schedule(sched_dt, reference_dt):
    diff_seconds = abs((sched_dt - reference_dt).total_seconds())
    return float(np.exp(-diff_seconds / SCORE_SCHEDULE_TAU_SEC))


def _score_get_psids(dsdb, shape_ids):
    all_psids = set()
    for chunk in _score_chunked(shape_ids, 999):
        placeholders = ", ".join([f":id{i}" for i in range(len(chunk))])
        params = {f"id{i}": v for i, v in enumerate(chunk)}
        params["baseversion"] = BASE_VERSION
        df = dsdb.query(f"""
            SELECT DISTINCT psid
            FROM dsdb.ds_routegeometry
            WHERE baseversion = :baseversion
              AND (fromshapeid IN ({placeholders})
               OR  toshapeid   IN ({placeholders}))
              AND psid IS NOT NULL
        """, params)
        df.columns = [c.lower() for c in df.columns]
        all_psids.update(df["psid"].tolist())
    return all_psids


def _score_get_route_pattern_combos(dsdb, psids):
    combos = set()
    for chunk in _score_chunked(list(psids), 999):
        placeholders = ", ".join([f":id{i}" for i in range(len(chunk))])
        params = {f"id{i}": v for i, v in enumerate(chunk)}
        params["baseversion"] = BASE_VERSION
        df = dsdb.query(f"""
            SELECT DISTINCT routeid, patternid
            FROM dsdb.ds_routegeometry
            WHERE baseversion = :baseversion
              AND psid IN ({placeholders})
        """, params)
        df.columns = [c.lower() for c in df.columns]
        for row in df.itertuples(index=False):
            combos.add((row.routeid, row.patternid))
    return combos


def _score_get_trips(dsdb, combos, serviceday, reference_time_str):
    frames = []
    for routeid, patternid in sorted(combos):
        df = dsdb.query("""
            SELECT DISTINCT blockid, tripid, sched_trip_start_time
            FROM bidb.sa_stoppoints
            WHERE routeid   = :routeid
              AND patternid = :patternid
              AND serviceday = TO_DATE(:serviceday, 'DD-MON-YYYY')
              AND sched_trip_start_time >= TO_DATE(:referencetime, 'DD-MON-YYYY HH24:MI:SS') - INTERVAL '15' MINUTE
              AND sched_trip_start_time <= TO_DATE(:referencetime, 'DD-MON-YYYY HH24:MI:SS') + INTERVAL '10' MINUTE
            ORDER BY sched_trip_start_time
        """, {"routeid": routeid, "patternid": patternid,
              "serviceday": serviceday, "referencetime": reference_time_str})

        if df.empty:
            continue

        df.columns      = [c.lower() for c in df.columns]
        df["routeid"]   = routeid
        df["patternid"] = patternid
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["blockid", "tripid", "sched_trip_start_time", "routeid", "patternid"])
    return pd.concat(frames, ignore_index=True)


# ── confidence tracker ────────────────────────────────────────────────────────

def _confidence_decay_rate(consecutive_misses):
    """Progressive decay: gentle for a single miss, harsher the longer it's missed."""
    return max(0.30, 0.85 - 0.15 * (consecutive_misses - 1))


def _confidence_bearing_delta(current_bearing, previous_bearing):
    """Smallest angle (0-180) between two compass bearings, wrap-around safe."""
    diff = abs(current_bearing - previous_bearing) % 360
    return min(diff, 360 - diff)


def _confidence_apply_reversal(current_compass):
    """
    If the vehicle's heading swung more than CONF_REVERSAL_ANGLE_THRESHOLD
    since the last included observation, multiply every tracked block's
    confidence by CONF_REVERSAL_PENALTY_FACTOR. Updates the stored bearing
    regardless. Returns True if the penalty fired.
    """
    previous_compass = _confidence_last_bearing["value"]
    fired = False

    if previous_compass is not None and current_compass is not None:
        if _confidence_bearing_delta(current_compass, previous_compass) > CONF_REVERSAL_ANGLE_THRESHOLD:
            for s in _confidence_table.values():
                s["confidence"] *= CONF_REVERSAL_PENALTY_FACTOR
            fired = True

    if current_compass is not None:
        _confidence_last_bearing["value"] = current_compass

    return fired


def _confidence_update(blocks):
    """
    Applies one observation's block list to _confidence_table in place.
    `blocks` is a list of dicts, each with at least: blockid, block_score,
    trip_id, trip_count. Returns the list of evicted blockids.
    """
    seen = {int(b["blockid"]): b for b in blocks}
    seen_ids    = set(seen.keys())
    tracked_ids = set(_confidence_table.keys())

    # (a) present again — EMA update
    for bid in seen_ids & tracked_ids:
        s = _confidence_table[bid]
        score = float(seen[bid]["block_score"])
        s["confidence"] = (
            CONF_PRESENT_PREV_WEIGHT * s["confidence"]
            + CONF_PRESENT_CURRENT_WEIGHT * score
        )
        s["consecutive_hits"]   += 1
        s["consecutive_misses"]  = 0
        s["trip_id"]    = seen[bid].get("trip_id")
        s["trip_count"] = seen[bid].get("trip_count")
        s["last_score"] = score

    # (b) brand new candidate — conservative seed
    for bid in seen_ids - tracked_ids:
        score = float(seen[bid]["block_score"])
        _confidence_table[bid] = {
            "confidence": score * CONF_NEW_CANDIDATE_SEED,
            "consecutive_hits": 1,
            "consecutive_misses": 0,
            "trip_id": seen[bid].get("trip_id"),
            "trip_count": seen[bid].get("trip_count"),
            "last_score": score,
        }

    # (c) missed this round — progressive decay
    for bid in tracked_ids - seen_ids:
        s = _confidence_table[bid]
        s["consecutive_misses"] += 1
        s["consecutive_hits"]    = 0
        s["confidence"] *= _confidence_decay_rate(s["consecutive_misses"])
        s["last_score"] = None

    # clip to [0, 1]
    for s in _confidence_table.values():
        s["confidence"] = min(1.0, max(0.0, s["confidence"]))

    # evict below threshold
    evicted = [bid for bid, s in _confidence_table.items() if s["confidence"] < CONF_EVICTION_THRESHOLD]
    for bid in evicted:
        del _confidence_table[bid]

    return evicted


def _confidence_table_out():
    rows = [
        {
            "blockid":            bid,
            "trip_id":            s.get("trip_id"),
            "confidence":         round(s["confidence"], 4),
            "consecutive_hits":   s["consecutive_hits"],
            "consecutive_misses": s["consecutive_misses"],
            "trip_count":         s.get("trip_count"),
            "last_score":         round(s["last_score"], 4) if s.get("last_score") is not None else None,
        }
        for bid, s in _confidence_table.items()
    ]
    rows.sort(key=lambda r: r["confidence"], reverse=True)
    return rows


# ── routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        service_day=SERVICE_DAY,
        total_stops=len(_df),
        total_cells=len(_cell_counts),
        center_lat=round(_mean_lat, 6),
        center_lon=round(_center_lon, 6),
        veh_techvhno=TECHVHNO,
        veh_blockid=BLOCKID,
        veh_tripid=TRIPID,
        veh_points=len(_veh_df),
    )


@app.route("/api/vehicle-route")
def api_vehicle_route():
    if _veh_df is None or _veh_df.empty:
        return jsonify({
            "vehicle": TECHVHNO, "block": BLOCKID, "trip": TRIPID,
            "total": 0, "points": [],
        })

    points = [
        {
            "id":       int(r["ID"]),
            "time":     r["DATE_TIME"].strftime("%H:%M:%S"),
            "datetime": r["DATE_TIME"].strftime("%d-%b-%Y %H:%M:%S"),
            "lat":      float(r["LATITUDE"]),
            "lon":      float(r["LONGITUDE"]),
            "compass":  int(r["COMPASSDIRECT"]) if pd.notna(r["COMPASSDIRECT"]) else None,
            "routeid":  int(r["ROUTEID"])       if pd.notna(r["ROUTEID"])       else None,
            "blockid":  int(r["BLOCKID"])       if pd.notna(r["BLOCKID"])       else None,
            "tripid":   int(r["TRIPID"])        if pd.notna(r["TRIPID"])        else None,
            "techvhno": int(r["TECHVHNO"])      if pd.notna(r["TECHVHNO"])      else None,
        }
        for _, r in _veh_df.iterrows()
    ]

    return jsonify({
        "vehicle": TECHVHNO,
        "block":   BLOCKID,
        "trip":    TRIPID,
        "total":   len(points),
        "points":  points,
    })


@app.route("/api/grid")
def api_grid():
    min_lat = request.args.get("min_lat", type=float)
    max_lat = request.args.get("max_lat", type=float)
    min_lon = request.args.get("min_lon", type=float)
    max_lon = request.args.get("max_lon", type=float)

    df = _cell_counts

    if None not in (min_lat, max_lat, min_lon, max_lon):
        row_lo = math.floor(min_lat / KM_LAT)
        row_hi = math.floor(max_lat / KM_LAT)
        col_lo = math.floor(min_lon / KM_LON)
        col_hi = math.floor(max_lon / KM_LON)
        df = df[
            (df["CELL_ROW"] >= row_lo) & (df["CELL_ROW"] <= row_hi) &
            (df["CELL_COL"] >= col_lo) & (df["CELL_COL"] <= col_hi)
        ]

    cells = []
    for _, row in df.iterrows():
        cr  = int(row["CELL_ROW"])
        cc  = int(row["CELL_COL"])
        cnt = int(row["COUNT"])
        s, n, w, e = cell_bounds(cr, cc)
        cells.append({
            "row": cr,   "col": cc,   "count": cnt,
            "south": round(s, 8), "north": round(n, 8),
            "west":  round(w, 8), "east":  round(e, 8),
            "color": count_to_color(cnt),
        })
    return jsonify({"cells": cells, "max_count": _max_count, "total_cells": len(_cell_counts)})


@app.route("/api/stops")
def api_stops():
    stops = [
        {
            "id":  int(r["STOPPOINTID"]),
            "lat": round(float(r["LATITUDE"]),  6),
            "lon": round(float(r["LONGITUDE"]), 6),
        }
        for _, r in _df.iterrows()
    ]
    return jsonify({"stops": stops, "total": len(stops)})


@app.route("/api/cell-stops")
def api_cell_stops():
    row = request.args.get("row", type=int)
    col = request.args.get("col", type=int)
    if row is None or col is None:
        return jsonify({"error": "row and col required"}), 400

    s, n, w, e = cell_bounds(row, col)
    mask  = (_df["CELL_ROW"] == row) & (_df["CELL_COL"] == col)
    chunk = _df[mask]

    stops = sorted(
        [
            {
                "id":  int(r["STOPPOINTID"]),
                "lat": round(float(r["LATITUDE"]),  6),
                "lon": round(float(r["LONGITUDE"]), 6),
            }
            for _, r in chunk.iterrows()
        ],
        key=lambda x: x["id"],
    )

    return jsonify({
        "cell": {
            "row": row, "col": col, "count": len(stops),
            "south": round(s, 6), "north": round(n, 6),
            "west":  round(w, 6), "east":  round(e, 6),
            "center_lat": round((s + n) / 2, 6),
            "center_lon": round((w + e) / 2, 6),
        },
        "stops": stops,
    })


@app.route("/api/stop-routes")
def api_stop_routes():
    stop_id = request.args.get("stop_id", type=int)
    if stop_id is None:
        return jsonify({"error": "stop_id required"}), 400

    dsdb = OracleDB(**DSDB_CONFIG)
    dsdb.connect()

    df = dsdb.query(
        """
        SELECT routeid, patternid
        FROM dsdb.ds_routepathhistory
        WHERE stoppointid = :stoppointid
        """,
        {"stoppointid": stop_id},
    )

    dsdb.disconnect()

    routes = (
        df[["ROUTEID", "PATTERNID"]]
        .drop_duplicates()
        .sort_values("ROUTEID")
        .to_dict(orient="records")
    )

    return jsonify({"stop_id": stop_id, "routes": routes, "total": len(routes)})


@app.route("/api/route-geometry")
def api_route_geometry():
    route_id   = request.args.get("routeid",   type=int)
    pattern_id = request.args.get("patternid", type=int)

    if route_id is None or pattern_id is None:
        return jsonify({"error": "routeid and patternid required"}), 400

    dsdb = OracleDB(**DSDB_CONFIG)
    dsdb.connect()

    df = dsdb.query(
        """
        SELECT DISTINCT psid,
               fromshapeid  shapepointid,
               fromlatitude  latitude,
               fromlongitude longitude
        FROM dsdb.ds_routegeometry
        WHERE routeid     = :routeid
          AND patternid   = :patternid
          AND baseversion = :baseversion
        ORDER BY psid, fromshapeid
        """,
        {
            "routeid":     route_id,
            "patternid":   pattern_id,
            "baseversion": BASE_VERSION,
        },
    )

    dsdb.disconnect()

    df = df.dropna(subset=["PSID", "SHAPEPOINTID", "LATITUDE", "LONGITUDE"])

    if df.empty:
        return jsonify({
            "route_id": route_id, "pattern_id": pattern_id,
            "psids": [], "total_psids": 0, "total_points": 0,
        })

    psid_map = {}
    for _, row in df.iterrows():
        psid = float(row["PSID"])
        if psid not in psid_map:
            psid_map[psid] = []
        psid_map[psid].append({
            "shapepointid": float(row["SHAPEPOINTID"]),
            "lat": float(row["LATITUDE"])  / 3_600_000,
            "lon": float(row["LONGITUDE"]) / 3_600_000,
        })

    psids = [
        {"psid": psid, "points": points}
        for psid, points in sorted(psid_map.items())
    ]

    return jsonify({
        "route_id":     route_id,
        "pattern_id":   pattern_id,
        "psids":        psids,
        "total_psids":  len(psids),
        "total_points": len(df),
    })


@app.route("/api/grid-shapepoints")
def api_grid_shapepoints():
    global _cell_shapepoints

    row = request.args.get("row", type=int)
    col = request.args.get("col", type=int)
    if row is None or col is None:
        return jsonify({"error": "row and col required"}), 400

    s, n, w, e = cell_bounds(row, col)

    south_sc = math.floor(s * 3_600_000)
    north_sc = math.ceil(n  * 3_600_000)
    west_sc  = math.floor(w * 3_600_000)
    east_sc  = math.ceil(e  * 3_600_000)
    print(f"[grid-shapepoints] scaled bounds → south={south_sc} north={north_sc} west={west_sc} east={east_sc}")

    dsdb = OracleDB(**DSDB_CONFIG)
    dsdb.connect()

    df = dsdb.query(
        """
        SELECT shapepointid, latitude, longitude
        FROM (
            SELECT fromshapeid  AS shapepointid,
                   fromlatitude  AS latitude,
                   fromlongitude AS longitude
            FROM dsdb.ds_routegeometry
            WHERE fromlatitude  BETWEEN :south AND :north
              AND fromlongitude BETWEEN :west  AND :east
              AND baseversion   = :baseversion

            UNION

            SELECT toshapeid    AS shapepointid,
                   tolatitude    AS latitude,
                   tolongitude   AS longitude
            FROM dsdb.ds_routegeometry
            WHERE tolatitude    BETWEEN :south AND :north
              AND tolongitude   BETWEEN :west  AND :east
              AND baseversion   = :baseversion
        )
        """,
        {
            "south":       south_sc,
            "north":       north_sc,
            "west":        west_sc,
            "east":        east_sc,
            "baseversion": BASE_VERSION,
        },
    )

    dsdb.disconnect()

    # ── group by (latitude, longitude) ──────────────────────────────────────
    # shapepoints sharing the exact same raw lat/lon are at the same physical
    # location → same distance + bearing from the vehicle → one group.
    # shapegroupid is sequential per request: 1, 2, 3, ...
    # distance_score + heading_score are shared for all shape_ids in a group;
    # schedule_score is computed per-trip on the backend (independent of group).

    location_map         = {}   # (raw_lat, raw_lon) → group dict
    shapegroupid_counter = 1

    for _, row_data in df.iterrows():
        shape_id = int(row_data["SHAPEPOINTID"])
        lat_raw  = row_data["LATITUDE"]
        lon_raw  = row_data["LONGITUDE"]

        key = (lat_raw, lon_raw)   # raw scaled integers — exact match grouping

        if key not in location_map:
            location_map[key] = {
                "shapegroupid": shapegroupid_counter,
                "lat":          float(lat_raw) / 3_600_000,
                "lon":          float(lon_raw) / 3_600_000,
                "shape_ids":    [],
            }
            shapegroupid_counter += 1

        location_map[key]["shape_ids"].append(shape_id)

    groups = list(location_map.values())

    _cell_shapepoints = {
        "cell": {
            "row":   row,  "col":   col,
            "south": round(s, 6), "north": round(n, 6),
            "west":  round(w, 6), "east":  round(e, 6),
        },
        "total":  len(groups),
        "groups": groups,
    }

    print(
        f"[grid-shapepoints] cell ({row},{col}) → "
        f"{len(df)} raw shapepoints → "
        f"{len(groups)} location groups stored"
    )
    return jsonify(_cell_shapepoints)


@app.route("/api/vehicle-score", methods=["POST"])
def api_vehicle_score():
    """
    Scores candidate trips/blocks for the vehicle's current GPS position.

    Expects JSON body:
      {
        "groups": [
          { "distance": 13.2, "bearing": 234.1, "shape_ids": [53588, 54129, 157223] },
          ...
        ],
        "reference_time": "16-MAY-2026 05:35:36",
        "compass": 225
      }

    Each group corresponds to one location group (same lat/lon) from the
    grid-shapepoints response. All shape_ids within a group share the same
    distance_score and heading_score (computed once per group). schedule_score
    is computed independently per tripid regardless of group.

    Block-level output also carries `trip_id` — the tripid that achieved
    that block's max_trip_score — so the confidence tracker (and the
    console.table view) always know which specific trip to associate with
    the winning block.
    """
    payload            = request.get_json(silent=True) or {}
    groups             = payload.get("groups", [])
    reference_time_str = payload.get("reference_time")
    compass            = payload.get("compass")

    if not groups or reference_time_str is None or compass is None:
        return jsonify({"error": "groups, reference_time and compass are required"}), 400

    try:
        reference_dt = datetime.strptime(reference_time_str, "%d-%b-%Y %H:%M:%S")
    except ValueError:
        return jsonify({"error": "reference_time must look like '16-MAY-2026 05:35:36'"}), 400

    compass = float(compass)

    dsdb = OracleDB(**DSDB_CONFIG)
    dsdb.connect()

    all_trip_rows = []
    try:
        for gi, group in enumerate(groups, 1):
            distance  = float(group.get("distance", 0))
            bearing   = float(group.get("bearing",  0))
            shape_ids = group.get("shape_ids", [])
            if not shape_ids:
                continue

            # distance_score + heading_score — computed ONCE per group
            # (all shape_ids in the group are at the same lat/lon)
            dist_score    = _score_distance(distance)
            heading_score = _score_heading(bearing, compass)

            psids = _score_get_psids(dsdb, shape_ids)
            if not psids:
                continue

            combos = _score_get_route_pattern_combos(dsdb, psids)
            if not combos:
                continue

            trips_df = _score_get_trips(dsdb, combos, SERVICE_DAY, reference_time_str)
            if trips_df.empty:
                continue

            trips_df["group_index"]    = gi
            trips_df["group_distance"] = distance
            trips_df["group_bearing"]  = bearing
            trips_df["distance_score"] = dist_score      # shared for all trips in this group
            trips_df["heading_score"]  = heading_score   # shared for all trips in this group
            all_trip_rows.append(trips_df)

    finally:
        dsdb.disconnect()

    if not all_trip_rows:
        return jsonify({
            "reference_time": reference_time_str, "compass": compass,
            "trips": [], "blocks": [],
        })

    final = pd.concat(all_trip_rows, ignore_index=True)

    # schedule_score — computed independently per tripid (per sched_trip_start_time)
    # regardless of which group the shape_ids came from
    final["sched_trip_start_time"] = pd.to_datetime(final["sched_trip_start_time"])
    final["schedule_score"] = final["sched_trip_start_time"].apply(
        lambda dt: _score_schedule(dt, reference_dt)
    )

    final["final_score"] = (
        SCORE_WEIGHT_DISTANCE * final["distance_score"]
        + SCORE_WEIGHT_HEADING  * final["heading_score"]
        + SCORE_WEIGHT_SCHEDULE * final["schedule_score"]
    )
    final = final.sort_values(by="final_score", ascending=False, ignore_index=True)

    # ── block-level aggregation ──────────────────────────────────────────────
    block_scores = final.groupby("blockid").agg(
        max_trip_score=("final_score", "max"),
        trip_count=("tripid", "nunique"),
    ).reset_index()

    block_scores["bonus"] = np.clip(
        (block_scores["trip_count"] - 1) * SCORE_MULTI_TRIP_BONUS_PER_EXTRA,
        0.0, SCORE_MULTI_TRIP_BONUS_CAP,
    )
    block_scores["block_score"] = np.clip(
        block_scores["max_trip_score"] + block_scores["bonus"], 0.0, 1.0
    )

    # tripid that achieved max_trip_score, per block
    best_idx      = final.groupby("blockid")["final_score"].idxmax()
    best_trip_map = final.loc[best_idx].set_index("blockid")["tripid"].to_dict()
    block_scores["trip_id"] = block_scores["blockid"].map(best_trip_map)

    block_scores = block_scores.sort_values(by="block_score", ascending=False, ignore_index=True)

    trips_out = [
        {
            "blockid":   int(r.blockid),
            "tripid":    int(r.tripid),
            "routeid":   int(r.routeid),
            "patternid": int(r.patternid),
            "sched_trip_start_time": r.sched_trip_start_time.strftime("%d-%b-%Y %H:%M:%S"),
            "group_distance":  round(float(r.group_distance), 1),
            "group_bearing":   round(float(r.group_bearing),  1),
            "distance_score":  round(float(r.distance_score), 4),
            "heading_score":   round(float(r.heading_score),  4),
            "schedule_score":  round(float(r.schedule_score), 4),
            "final_score":     round(float(r.final_score),    4),
        }
        for r in final.itertuples(index=False)
    ]

    blocks_out = [
        {
            "blockid":        int(r.blockid),
            "trip_id":        int(r.trip_id),
            "max_trip_score": round(float(r.max_trip_score), 4),
            "trip_count":     int(r.trip_count),
            "bonus":          round(float(r.bonus),          4),
            "block_score":    round(float(r.block_score),    4),
        }
        for r in block_scores.itertuples(index=False)
    ]

    print(
        f"[vehicle-score] ref={reference_time_str} compass={compass}° → "
        f"{len(trips_out)} trip candidates, top block="
        f"{blocks_out[0]['blockid'] if blocks_out else 'None'}"
    )

    return jsonify({
        "reference_time": reference_time_str,
        "compass":        compass,
        "trips":          trips_out,
        "blocks":         blocks_out,
    })


@app.route("/api/confidence", methods=["GET"])
def api_confidence_get():
    return jsonify({
        "table":        _confidence_table_out(),
        "last_bearing": _confidence_last_bearing["value"],
    })


@app.route("/api/confidence/include", methods=["POST"])
def api_confidence_include():
    """
    Feeds one observation's block scores into the server-side confidence
    tracker. Expects JSON body:
      { "blocks": [ {blockid, trip_id, block_score, trip_count, ...}, ... ],
        "compass": 225 }

    Runs the bearing-reversal check first (against the compass stored from
    the previous included observation), then the present/new/missed update,
    then clips and evicts. Returns the updated table plus what happened.
    """
    payload = request.get_json(silent=True) or {}
    blocks  = payload.get("blocks", [])
    compass = payload.get("compass")

    if compass is not None:
        compass = float(compass)

    reversal_fired = _confidence_apply_reversal(compass)
    evicted        = _confidence_update(blocks)
    table           = _confidence_table_out()

    print(
        f"[confidence] included {len(blocks)} block(s) · "
        f"reversal={reversal_fired} · evicted={evicted}"
    )

    return jsonify({
        "table":          table,
        "reversal_fired": reversal_fired,
        "evicted":        evicted,
    })


@app.route("/api/confidence/reset", methods=["POST"])
def api_confidence_reset():
    _confidence_table.clear()
    _confidence_last_bearing["value"] = None
    print("[confidence] table reset")
    return jsonify({"table": []})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)