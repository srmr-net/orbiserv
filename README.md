# Orbiserv

A local-network service that serves predicted satellite positions as CZML. Skyfield propagates CelesTrak GP elements with SGP4 and
converts each position to Earth-fixed Cartesian coordinates in meters.

## Run with current elements

Use Python 3.11 or later. With uv, run these commands from the project root.

```sh
uv venv
uv pip install -r requirements.txt
.venv/bin/python orbiserv.py
```

Open `http://127.0.0.1:8000/` to search by satellite name or NORAD ID,
browse configured groups, and choose a prediction window. Copy the resulting
feed URL into NeedleEye or download a CZML snapshot. The JSON catalog is
available at `/satellites`.

To use the dashboard and feeds from other machines on your local network,
bind Orbiserv to the machine's LAN address or all IPv4 interfaces.

```sh
.venv/bin/python orbiserv.py --host 0.0.0.0
```

Open port 8000 on the machine's LAN address in your browser. The dashboard
uses that address for the feed URLs it generates. Example URLs on the host
machine:

| Satellite | Feed URL |
| --- | --- |
| ISS (ZARYA) | `http://127.0.0.1:8000/satellites/25544/orbit.czml` |
| Hubble Space Telescope | `http://127.0.0.1:8000/satellites/20580/orbit.czml` |

Add a feed for each satellite you want to display. The default prediction
runs from the time of the request through the next two hours. You can also
save a snapshot and open it in NeedleEye.

```sh
curl --fail --output hubble.czml 'http://127.0.0.1:8000/satellites/20580/orbit.czml'
```

Live mode fetches the [CelesTrak active catalog](https://celestrak.org/NORAD/elements/table.php?GROUP=active)
on startup and refreshes it every two hours. A failed refresh leaves the last
successful catalog in service. Requests for IDs absent from the catalog return
HTTP 404. The CZML entity's `elementEpoch` property records the source elements'
epoch. The document's `clock.interval` gives the time span of the prediction.
The installed SGP4 library may skip catalog IDs above its Alpha-5 limit of
339999.

The optional `start` query parameter accepts an ISO 8601 timestamp with a
timezone and defaults to the current UTC time in live mode. The optional
`duration` accepts whole minutes or hours from `1m` to `24h`, defaulting to
`2h`. Both ends of the interval are sampled at 15-second spacing.

## Configuration file

Copy [orbiserv.example.toml](orbiserv.example.toml) to `orbiserv.toml` and
edit it to suit your network and the groups you want.

```toml
host = "0.0.0.0"
port = 8000
groups = ["stations", "gps-ops", "weather"]
```

Start with `.venv/bin/python orbiserv.py` from the directory containing
`orbiserv.toml`, or pass `--config /path/to/settings.toml` to pick another
file. The default file is optional. Command-line flags override their
matching settings, and supplying any `--group` flags replaces the configured
group list.

To run offline, set `fixture = "fixtures/iss.json"` and configure a single
group. Relative fixture paths in TOML are resolved relative to the config
file. Relative `--fixture` paths are resolved from the working directory.

## Run the May 2024 example offline

```sh
.venv/bin/python orbiserv.py --fixture fixtures/iss.json
```

The bundled fixture contains one historical ISS element set from the
[Skyfield documentation](https://rhodesmill.org/skyfield/earth-satellites.html).
Its default playback starts on May 6, 2024, at the element epoch. Use live
mode for current and near-future predictions. You can also pass your own GP
JSON file with multiple satellite records to `--fixture`.

## Satellite groups

Orbiserv uses CelesTrak's `active` group by default. Choose other groups by
repeating `--group` with the value of `GROUP` in the raw-data links on the
[CelesTrak catalog page](https://celestrak.org/NORAD/elements/index.php?FORMAT=json).

```sh
.venv/bin/python orbiserv.py --group stations --group gps-ops --group weather
```

Each configured group refreshes independently. The individual satellite
endpoints search all configured groups and use the newest element set if a
satellite appears in several groups.

| URL path | Response |
| --- | --- |
| `/groups` | Configured groups, their member counts, and their URLs |
| `/groups/stations` | Member list with individual CZML URLs |
| `/groups/stations/orbit.czml` | One CZML feed for the group, ready to add to NeedleEye |

Group feeds accept the same `start` and `duration` parameters as individual
feeds. All satellites on a page share one prediction window. Groups with more
than 100 members are split into pages ordered by NORAD ID. Use `?page=2`
for the next page, and add each page as a separate NeedleEye feed.
Satellites that fail to propagate are skipped. A page returns HTTP 422
if none of its satellites can be propagated.

To try a group feed offline, start with
`--fixture fixtures/iss.json --group stations`. The bundled fixture will
give that group a single historical ISS record.
