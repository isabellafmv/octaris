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

### Manual control (Take Over screen)

Accessible from the sidebar during a print or when idle:

- **Serial log** — scrollable view of every command sent to and received from the printer.
- **G-code input** — type any G-code command and send it directly.
- **Quick commands** — buttons for common operations (Home, position query, settings, etc.).
- **Jog panel** — move individual axes by fixed increments (0.1, 1, or 5 mm).
- **Go to Origin** — returns the stage to X0 Y0.
- **Emergency STOP** — sends `M410` and flushes the queue immediately.

---

## Development Setup

If you want to run from source instead of the packaged app:

### 1. Backend

```bash
cd backend
pip install -e .
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
| `touch` | boolean | Enable touch-optimised UI layout |

Slicer settings live in `context/octaris_settings.json` (CuraEngine profile). Printer geometry is in `context/fdmprinter.def.json`.
