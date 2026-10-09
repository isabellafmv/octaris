# Octaris

Control software for the Printess bioprinter. Upload an STL or a pre-sliced G-code file, configure your syringe mode, and manage the full print workflow: slicing, a 3D preview, calibration, printing with pause, stop and resume, and temperature monitoring.


## Download

Grab the latest release from [Releases](../../releases): the `.dmg` for macOS (Apple Silicon), or `Octaris-<version>-win-setup.exe` for Windows (x64).

### macOS

After installing, open Terminal and run:

```bash
xattr -cr /Applications/Octaris.app
```

This removes the macOS quarantine flag so the unsigned app can launch. You only need to do this once after the initial install (automatic updates don't require it)

### Windows

Run `Octaris-<version>-win-setup.exe`. It installs for the current user (no admin rights needed) and starts Octaris.

The installer isn't code-signed yet, so Microsoft Defender SmartScreen shows *"Windows protected your PC"* the first time. Click **More info**, then **Run anyway**. Your browser may also flag the download as uncommon; choose **Keep**.

The printer shows up as a COM port. Octaris lists only ports that belong to the STM32 board (USB vendor ID `0483`, or a driver name containing "STMicroelectronics" or "STM32"). If the board isn't listed, check in Device Manager under *Ports (COM & LPT)* that Windows recognises it.

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
- macOS (Apple Silicon) or Windows (x64)


## Using the App

### Setup screen

1. **Connect the printer**: use the port selector in the top-right corner. Click *Connect*.

2. **Select syringe mode**: choose *Left*, *Right*, or *Both* (both mode is not fully functional yet) syringes. This controls which axes receive extrusion commands.

3. **Calibrate (zero the nozzles)** with the jog panel. X/Y are always set with the *left* nozzle over the print's start point; each nozzle's height (Z for the left, A for the right) is set with that nozzle lowered onto the bed. The right nozzle's X offset is applied automatically.
   - *Left*: jog the left nozzle over the start point, lower it onto the bed, click **Zero left**.
   - *Right*: jog the left nozzle over the start point, then lower the right nozzle onto the bed (A), click **Zero right**.
   - *Both*, in two steps: first lower the left nozzle onto the bed over the start point and click **Zero left**, then lower the right nozzle onto the bed and click **Zero right**.

   Connecting, disconnecting or losing the USB link resets the calibration.

4. **Upload a file** (two modes are available via the toggle below the syringe selector):
   - **STL File**: upload a `.stl` model, then click *Click to Slice*. The backend runs CuraEngine and post-processes the G-code (extrusion substitution, feed-rate clamping, travel retraction). Requires UltiMaker Cura to be installed.
   - **G-Code File**: upload a pre-sliced `.gcode` file. Processing (extrusion substitution and validation) happens automatically on upload.

5. **Review the preview**: after slicing or upload you'll see the total line count and estimated print time, and either the first 40 lines or a **3D view** of the toolpath: each nozzle in its own colour, a slider to show the layers up to a given one, and toggles for *This layer only* and *Travel* moves. Drag to orbit, scroll to zoom.

6. **Proceed to Preview**: click the button to move to the print screen.

### Print screen

- The circular progress indicator shows percentage complete.
- **Pause / Resume / Stop / Restart** buttons control the print queue.
- The **flow rate slider** (50–150%) adjusts extrusion speed live.
- The status bar shows current line number and system state.

**Pause and resume.** You can jog or send commands while paused; on resume the head goes back to where it paused and the print continues.

**Stop and resume.** *Stop* sends `M410`, which halts the motors at once, then reads the position back (`M114`) to find the line the printer stopped on. If that works, the stop dialog offers **Resume**: the head returns to the stop point and the print continues from the rest of that line. A stop can't be resumed (the dialog says why) if the USB link dropped, the printer was reconnected, or something changed the coordinates or plungers in the meantime (`G92`, a manual B/C move). *Restart* starts the print from the beginning.

### Relative printing

Every print is sent fully relative: the post-processor turns the sliced program into `G91` moves behind a single `G91` and drops every `G90`/`G92`, and jogs, stops and resumes keep the printer in `G91`. A print therefore starts wherever the nozzle was zeroed. Pre-sliced files that are already relative (lab `G91` files) are sent as they are.

### Temperatures

> Temperature monitoring is new since v1.0.0; v1.0 has none.

The **Temperature** screen (sidebar) shows each sensor's reading, target and status, a chart of actual vs. target, and a CSV export. Warnings appear when a sensor drifts from its target during a print, stops reporting, or reads something implausible (see the safety note above).

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

On Windows (PowerShell):

```powershell
cd backend
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Slicing STL files needs CuraEngine. On macOS the binary in `resources/bin/macos/` (or an installed UltiMaker Cura) is used. On Windows Octaris looks for `resources\bin\windows\CuraEngine.exe`, then the newest UltiMaker Cura install under `C:\Program Files\`, then `CuraEngine` on `PATH`. For development, installing [UltiMaker Cura](https://ultimaker.com/software/ultimaker-cura/) is enough.

### 2. Frontend

```bash
cd client
npm ci
```

`npm ci` installs exactly what `package-lock.json` pins (same on Windows). Use `npm install <package>` only to add or change a dependency.

### Running (dev mode)

Open two terminal windows.

Terminal 1 (backend, with its virtualenv activated)
```bash
cd backend
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

On Windows (PowerShell), leave out `--reload`: with it uvicorn uses an event loop that can't start subprocesses, so slicing fails.

```powershell
cd backend
.venv\Scripts\Activate.ps1
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

On Windows (PowerShell):

```powershell
$env:OCTARIS_VIRTUAL_PRINTER = "1"
uvicorn backend.main:app --host 127.0.0.1 --port 8000
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

To use exactly the versions CI uses, install from the lock file instead of `pip install -e ".[dev]"`:

```bash
pip install -r requirements-dev.txt
pip install --no-deps -e .
```

`pre-commit install` (from the repository root, with the backend's dev dependencies installed) runs all of these on every commit. CI runs them on every push and pull request, and runs mypy and the tests on Windows too. Pushing a tag builds the macOS app and the Windows installer with `build.py` and attaches the `.dmg`, `.zip` and `.exe` to the workflow run.

### Pinned dependencies

`backend/pyproject.toml` keeps flexible version ranges. `backend/requirements-dev.txt` is the lock file: exact versions of everything, dev tools included, resolved for Python 3.10+ on macOS, Windows and Linux. CI installs from it, and the client from `client/package-lock.json` with `npm ci`, so a new upstream release can't break CI overnight.

Dependabot (`.github/dependabot.yml`) opens weekly pull requests that bump pip, npm and GitHub Actions versions (minor and patch updates grouped, majors one PR each). CI checks them before merging.

To update the lock file by hand (after changing `pyproject.toml`, or to pull in new versions), use [uv](https://docs.astral.sh/uv/) (`pip install uv`):

```bash
cd backend
uv pip compile pyproject.toml --extra dev --universal --python-version 3.10 -o requirements-dev.txt                 # after editing pyproject.toml; keeps other pins
uv pip compile pyproject.toml --extra dev --universal --python-version 3.10 -o requirements-dev.txt --upgrade-package fastapi  # bump one package
uv pip compile pyproject.toml --extra dev --universal --python-version 3.10 -o requirements-dev.txt --upgrade        # bump everything
```

Then reinstall from it, run the checks, and commit the lock file with the change. Keep `ruff`'s version in step with the `ruff-pre-commit` rev in `.pre-commit-config.yaml`. For the client, `npm install <package>@<version>` updates `package-lock.json`; commit both files.

### Logs

The backend writes a rotating log to `logs/octaris-backend.log` in the app data folder: `~/Library/Application Support/Octaris/` on macOS, `%LOCALAPPDATA%\Octaris\` on Windows. The packaged app's database (`octaris_log.db`) lives there too. Each print's serial traffic goes to its own file in `logs/prints/`, named in the print's history entry (`serial_log`). Set `OCTARIS_DATA_DIR` to put them elsewhere.

### Building the desktop app

```bash
pip install pyinstaller
python build.py        # or ./build.sh on macOS
```

This builds the Python backend into a standalone folder with PyInstaller, then packages everything with electron-builder: a `.dmg` and `.zip` on macOS, an NSIS installer (`Octaris-<version>-win-setup.exe`) on Windows. Each OS builds its own app; there's no cross-building. Output is in `client/dist/`. Arguments are passed on to electron-builder (e.g. `python build.py --publish never`).

The build bundles CuraEngine from `resources/bin/<target>/` and stops with an error if it's missing:

- macOS: `resources/bin/macos/CuraEngine` and `UltiMaker-Cura` (in the repository).
- Windows: `resources/bin/windows/CuraEngine.exe` is **not** in the repository yet. Copy `CuraEngine.exe` from an UltiMaker Cura 5 install (`C:\Program Files\UltiMaker Cura 5.x.x\`) into `resources\bin\windows\`, together with any DLLs it needs from that folder (`CuraEngine.exe --help` from a plain command prompt in `resources\bin\windows\` should run). Commit it through Git LFS (already set up in `.gitattributes` for `resources/bin/**`) so the tag build on CI can bundle it; until then that job fails at this check.

---

## Configuration

`config.json` at the repository root controls runtime behaviour:

| Key | Values | Description |
|-----|--------|-------------|
| `target` | `macos` / `windows` / `rpi` | Platform, affects where CuraEngine is looked up. Defaults to the OS it runs on (Linux → `rpi`); set it only to override that. |
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
