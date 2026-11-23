"""Serve sampled Earth-fixed CZML for satellites from CelesTrak groups."""

import argparse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
from pathlib import Path
import re
from threading import Event, Thread
import tomllib
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from skyfield.api import EarthSatellite, load
from skyfield.framelib import itrs

WEB_ROOT = Path(__file__).resolve().parent / "web"
WEB_ASSETS = {
    "/": ("index.html", "text/html"),
    "/ui/catalog.css": ("catalog.css", "text/css"),
    "/ui/catalog.js": ("catalog.js", "text/javascript"),
}
GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
GROUP_ID = r"[a-z0-9]+(?:-[a-z0-9]+)*"
GROUP_ROUTE = re.compile(rf"/groups/({GROUP_ID})(/orbit\.czml)?")
GROUP_PAGE_SIZE = 100
CATALOG_PATH = "/satellites"
ORBIT_ROUTE = re.compile(r"/satellites/([0-9]{1,9})/orbit\.czml")
SAMPLE_SECONDS = 15
REFRESH_SECONDS = 2 * 60 * 60


def load_catalog(timescale, fixture, group):
    if fixture is None:
        query = urlencode({"GROUP": group, "FORMAT": "json"})
        with urlopen(f"{GP_URL}?{query}", timeout=30) as response:
            records = json.load(response)
    else:
        with fixture.open(encoding="utf-8") as source:
            records = json.load(source)

    if not isinstance(records, list):
        raise ValueError("Expected a GP JSON array.")

    catalog = {}
    for fields in records:
        try:
            catalog_id = int(fields["NORAD_CAT_ID"])
            satellite = EarthSatellite.from_omm(timescale, fields)
        except (ValueError, TypeError, KeyError, OverflowError):
            logging.warning("Skipping invalid GP record with catalog ID %s",
                            fields.get("NORAD_CAT_ID") if isinstance(fields, dict) else None)
            continue
        previous = catalog.get(catalog_id)
        if previous is None or previous.epoch < satellite.epoch:
            catalog[catalog_id] = satellite

    if not catalog:
        raise ValueError("Element feed contained no usable satellites.")
    return catalog


def refresh_elements(server, stop):
    while not stop.wait(REFRESH_SECONDS):
        groups = server.groups.copy()
        for group in groups:
            if stop.is_set():
                return
            try:
                groups[group] = load_catalog(server.timescale, None, group)
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                logging.exception("Refresh failed for group %s. Keeping its last catalog.", group)
        server.groups = groups


def parse_window(parameters, default_start):
    start = default_start
    if "start" in parameters:
        value = parameters["start"][0]
        if "T" not in value:
            raise ValueError("start must be an ISO 8601 timestamp with a timezone.")
        start = datetime.fromisoformat(value)
        if start.utcoffset() is None:
            raise ValueError("start must include a timezone.")
        start = start.astimezone(UTC)

    duration = parameters.get("duration", ["2h"])[0]
    match = re.fullmatch(r"([0-9]{1,4})([mh])", duration)
    if match is None:
        raise ValueError("duration must use whole minutes or hours, such as 90m or 2h.")
    count, unit = match.groups()
    minutes = int(count) * (60 if unit == "h" else 1)
    if not 1 <= minutes <= 24 * 60:
        raise ValueError("duration must be between 1m and 24h.")
    return start, start + timedelta(minutes=minutes)


def orbit_packet(catalog_id, satellite, times, offsets, epoch, interval):
    orbit = satellite.at(times)
    for message in orbit.message:
        if message is not None:
            raise ValueError(f"SGP4 could not propagate the requested window: {message}")
    positions = orbit.frame_xyz(itrs).m.T
    cartesian = []
    for offset, (x, y, z) in zip(offsets, positions, strict=True):
        if not all(map(math.isfinite, (x, y, z))):
            raise ValueError("Propagation produced a non-finite position.")
        cartesian.extend((offset, float(x), float(y), float(z)))

    return {
        "id": f"norad-{catalog_id}",
        "name": satellite.name or f"NORAD {catalog_id}",
        "availability": interval,
        "properties": {"elementEpoch": satellite.epoch.utc_iso(places=6)},
        "position": {
            "referenceFrame": "FIXED",
            "epoch": epoch,
            "interpolationAlgorithm": "LINEAR",
            "interpolationDegree": 1,
            "cartesian": cartesian,
        },
        "point": {"pixelSize": 9, "color": {"rgba": [255, 210, 70, 255]}},
        "path": {
            "show": True,
            "width": 2,
            "leadTime": 0,
            "trailTime": 5400,
            "resolution": SAMPLE_SECONDS,
            "material": {"solidColor": {"color": {"rgba": [255, 210, 70, 200]}}},
        },
    }


