# Octaris

Control software for the printess bioprinter. Upload an STL or a pre-sliced G-code file, configure your syringe mode, and manage the full print workflow — slicing, previewing, and live monitoring — from a single Electron app.

---

## Requirements

**Hardware**
- Octaris bioprinter connected via USB (STM32 Virtual COM Port or similar)

**Software**
- [Node.js](https://nodejs.org) 20+
- [Python](https://python.org) 3.11+
- [UltiMaker Cura](https://ultimaker.com/software/ultimaker-cura/) — required for STL slicing (the app uses CuraEngine from the Cura installation). Not required if you upload pre-sliced G-code directly.

---

## Setup

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

---

## Running

Open two terminal windows.

**Terminal 1 — backend**
```bash
cd backend
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — frontend**
```bash
cd client
npm run dev
```

The Electron app opens automatically. The backend must be running for any printer communication to work.

---

## Configuration

`config.json` at the repository root controls runtime behaviour:

| Key | Values | Description |
|-----|--------|-------------|
| `target` | `macos` / `rpi` | Platform — affects where CuraEngine is looked up |
| `baud_rate` | integer | Serial baud rate (default `250000`) |
| `touch` | boolean | Enable touch-optimised UI layout |

Slicer settings live in `context/octaris_settings.json` (CuraEngine profile). Printer geometry is in `context/fdmprinter.def.json`.

---

## Using the App

### Setup screen

1. **Connect the printer** — use the port selector in the top-right corner. Available USB ports are listed; click *Connect*.

2. **Select syringe mode** — choose *Left*, *Right*, or *Both* syringes. This controls which axes receive extrusion commands.

3. **Upload a file** — two modes are available via the toggle below the syringe selector:
   - **STL File** — upload a `.stl` model, then click *Click to Slice*. The backend runs CuraEngine and post-processes the G-code (extrusion substitution, feed-rate clamping). Requires UltiMaker Cura to be installed.
   - **G-Code File** — upload a pre-sliced `.gcode` file. Processing (extrusion substitution and validation) happens automatically on upload. No CuraEngine needed.

4. **Review the G-code preview** — after slicing or upload you'll see the first 40 lines, total line count, and estimated print time.

5. **Proceed to Preview** — click the button to move to the print screen.

### Print screen

- The circular progress indicator shows percentage complete.
- **Pause / Resume / Stop** buttons control the print queue.
- The **flow rate slider** (50–150%) sends an `M221` command to adjust extrusion speed live.
- The status bar shows current line number and system state.

### Manual control (Take Over screen)

Accessible from the sidebar during a print or when idle:

- **Serial log** — scrollable view of every command sent to and received from the printer.
- **G-code input** — type any G-code command and send it directly.
- **Quick commands** — buttons for common operations (Home, position query, settings, etc.).
- **Jog panel** — move individual axes by fixed increments.
- **Emergency STOP** — sends `M410` and flushes the queue immediately.

