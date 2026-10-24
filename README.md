# Octaris

Control software for the Printess bioprinter. Upload an STL or a pre-sliced G-code file, choose the syringes to print with, and manage the full print workflow: slicing, a 3D preview, calibration, printing with pause, stop and resume, and temperature monitoring.


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

2. **Select the syringes**: the *Printer* dropdown shows the printer profile (*Printess 1.0*, *Printess 2.0*, any added in `config.json`, and *Custom* once one is saved; see [Printer profiles](#printer-profiles)), and the card draws its syringes, left to right. Tap a syringe to select it, tap it again to deselect it: any combination, at least one. The selected syringes move together on one carriage, so each prints its own copy of the same path, as far over as it sits on the head (its offset from the first selected syringe); there are no multi-material prints. The selection decides which plunger and height axes receive the print's moves.

   **Every copy has to stay on the bed.** With several syringes selected, each one's copy of the path is checked against the bed limits (the first's path shifted by the syringe's offset minus the first selected syringe's), on upload, at print start (from the position the printer reports), after an edit in the G-code editor and on *Restart*. A copy that would leave the bed is refused, naming the syringe and the line, for example *The right syringe's copy leaves the bed: line 12 moves it to X 52 mm (bed −45 to 45 mm).* Jogs and *Go to Origin* are checked the same way for every selected syringe, once zeroed. With one syringe nothing changes.

   **Set up the syringes** (a printer with other axes or more syringes than a built-in profile): click **Edit setup**. Under each syringe, type its name, its height axis (*H*) and plunger axis (*P*), and its offset from the first syringe in mm (*X*, *Y*). **+ Add syringe** adds one on the right (up to 6, as long as axis letters are left), **Remove** takes one away (at least 1 stays). Tick **Offsets measured** once the offsets are measured: prints with several syringes are refused until then. Below the syringes, **Bed limits (mm)** sets where the nozzles may go, X, Y and Z min to max, relative to where you zero (e.g. −45 to 45 for a 90 mm dish zeroed in the centre; every syringe's height has the Z range); each min has to be below its max. Jogs (once zeroed), *Go to Origin* and prints that would leave the bed are refused. **Steps per mm** is one value for every axis, X and Y included (above 0), sent to the printer as `M92` on connect and on saving. Each axis is one of Z, A, B, C, I, J, K, U, V, W, D (not X/Y, the stage, nor E), used once in the setup; a field that breaks this turns red and says why, and **Save** stays off until every field is valid. Saving stores the setup, bed limits and steps per mm included, as the *Custom* profile (editing a built-in profile copies it, with its bed, steps per mm and build volume, into *Custom*; the built-in profile keeps its own bed from `config.json`) and selects it at once, kept across restarts: no `config.json` change or restart needed. **Reset to Printess 1.0** deletes *Custom* and goes back to the Printess 1.0. Saving, resetting or switching the printer resets the calibration and the resume checkpoint and unloads the loaded print, and isn't possible while a print is running or paused.

3. **Calibrate (zero the nozzles)** with the jog panel, which has X/Y and a height column that moves the height axes chosen with its chips: by default those of the selected syringes (Z for the left, A for the right on the Printess 1.0; see *Jog panel* under [Manual control](#manual-control-take-over-screen)). X/Y are set at the first (leftmost) selected nozzle over the print's start point; each nozzle's height is set with that nozzle lowered onto the bed. One step per selected nozzle, left to right:
   - The first: jog it over the start point, lower it onto the bed, click **Zero <nozzle>** (`G92 X0 Y0 <height>0 <plunger>0`).
   - Each further one: lower it onto the bed and click **Zero <nozzle>** (`G92 <height>0 <plunger>0`): its height only, the start point stays where the first set it.

   On the Printess 1.0: *Left* alone sends `G92 X0 Y0 Z0 B0`; *Right* alone `G92 X0 Y0 A0 C0` (the right nozzle over the start point); *Left + Right* `G92 X0 Y0 Z0 B0`, then `G92 A0 C0`. On the Printess 2.0, all four: `G92 X0 Y0 Z0 U0`, `G92 A0 V0`, `G92 B0 W0`, `G92 C0 D0`. Prints aren't shifted by the nozzle offsets.

   **Lower to zero height** (next to *Go to Origin*) lowers the selected syringes' height axes to the zero set here, onto the bed (after *Go to Origin*'s X0 Y0). It stays off, saying *Zero the selected syringes first: jog each one onto the bed and press Zero*, until every selected syringe is zeroed: before that its 0 is wherever the printer powered up.

   Connecting, disconnecting, losing the USB link or switching the printer profile resets the calibration.

4. **Upload a file** (two modes are available via the toggle below the syringe selector):
   - **STL File**: upload a `.stl` model, then click *Click to Slice*. The backend runs CuraEngine (sliced for one extruder; every selected plunger is driven with it) and post-processes the G-code (extrusion substitution, feed-rate clamping, travel retraction). Requires UltiMaker Cura to be installed. With several syringes selected, the model is checked before slicing: its footprint plus the spread of the syringes' offsets has to fit the bed in X and in Y, or it is refused at once (*The model doesn't fit on the bed with the left and right syringes: it is 40.0 mm across in X and the syringes are 30 mm apart, 70.0 mm in all, but the bed is 60 mm across in X (−30 to 30 mm). Select fewer syringes or use a smaller model.*). Where the copies end up is checked after slicing.
   - **G-Code File**: upload a pre-sliced `.gcode` file. Processing (extrusion substitution and validation) happens automatically on upload. A file with tool changes (more than one of `T0`, `T1`, …: a multi-material file) is refused when it *Needs changes* (*Line 4: T1 changes tool (the file starts with T0 on line 1). Multi-material files aren't supported: every selected syringe prints the same path. Slice the model for one extruder.*); sent as uploaded it only gets a warning, and the `T` lines go out unchanged. A lone `T0`, which Cura writes for one extruder, is no tool change.

5. **Review the preview**: after slicing or upload you'll see the total line count and estimated print time, and either the first 40 lines or a **3D view** of the toolpath: each nozzle in its own colour, a slider to show the layers up to a given one, and toggles for *This layer only* and *Travel* moves. Drag to orbit, scroll to zoom.

6. **Proceed to Preview**: click the button to move to the print screen.

### Print screen

- The circular progress indicator shows percentage complete.
- **Pause / Resume / Stop / Restart** buttons control the print queue.
- The **flow rate slider** (50–150%) adjusts extrusion speed live.
- The status bar shows current line number and system state.

**Pause and resume.** You can jog or send commands while paused; on resume the head goes back to where it paused and the print continues.

**Stop and resume.** *Stop* sends `M410`, which halts the motors at once, then reads the position back (`M114`) to find the line the printer stopped on. If that works, the stop dialog offers **Resume**: the head returns to the stop point and the print continues from the rest of that line. A stop can't be resumed (the dialog says why) if the USB link dropped, the printer was reconnected, or something changed the coordinates or plungers in the meantime (`G92`, a manual plunger move). *Restart* starts the print from the beginning.

### Relative printing

Every print is sent fully relative: the post-processor turns the sliced program into `G91` moves behind a single `G91` and drops every `G90`/`G92`, and jogs, stops and resumes keep the printer in `G91`. A print therefore starts wherever the nozzle was zeroed. Pre-sliced files that are already relative (lab `G91` files) are sent as they are.

### Temperatures

> Temperature monitoring is new since v1.0.0; v1.0 has none.

Temperature control is **off by default**, for printers without temperature
sensors. Turn it on in Setup under *Temperature control* with **This printer
has temperature sensors** (kept across restarts, applied at once: no reconnect
needed). While it's off, the app never sends `M155` or `M105` by itself, the
Temperature screen is disabled, and nothing temperature-related is shown,
logged or warned about; the `M105`/`M104` quick commands in Take Over still
work. **Test sensors** next to the switch sends `M105` once (5 s to answer) and
shows the raw reply and the sensors it found, or *No reply*, whether the
setting is on or off: use it to check the setup.

Under the switch, **Protocol** chooses how heater targets are sent: **Marlin
(M104/M140)** or **Printess lab firmware (M104 I<n> T<°C> H/C)** (see *Lab
firmware* below). It is kept in `settings.json` and applies at once, like the
switch: no restart or reconnect. Until one is chosen here,
`temperature.protocol` in `config.json` is used. Switching forgets the readings
and targets remembered for the old protocol (the logged readings stay in the
database), and isn't possible while a print is waiting for its temperatures.
The channel names and the off command still come from `config.json`.

The **Temperature** screen (sidebar) shows each sensor's reading, target and status, a chart of actual vs. target, and a CSV export. Warnings appear when a sensor drifts from its target during a print, stops reporting, or reads something implausible (see the safety note above).

The backend reads the printer's temperatures (Marlin's `M155` auto-report, or
`M105` polling if the firmware doesn't auto-report) and logs every reading to
the SQLite database, tagged with the print it belongs to. Polling never holds
up other commands: a poll is skipped while a command is queued, has 5 s to be
answered, and gives way after 0.5 s to a command that arrives. If two polls in
a row get no usable reply, polling pauses, and Setup and Temperature show *The
printer doesn't report temperatures* with a **Retry** button.

- `GET /settings`, `PUT /settings` `{"temperature_sensors": true}`,
  `{"temperature_protocol": "printess"}`: the settings changed in the app, saved
  to `settings.json` in the app data folder. Only the settings given change.
  `temperature_protocol` is `null` until one is chosen (then `config.json`'s
  applies); changing it is refused (409) while a print waits for temperatures.
- `POST /temperature/test`: one `M105`; `{"reply": "ok T:…" | null, "sensors": […]}`.
- `POST /temperature/retry`: start polling again after it paused.

- `GET /temperature`: the latest reading, target and status (heating, cooling,
  at target, off) of each sensor. If the printer never reports a temperature
  after connecting, `state` is `no_sensors`.
- `POST /temperature/target` `{"sensor": "T0", "target": 37}`: sets a target with
  `M104` (T, T0, T1…), `M140` (bed) or `M141` (chamber); `0` turns the heater off.
  With the lab firmware it's a channel, `{"sensor": "I0", …}` (see below).
  Targets outside the sensor's range are refused. This works during a print,
  since it doesn't move anything. `M109`/`M190`, which block the printer until
  the temperature is reached, are never sent.
- `GET /temperature/history?minutes=30` or `?session_id=…`: readings for a chart.
- `GET /temperature/export.csv?session_id=…` or `?from=…&to=…` (ISO 8601): CSV
  download (timestamp, sensor, name, actual, target).
- `POST /print/start` `{"wait_for_temperature": true}`: the print holds its first
  line until every sensor with a target has stayed within ±1 °C of it for 30 s.
  Stopping the print cancels the wait.
  Refused while temperature control is off.

#### Lab firmware: heater channels (`"protocol": "printess"`)

The lab's own firmware addresses its heaters differently from Marlin. With
the protocol set to *Printess lab firmware* on Setup (or, until one is chosen
there, `temperature.protocol` set to `"printess"` in `config.json`; default
`"marlin"`), Octaris drives its four heater channels `I0`–`I3`:

- A target goes out as `M104 I<n> T<°C> H` to heat or `M104 I<n> T<°C> C` to
  cool. Octaris picks **H** when the new target is at or above the channel's
  current reading and **C** when it is below. Without a reading from that
  channel the target is refused (*No reading from … yet*), since the direction
  can't be chosen. A channel only moves the way it was told, so the Temperature
  screen shows *Heating* or *Cooling* (*heats only* / *cools only*) for as long
  as it is away from its target.
- Each channel's reading comes from a key of the `M105` reply, set per channel
  in `temperature.channels` (`reply_key`, e.g. `T0` → `I0`). The reply is read
  with the same parser as Marlin's, so `reply_key` has to be one of Marlin's
  sensor keys (`T`, `T0`, `T1`…, `B`, `C`, `P`, `R`, `W`); keys no channel reads
  from show as read-only sensors. If the reply carries no `/target`, Octaris
  shows the target it last sent (forgotten on reconnect).
- **Off** sends `temperature.off_command`, default `M104 I{n} T0`.
- The Temperature screen, Setup's **Test sensors** and Take Over's **Set Temp**
  (channel picker; H/C chosen as above) all use the channels by name, and
  *Wait for temperature* waits for every channel with a target. A channel
  target typed by hand in Take Over (`M104 I1 T30 C`) counts as set too.

> **Not yet confirmed by the lab.** Two parts are guesses until the lab answers:
> 1. **The `M105` reply format.** Octaris assumes Marlin-style keys, one per
>    channel (`T0:21.3 T1:21.1 T2:20.8 T3:21.6`, with or without `/target`), and
>    maps them with `reply_key`. If the firmware uses other keys (e.g. `I0:`),
>    the reply parser in `serial_manager.py` has to learn them.
> 2. **How a channel is switched off.** `off_command` (`M104 I{n} T0`) is a
>    guess; change it in `config.json` once the lab says.
>
> The virtual printer's printess mode answers in the assumed format, so it
> can't tell whether the guesses are right.

### Manual control (Take Over screen)

Accessible from the sidebar during a print or when idle:

- **Serial log** — scrollable view of every command sent to and received from the printer.
- **G-code input** — type any G-code command and send it directly.
- **Quick commands** — buttons for common operations (Home, position query, settings, etc.).
- **Jog panel** (*Manual Navigation* on Setup) — move the stage, the nozzle heights and the plungers by a step (0.1, 1 or 5 mm, or a custom one from 0.01 to 200 mm). Two feed rates in mm/min, 1 to 3000, both 300 by default: **XY feed** for the X/Y pad and **Z / extruder feed** for the height and plunger columns; *Go to Origin* uses the XY feed for X/Y and the Z feed for lowering. Next to the X/Y pad is a height column (up/down), with a row of chips above it, one per syringe of the printer profile, labelled with its height axis and name (*Z left*, *A right*). Tap the chips to choose which height axes the buttons move: one, several or all (at least one stays chosen). One press moves every chosen axis together, in one block (`G1 Z1 A1 F300`); once zeroed, each axis is checked against the bed limits (X/Y for every syringe selected for printing, at its offset), and if one would leave the bed nothing moves. Next to it is a plunger column with its own chips, labelled with each syringe's plunger axis and name (*B left*, *C right*): **Retract** (up) moves the chosen plungers in the positive direction, **Extrude** (down) in the negative one, all in one block (`G1 U-1 V-1 F300`). A plunger jog makes a stopped print unresumable, as any manual plunger move does. Until a chip is tapped, each row of chips follows the syringes selected for printing; after that its choice is kept for the session (like the step and the feed rates). `POST /jog` takes `{"axes": ["Z", "A"], "distance": 1, "feed_rate": 300}` (the axes X, Y and the profile's heights and plungers; `distance` at most 200 mm either way, `feed_rate` 1 to 3000 mm/min, default 300), or one `"axis"` as before.
- **Go to Origin** — returns the stage to X0 Y0, refused if that puts a selected syringe off the bed. *Lower to zero height* (Setup) then lowers the selected syringes' height axes to the zero set during calibration. `POST /jog/origin` takes `{"lower": true, "xy_feed": 300, "z_feed": 300}` (mm/min, 1 to 3000, default 300).
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

Start the backend with `OCTARIS_VIRTUAL_PRINTER=1` and the port selector offers a **Virtual printer**: a simulated Marlin board (`backend/backend/virtual_printer.py`) with a 16-move planner, real move timing, temperatures, resends and M410. The whole app can be used against it. `OCTARIS_VIRTUAL_PRINTER_SPEED=20` runs its moves 20× faster than real time. It has the selected printer profile's axes: the `M92` sent on connect and on a change of profile names them, and its `M114` reports them all. With the *Printess lab firmware* protocol chosen on Setup (or `"protocol": "printess"` in `config.json`) when it connects, it simulates the lab firmware's four heater channels instead of Marlin's heaters (it keeps the protocol it connected with until it is reconnected): `M104 I<n> T<°C> H` only heats, `C` only cools, `M104 I<n> T0` switches off, and `M105` answers `ok T0:21.30 T1:21.10 T2:20.80 T3:21.60`.

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

`config.json` at the repository root controls runtime behaviour. In the installed app it is inside the app bundle and read-only, so the settings the lab needs to change are also set in the app, kept in `settings.json` in the app data folder (see [Logs](#logs) for where), and win over `config.json` from then on:

- the printer profile (*Printer* dropdown on Setup) instead of `printer_profile`;
- the Custom profile's syringes, offsets, **bed limits** and **steps per mm** (*Edit setup* on Setup) instead of a profile's `nozzles`, `nozzle_offset_measured`, `bed` and `steps_per_mm` under `profiles`;
- whether the printer has temperature sensors (*Temperature control* on Setup);
- the **temperature protocol** (*Protocol* on Setup) instead of `temperature.protocol`.

Everything else, such as the channel names, is only in `config.json`:

| Key | Values | Description |
|-----|--------|-------------|
| `target` | `macos` / `windows` / `rpi` | Platform, affects where CuraEngine is looked up. Defaults to the OS it runs on (Linux → `rpi`); set it only to override that. |
| `baud_rate` | integer | Serial baud rate (default `115200`) |
| `printer_profile` | profile id | The printer, from `profiles` (default `printess-1`), until one is chosen with the *Printer* dropdown on Setup: that choice is kept in `settings.json` and wins from then on. |
| `profiles` | `{"<id>": {…}}` | Overrides of the built-in profiles and added ones, see [Printer profiles](#printer-profiles). In the app, the Custom profile (*Edit setup*) has its own syringes and bed limits. |
| `syringe_travel_mm` | number | Plunger travel of a full syringe (default `40`). A print that needs more is refused; a warning appears when less than 10% is left. |
| `temperature.protocol` | `marlin` / `printess` | How heaters are set: Marlin's `M104`/`M140`/`M141` (default), or the lab firmware's channels, `M104 I<n> T<°C> H\|C` (see *Lab firmware* above). The default until a protocol is chosen on Setup, which is kept in `settings.json` and wins from then on. |
| `temperature.channels` | `[{"index", "name", "reply_key", "min", "max"}, …]` | `printess` only: each heater channel `I<index>`, its name, the `M105` reply key it reads from (**unconfirmed**), and the targets it accepts (besides 0 for off). Default: `I0`–`I3` reading `T0`–`T3`, 0–120 °C |
| `temperature.off_command` | string | `printess` only: the command that switches channel `{n}` off. Default `M104 I{n} T0` (**unconfirmed**) |
| `temperature.sensors` | `{"T0": {"name", "min", "max"}, …}` | A readable name per sensor key the printer reports, and the targets it accepts (besides 0 for off). Unlisted sensors show under their raw key and accept 0–120 °C. |
| `temperature.retention_days` | number | Logged readings older than this are deleted at startup (default `30`) |
| `temperature.target_band_c` | number | A sensor counts as at its target within ± this (default `1`) |
| `temperature.settle_s` | number | How long every target has to hold before a waiting print starts (default `30`) |
| `temperature.deviation_c`, `temperature.deviation_s` | numbers | Warn during a print when a sensor is further than `deviation_c` from its target for longer than `deviation_s` (defaults `3` °C, `60` s) |
| `temperature.report_timeout_s` | number | Warn when no temperature report came for this long while connected (default `15`) |

Slicer settings live in `context/octaris_settings.json` (CuraEngine profile). Cura's machine size (`machine_width`, `machine_depth`, `machine_height`) comes from the printer profile's `build_volume`.

### Printer profiles

A profile describes the printer: its nozzles left to right, each with its own height motor and plunger, where they may move and the slicer's build volume. The built-in profiles are in `backend/backend/printer.py`. The *Printess 1.0* (`printess-1`) has two nozzles: *left* (height Z, plunger B) and *right* (height A, plunger C, 30 mm in +X from the left), 800 steps/mm. The *Printess 2.0* (`printess-2`) has four, *Syringe 1* to *Syringe 4*, left to right: height Z / plunger U, A / V, B / W and C / D, at X offsets 0, 30, 60 and 90 mm (Y 0, measured), with 3200 steps/mm on every axis, X and Y included, a bed of X −100…100, Y −100…100 and Z 0…60 mm, and a 200 × 200 × 60 mm build volume for Cura. `GET /printer` returns the selected profile, `GET /printer/profiles` all of them, and `PUT /printer {"profile": "<id>"}` switches (which resets the calibration and any resume checkpoint, unloads the loaded print and sends a connected printer the profile's `M92`). The choice is kept in `settings.json`. The client also gets the profile in the snapshot on connect.

Nothing in Octaris knows axis letters other than through the selected profile: processing (Cura's Z becomes every selected nozzle's height axis, E each selected plunger, negated), calibration, jogs, *Go to Origin* / *Lower to zero height*, the bed limits (every height axis has the `z` range), the plunger travel check, the position read with `M114`, e-stop resume, the virtual printer, the 3D preview and the jog panel all take them from it. On connect, `M92` sets X, Y and every axis of the profile to its `steps_per_mm`.

The *Custom* profile (`custom`) is the one edited on Setup (see [Setup screen](#setup-screen)). It is saved in `settings.json` in the app data folder, under `"printer"`, not in `config.json`, and is listed after the others once saved. `GET /printer/custom` returns it (`null` until saved), `PUT /printer/custom {"nozzles": [...], "nozzle_offset_measured": bool, "base": "<id>", "bed": {"x": {"min", "max"}, "y": …, "z": …}, "steps_per_mm": 3200}` saves and selects it (400 with every problem, one per line, for an invalid setup, e.g. *Bed X: min (45.0) must be below max (-45.0)* or *Steps per mm: Input should be greater than 0*; 409 during a print), and sends a connected printer its `M92`, and `DELETE /printer/custom` deletes it and selects `printess-1`. The build volume (and the bed and steps/mm, if `bed` or `steps_per_mm` is left out) come from `base`, the profile being edited (default: the Custom profile so far). Other windows follow along through the `printer_profile` and `printer_profiles` events.

| Key | Values | Description |
|-----|--------|-------------|
| `name` | text | Shown in the *Printer* dropdown |
| `nozzles` | list of `{"id", "name", "height_axis", "plunger_axis", "offset_x", "offset_y"}` | Left to right, 1 to 6. `offset_x`/`offset_y`: mm from the first nozzle. Each axis is one of Z, A, B, C, I, J, K, U, V, W (Marlin's axis names but X/Y/E) or D (the Printess 2.0's fourth plunger), used once in the profile, and a nozzle's height and plunger axis differ. Eleven letters make 6 nozzles impossible in practice: 5 is the most. |
| `bed` | `{"x": {"min", "max"}, "y": …, "z": …}` | Where the nozzles may move, in mm relative to the zero point (default X/Y −30…30, Z 0…60; every nozzle's height has the `z` range). Jogs (once zeroed), *Go to Origin* and prints that would leave it are refused, for every selected nozzle at its offset. The Custom profile's is set in the app (*Edit setup*). |
| `build_volume` | `{"x", "y", "z"}` | Cura's machine size in mm (default 60 × 60 × 60). Larger models are refused before slicing. |
| `nozzle_offset_measured` | `true` / `false` | Set to `true` once the nozzles' `offset_x`/`offset_y` have been measured. Prints with more than one nozzle are refused until then. |
| `steps_per_mm` | number | Steps/mm of every axis, X and Y included, sent as `M92` on connect and when the profile is selected (default `800`, the Printess 2.0's `3200`; EEPROM is disabled on the board). The Custom profile's is set in the app (*Edit setup*). |

Under `profiles`, a built-in profile's id overrides only the keys given (nested objects key by key; `nozzles` is replaced as a whole), and any other id adds a profile, which needs `name` and `nozzles` (`custom` is kept for the app's Custom profile). For example, a 90 mm dish on the Printess 1.0:

```json
"profiles": {
  "printess-1": {
    "bed": { "x": { "min": -45, "max": 45 }, "y": { "min": -45, "max": 45 } },
    "nozzle_offset_measured": true
  }
}
```

A `config.json` that still has the old top-level `bed` and `nozzle_offset_measured` keys applies them to the selected profile.