def orbit_czml(name, satellites, timescale, start, end, *, skip_failed=False):
    offsets = range(0, int((end - start).total_seconds()) + 1, SAMPLE_SECONDS)
    times = timescale.from_datetimes([start + timedelta(seconds=t) for t in offsets])
    epoch = start.isoformat().replace("+00:00", "Z")
    interval = f"{epoch}/{end.isoformat().replace('+00:00', 'Z')}"
    packets = [{
        "id": "document",
        "name": name,
        "version": "1.0",
        "clock": {
            "interval": interval,
            "currentTime": epoch,
            "multiplier": 60,
            "range": "LOOP_STOP",
        },
    }]
    for catalog_id, satellite in satellites:
        try:
            packets.append(orbit_packet(catalog_id, satellite, times, offsets, epoch, interval))
        except (ValueError, OverflowError) as error:
            if not skip_failed:
                raise
            logging.warning("Skipping NORAD ID %s: %s", catalog_id, error)
    if len(packets) == 1:
        raise ValueError("No satellites could be propagated for the requested window.")
    return packets


def satellite_listing(catalog):
    return [
        {
            "noradId": catalog_id,
            "name": satellite.name or f"NORAD {catalog_id}",
            "elementEpoch": satellite.epoch.utc_iso(places=6),
            "orbitUrl": f"{CATALOG_PATH}/{catalog_id}/orbit.czml",
        }
        for catalog_id, satellite in sorted(catalog.items())
    ]


class OrbitServer(ThreadingHTTPServer):
    def __init__(self, address, fixture, groups):
        self.timescale = load.timescale(builtin=True)
        self.groups = {group: load_catalog(self.timescale, fixture, group) for group in groups}
        self.fixture = fixture
        super().__init__(address, OrbitHandler)


class OrbitHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        target = urlsplit(self.path)
        asset = WEB_ASSETS.get(target.path)
        if asset:
            filename, content_type = asset
            try:
                body = (WEB_ROOT / filename).read_bytes()
            except OSError:
                logging.exception("Could not read UI asset %s", filename)
                self.send_error(500, "UI asset is unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        groups = self.server.groups
        if target.path in {"/groups", "/ui/catalog-config"}:
            listing = [
                {
                    "id": group,
                    "count": len(catalog),
                    "membersUrl": f"/groups/{group}",
                    "orbitUrl": f"/groups/{group}/orbit.czml",
                    "pageSize": GROUP_PAGE_SIZE,
                }
                for group, catalog in groups.items()
            ]
            if target.path == "/ui/catalog-config":
                self.send_json(200, {"groups": listing, "fixture": self.server.fixture is not None})
            else:
                self.send_json(200, listing)
            return

        group_route = GROUP_ROUTE.fullmatch(target.path)
        if group_route:
            group, orbit_suffix = group_route.groups()
            catalog = groups.get(group)
            if catalog is None:
                self.send_json(404, {"error": f"Group {group} is not configured."})
            elif orbit_suffix:
                self.send_orbits(target.query, group, sorted(catalog.items()), group=True)
            else:
                self.send_json(200, satellite_listing(catalog))
            return

        route = ORBIT_ROUTE.fullmatch(target.path)
        if target.path != CATALOG_PATH and route is None:
            self.send_json(404, {"error": "Unknown endpoint."})
            return

        if target.path == CATALOG_PATH:
            catalog = {}
            for members in groups.values():
                for catalog_id, satellite in members.items():
                    previous = catalog.get(catalog_id)
                    if previous is None or previous.epoch < satellite.epoch:
                        catalog[catalog_id] = satellite
            self.send_json(200, satellite_listing(catalog))
            return

        catalog_id = int(route[1])
        satellite = max(
            (members[catalog_id] for members in groups.values() if catalog_id in members),
            key=lambda member: member.epoch,
            default=None,
        )
        if satellite is None:
            self.send_json(404, {"error": f"NORAD ID {catalog_id} is unavailable in this catalog."})
            return
        self.send_orbits(target.query, f"norad-{catalog_id}", [(catalog_id, satellite)])

    def send_orbits(self, query, name, satellites, *, group=False):
        try:
            allowed = {"start", "duration", "page"} if group else {"start", "duration"}
            parameters = parse_qs(query, keep_blank_values=True, max_num_fields=len(allowed))
            if any(key not in allowed or len(values) != 1 for key, values in parameters.items()):
                raise ValueError(f"Use at most one of each parameter: {', '.join(sorted(allowed))}.")
            if group:
                page = parameters.get("page", ["1"])[0]
                if not re.fullmatch(r"[1-9][0-9]{0,8}", page):
                    raise ValueError("page must be a positive integer.")
                offset = (int(page) - 1) * GROUP_PAGE_SIZE
                satellites = satellites[offset:offset + GROUP_PAGE_SIZE]
                if not satellites:
                    self.send_json(404, {"error": "No satellites on this page."})
                    return
            default_start = (
                max(satellite.epoch.utc_datetime() for _, satellite in satellites)
                if self.server.fixture else datetime.now(UTC)
            )
            start, end = parse_window(parameters, default_start)
        except (ValueError, OverflowError) as error:
            self.send_json(400, {"error": str(error)})
            return

        label = name if group else satellites[0][1].name or name
        try:
            packets = orbit_czml(
                f"Orbiserv prediction for {label}", satellites,
                self.server.timescale, start, end, skip_failed=group,
            )
        except (ValueError, OverflowError) as error:
            self.send_json(422, {"error": str(error)})
            return

        self.send_json(200, packets, filename=f"{name}.czml")

    def send_json(self, status, data, filename=None):
        body = json.dumps(data, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if filename:
            self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)


def read_config(path, *, optional):
    try:
        with path.open("rb") as source:
            config = tomllib.load(source)
    except FileNotFoundError:
        if optional:
            return {}
        raise

    unknown = config.keys() - {"host", "port", "groups", "fixture"}
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}.")
    for key in ("host", "fixture"):
        if key in config and (not isinstance(config[key], str) or not config[key].strip()):
            raise ValueError(f"{key} must be a non-empty string.")
    if "port" in config and type(config["port"]) is not int:
        raise ValueError("port must be an integer.")
    if "groups" in config:
        groups = config["groups"]
        if not isinstance(groups, list) or not groups or any(
            not isinstance(group, str) for group in groups
        ):
            raise ValueError("groups must be a non-empty array of strings.")
    if "fixture" in config:
        config["fixture"] = path.parent / config["fixture"]
    return config


def parse_settings(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="TOML file (default: ./orbiserv.toml if present).")
    parser.add_argument("--fixture", type=Path, help="Use a local GP JSON file.")
    parser.add_argument("--host", help="IPv4 address to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, help="Port to bind (default: 8000).")
    parser.add_argument(
        "--group", dest="groups", action="append",
        help="CelesTrak GROUP slug. Repeat to load several (default: active).",
    )
    args = parser.parse_args(argv)
    config_path = args.config or Path("orbiserv.toml")
    try:
        config = read_config(config_path, optional=args.config is None)
    except (OSError, ValueError) as error:
        parser.error(f"Could not read {config_path}: {error}")

    args.host = args.host if args.host is not None else config.get("host", "127.0.0.1")
    args.port = args.port if args.port is not None else config.get("port", 8000)
    args.fixture = args.fixture if args.fixture is not None else config.get("fixture")
    args.groups = list(dict.fromkeys(args.groups or config.get("groups", ["active"])))
    if not args.host.strip():
        parser.error("host must be a non-empty string.")
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535.")
    if any(not re.fullmatch(GROUP_ID, group) for group in args.groups):
        parser.error("Group names must be lowercase CelesTrak slugs, such as stations or gps-ops.")
    if args.fixture and len(args.groups) > 1:
        parser.error("Fixture mode accepts one group name.")
    return args


def main():
    args = parse_settings()
    try:
        server = OrbitServer((args.host, args.port), args.fixture, args.groups)
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        raise SystemExit(f"Could not start Orbiserv: {error}") from None

    stop = Event()
    with server:
        if args.fixture is None:
            Thread(target=refresh_elements, args=(server, stop), daemon=True).start()
        host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        print(f"Serving http://{host}:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()


if __name__ == "__main__":
    main()
