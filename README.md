# Octaris

Control software for the Printess bioprinter. Upload an STL or a pre-sliced G-code file, configure your syringe mode, and manage the full print workflow including slicing, previewing, and live monitoring


## Download

Grab the latest `.dmg` from [Releases](../../releases).

After installing, open Terminal and run:

```bash
xattr -cr /Applications/Octaris.app
```

This removes the macOS quarantine flag so the unsigned app can launch. You only need to do this once after the initial install (automatic updates don't require it)

---

## Requirements

**Hardware**
- Printess bioprinter connected via USB (STM32 Virtual COM Port or similar)
- A hardware emergency stop that cuts power to the motors (see below)

> **Safety: the Stop button is not an emergency stop.** It sends `M410` over
> USB, which only works if the app, the USB link and the firmware are all
> responding, and it doesn't cut motor power. M410 over USB is not a safety
> function. The printer needs its own hardware emergency stop (a latching,
> red-on-yellow switch that removes motor power) within reach of the operator.

> **Safety: keep the firmware's thermal protection enabled.** Octaris warns
> when a temperature drifts from its target during a print, when reports stop
> coming, and when a sensor reads something implausible (below −20 °C or above
> 300 °C, usually a disconnected sensor). These are warnings only, and they
> depend on the app running. Marlin's `THERMAL_PROTECTION_HOTENDS`,
> `THERMAL_PROTECTION_BED` and `THERMAL_PROTECTION_CHAMBER` (thermal runaway
> protection) must stay enabled in the firmware: they cut the heaters when the
> host can't.

**Software (for users)**
- macOS (Apple Silicon)

currently only available for mac users


## Using the App

### Setup screen

1. **Connect the printer**: use the port selector in the top-right corner. Click *Connect*.

2. **Select syringe mode**: choose *Left*, *Right*, or *Both* (both mode is not fully functional yet) syringes. This controls which axes receive extrusion commands. Always zero/calibrate at the left nozzle (the software applies the right nozzle offset automatically).

3. **Upload a file** (two modes are available via the toggle below the syringe selector):
   - **STL File**: upload a `.stl` model, then click *Click to Slice*. The backend runs CuraEngine and post-processes the G-code (extrusion substitution, feed-rate clamping, travel retraction). Requires UltiMaker Cura to be installed.
   - **G-Code File**: upload a pre-sliced `.gcode` file. Processing (extrusion substitution and validation) happens automatically on upload.

4. **Review the G-code preview**: after slicing or upload you'll see the first 40 lines, total line count, and estimated print time.

5. **Proceed to Preview**: click the button to move to the print screen.

### Print screen

- The circular progress indicator shows percentage complete.
- **Pause / Resume / Stop / Restart** buttons control the print queue.
- The **flow rate slider** (50–150%) adjusts extrusion speed live.
- The status bar shows current line number and system state.

### Temperatures

The backend reads the printer's temperatures (Marlin's `M155` auto-report, or
`M105` polling if the firmware doesn't auto-report) and logs every reading to
the SQLite database, tagged with the print it belongs to.

- `GET /temperature`: the latest reading, target and status (heating, cooling,
  at target, off) of each sensor. If the printer never reports a temperature
  after connecting, `state` is `no_sensors`.
- `POST /temperature/target` `{"sensor": "T0", "target": 37}`: sets a target with
  `M104` (T, T0, T1…), `M140` (bed) or `M141` (chamber); `0` turns the heater off.
  Targets outside the sensor's range are refused. This works during a print,
  since it doesn't move anything. `M109`/`M190`, which block the printer until
  the temperature is reached, are never sent.
- `GET /temperature/history?minutes=30` or `?session_id=…`: readings for a chart.
- `GET /temperature/export.csv?session_id=…` or `?from=…&to=…` (ISO 8601): CSV
  download (timestamp, sensor, name, actual, target).
- `POST /print/start` `{"wait_for_temperature": true}`: the print holds its first
  line until every sensor with a target has stayed within ±1 °C of it for 30 s.
  Stopping the print cancels the wait.

### Manual control (Take Over screen)

Accessible from the sidebar during a print or when idle:

- **Serial log** — scrollable view of every command sent to and received from the printer.
- **G-code input** — type any G-code command and send it directly.
- **Quick commands** — buttons for common operations (Home, position query, settings, etc.).
- **Jog panel** — move individual axes by fixed increments (0.1, 1, or 5 mm).
- **Go to Origin** — returns the stage to X0 Y0.
- **Stop** — sends `M410` and flushes the queue immediately. This is not an emergency stop; use the printer's hardware emergency stop for that.

---

## Development Setup

If you want to run from source instead of the packaged app:

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Frontend

```bash
cd client
npm install
```

### Running (dev mode)

Open two terminal windows.

Terminal 1 (backend)
```bash
cd backend
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 (frontend)
```bash
cd client
npm run dev
```

The Electron app opens automatically. The backend must be running for any printer communication to work.

### Without a printer

Start the backend with `OCTARIS_VIRTUAL_PRINTER=1` and the port selector offers a **Virtual printer**: a simulated Marlin board (`backend/backend/virtual_printer.py`) with a 16-move planner, real move timing, temperatures, resends and M410. The whole app can be used against it. `OCTARIS_VIRTUAL_PRINTER_SPEED=20` runs its moves 20× faster than real time.

```bash
OCTARIS_VIRTUAL_PRINTER=1 uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### Tests and checks

```bash
cd backend
pytest              # tests (most run against the virtual printer)
ruff check .        # lint
ruff format .       # format
mypy                # type check

cd ../client
npm run typecheck && npm run lint
```

`pre-commit install` (from the repository root, with the backend's dev dependencies installed) runs all of these on every commit. CI runs them on every push and pull request; pushing a tag builds the macOS app with `build.sh` and attaches the `.dmg` and `.zip` to the workflow run.

### Logs

The backend writes a rotating log to `~/Library/Application Support/Octaris/logs/octaris-backend.log`. Each print's serial traffic goes to its own file in `logs/prints/`, named in the print's history entry (`serial_log`). Set `OCTARIS_DATA_DIR` to put them elsewhere.

### Building the desktop app

```bash
pip install pyinstaller
./build.sh
```

This builds the Python backend into a standalone binary with PyInstaller, then packages everything into a macOS `.app` with electron-builder. Output is in `client/dist/`.

---

## Configuration

`config.json` at the repository root controls runtime behaviour:

| Key | Values | Description |
|-----|--------|-------------|
| `target` | `macos` / `rpi` | Platform, affects where CuraEngine is looked up |
| `baud_rate` | integer | Serial baud rate (default `115200`) |
| `bed` | `{"x": {"min", "max"}, "y": …, "z": …}` | Where the left nozzle may move, in mm relative to the zero point (default X/Y −30…30, Z 0…60). Jogs (once zeroed) and prints that would leave it are refused. |
| `syringe_travel_mm` | number | Plunger travel of a full syringe (default `40`). A print that needs more is refused; a warning appears when less than 10% is left. |
| `nozzle_offset_measured` | `true` / `false` | Set to `true` once `NOZZLE_OFFSET_X` in `backend/backend/gcode_processor.py` has been measured. Right-nozzle and dual prints are refused until then. |
| `temperature.sensors` | `{"T0": {"name", "min", "max"}, …}` | A readable name per sensor key the printer reports, and the targets it accepts (besides 0 for off). Unlisted sensors show under their raw key and accept 0–120 °C. |
| `temperature.retention_days` | number | Logged readings older than this are deleted at startup (default `30`) |
| `temperature.target_band_c` | number | A sensor counts as at its target within ± this (default `1`) |
| `temperature.settle_s` | number | How long every target has to hold before a waiting print starts (default `30`) |
| `temperature.deviation_c`, `temperature.deviation_s` | numbers | Warn during a print when a sensor is further than `deviation_c` from its target for longer than `deviation_s` (defaults `3` °C, `60` s) |
| `temperature.report_timeout_s` | number | Warn when no temperature report came for this long while connected (default `15`) |

Slicer settings live in `context/octaris_settings.json` (CuraEngine profile). Printer geometry is in `context/fdmprinter.def.json`.
