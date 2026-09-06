# BSU group 653 timetable

A local, minimal timetable application with a light sage palette for BSU group 653. It shows
the current or next class, today's agenda, every published week, subgroup
filters, rooms, teachers, and calculated class end times.

The BSU source only publishes start times. This application calculates each end
time as 85 minutes after the start: two 40-minute periods and a five-minute
break.

## Install and run

Python 3.11 or newer is required.

```bash
python -m pip install -e .
python -m timetable_fetcher
```

The app starts at `http://127.0.0.1:8000/` and opens in the default browser.
Stop it with `Ctrl+C`.

Options:

```bash
python -m timetable_fetcher --port 8080
python -m timetable_fetcher --no-open
```

The server is deliberately bound to `127.0.0.1`; it is not exposed to the
internet or other devices on the local network.

## Refresh behavior

The interface displays `docs/timetable.json` immediately, then contacts BSU
once when the page opens. Press **Fetch timetable** at any time to refresh it
again.

Each refresh:

- downloads the group 653 page with a trusted CA bundle, a 20-second timeout,
  and up to three attempts;
- falls back to Jina Reader's HTTPS HTML relay only when the official BSU host
  rejects the client's TLS protocol or the connection times out; the relayed page is public timetable data
  and is still parsed and validated before it can replace the snapshot;
- discovers all week identifiers currently published by BSU, including weeks
  added after this application was written;
- parses every class and calculates its timezone-aware end time;
- writes the snapshot atomically only when the timetable or source update time
  changed;
- leaves the previous snapshot intact if downloading or parsing fails.

Only one refresh can run at a time. A failed automatic refresh does not blank
the page: the saved timetable stays visible with a retry message.

## Vercel deployment

The repository includes `vercel.json` and a Python function at
`api/refresh.py`. Deploy from the repository root with `npx vercel --prod`.
The frontend is served from `docs/`; no frontend build or framework migration
is required. `.python-version` selects Python 3.12, and dependencies come from
`pyproject.toml`.

On Vercel, automatic and manual refreshes fetch and validate the BSU timetable
without writing deployment files. Successful responses are cached for 60 seconds
within each function instance; simultaneous requests to that instance share the
fetch. This cache is not shared across instances and is not durable storage.

The browser saves each successful timetable and displays the newest available
saved or bundled snapshot on startup. If BSU is unavailable, the displayed
snapshot remains intact. Saved updates persist in that browser when local storage
is available; a new browser initially receives the deployment's bundled snapshot
until a live refresh succeeds. There is no shared persistent database. Local
Python server mode continues to update `docs/timetable.json` atomically.

Newly published weeks appear on the next successful refresh without redeploying.
After deployment, verify `/`, `/timetable.json`, and `POST /api/refresh`, including
BSU failure fallback, subgroup selection, and calendar downloads.

## Local API

The web interface and API share the same local origin:

- `GET /` — timetable interface
- `GET /timetable.json` — saved schema-version-1 snapshot
- `POST /api/refresh` — fetch BSU and return `{ "changed", "timetable" }`

An unsuccessful refresh returns a JSON error with status `502`. Overlapping
refreshes return `409`, and cross-origin browser refresh requests are rejected.

The JSON groups data as `weeks → days → classes`. Each class includes
`start_time`, `end_time`, timezone-aware `starts_at` and `ends_at`, `subject`,
`teacher`, `room`, and `subgroup`. Shared classes have a `null` subgroup.

## Interface features

- Current/next class status and a countdown updated every minute
- Minsk-time date and class calculations, independent of the computer timezone
- Automatic selection of the current, nearest future, or latest published week
- Remembered All/Subgroup 1/Subgroup 2 filter
- Separate display of simultaneous subgroup classes
- Per-class `.ics` calendar downloads
- Responsive keyboard-accessible layout with reduced-motion support

## Tests

Run the offline Python and JavaScript suites:

```bash
python -m unittest discover -s tests -v
node --test tests/*.test.mjs
```

The HTTP tests open an ephemeral loopback port. To query the real BSU page as an
additional integration test:

```bash
RUN_LIVE_TESTS=1 python -m unittest tests.test_live -v
```

The source URL looks like XML but returns HTML:
`https://sb.bsu.by/raspisanie/map-653__engl_.xml`.

## Troubleshooting

If the interface says that BSU's timetable server is unavailable, neither the
official endpoint nor (for a TLS protocol mismatch or timeout) the secure HTML relay could
return the page after all retries. The application will continue showing the
last valid snapshot and will not overwrite it. Wait a few minutes and press
**Fetch timetable** again; certificate verification should not be disabled.
