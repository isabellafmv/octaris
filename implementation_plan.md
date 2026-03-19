# Octaris Bioprinter — Implementation Plan

**Based on:** `context/OctarisBioprinter-CustomControlSoftware.md` v0.1
**Current state:** Core scaffolding complete (FastAPI backend, React/Electron frontend, two-screen flow)
**Last updated:** March 2026

---

## What's Already Built

The following foundation is in place and working:

- **Backend:** FastAPI app with lifespan management, config loading, event bus, serial manager, queue worker, database init
- **Serial layer:** `SerialManager` with connect/disconnect, port listing (macOS/Linux prioritisation), reconnection logic, `send_line` with OK/error parsing
- **Queue worker:** Two-lane queue (normal + priority), print status tracking (idle/printing/paused/stopped/completed)
- **G-code processor:** Header/footer stripping, E→B/C axis substitution, feed rate clamping, time metadata extraction
- **Slicer:** CuraEngine CLI wrapper triggered by STL upload
- **REST API:** All endpoints (`/ports`, `/connect`, `/disconnect`, `/upload`, `/print/*`, `/extrusion`, `/jog`)
- **WebSocket:** Real-time event streaming via `/ws`
- **Frontend:** Two-screen flow (Setup → Print), StatusBar, PortSelector, SyringeSelector, JogPanel, STLUpload, StartPrintButton, ProgressBar, TimeRemaining, ExtrusionSlider, PrintOverlay
- **Electron shell:** Main process, preload bridge, Vite build config

---

## Implementation Steps

Each step is self-contained and builds on the previous. Steps can be implemented sequentially — each leaves the app in a working state.

---

### Step 1 — Backend: Serial communication logging & raw G-code endpoint

**Goal:** Add the backend infrastructure needed for the Take Over screen — a serial log buffer and a raw G-code send endpoint.

**Files to create/modify:**
- `backend/backend/serial_manager.py` — Add a ring buffer (last 500 lines) that captures every line sent and every response received, with timestamps
- `backend/backend/routers/gcode.py` *(new)* — `POST /gcode/send` endpoint: accepts `{ "line": "G28 X0" }`, sends via priority lane, returns the printer's response
- `backend/backend/routers/gcode.py` — `GET /gcode/log` endpoint: returns the last N entries from the serial ring buffer
- `backend/backend/main.py` — Register the new router

**Schema for log entries:**
```python
@dataclass
class SerialLogEntry:
    timestamp: str       # ISO 8601
    direction: str       # "sent" | "received"
    content: str         # the raw line
```

**WebSocket addition:**
- Emit `{ "type": "serial_log", "entry": { ... } }` events so the Take Over screen can stream logs in real time without polling

**Acceptance criteria:**
- [ ] `POST /gcode/send` sends arbitrary G-code and returns the response
- [ ] `GET /gcode/log?limit=100` returns recent serial traffic
- [ ] WebSocket emits `serial_log` events for every sent/received line
- [ ] Ring buffer is capped at 500 entries to avoid memory growth

---

### Step 2 — Frontend: Take Over screen (UI shell & navigation)

**Goal:** Create Screen 3 — "Take Over" — accessible from both Setup and Print screens.

**Files to create/modify:**
- `client/src/renderer/src/screens/TakeOverScreen.tsx` *(new)* — Main screen component
- `client/src/renderer/src/App.tsx` — Add `'takeover'` to the `Screen` type union, add navigation handlers, render TakeOverScreen
- `client/src/renderer/src/components/StatusBar.tsx` — Add a "Take Over" button (wrench/terminal icon) visible when the printer is connected
- `client/src/renderer/src/types.ts` — Add `SerialLogEntry` type, add `'serial_log'` to `WsEvent` type

**Layout:**
```
┌────────────────────────────────────────────┐
│  ● CONNECTED   /dev/tty.usbmodem14201     │  ← status bar (with Back button)
├───────────────────────┬────────────────────┤
│                       │                    │
│   Serial Log          │  Send G-code       │
│   (scrollable)        │                    │
│                       │  ┌──────────────┐  │
│   > G28 X0            │  │ G-code input │  │
│   < ok                │  └──────────────┘  │
│   > G1 X10 F200       │  [ Send ]          │
│   < ok                │                    │
│   > M114              │  Quick commands:   │
│   < X:10.0 Y:0.0 ...  │  [Home] [M114]    │
│                       │  [M410] [M503]    │
│                       │                    │
├───────────────────────┴────────────────────┤
│   [  ← Back  ]                             │
└────────────────────────────────────────────┘
```

**Acceptance criteria:**
- [ ] TakeOverScreen renders with two-panel layout (log + command input)
- [ ] Navigable via StatusBar button from both Setup and Print screens
- [ ] "Back" returns to the previous screen
- [ ] Screen is functional on touch (48px minimum tap targets)

---

### Step 3 — Frontend: Serial log panel (live streaming)

**Goal:** Wire up the serial log panel to the WebSocket stream.

**Files to modify:**
- `client/src/renderer/src/hooks/useWebSocket.ts` — Handle `serial_log` events, maintain a buffer of the last 200 entries in state
- `client/src/renderer/src/screens/TakeOverScreen.tsx` — Render the log panel with auto-scroll to bottom
- `client/src/renderer/src/components/SerialLog.tsx` *(new)* — Reusable scrollable log component

**Behaviour:**
- Sent lines shown in one colour (e.g. `text-blue-400`), received lines in another (`text-green-400`)
- Timestamps shown in a muted colour on the left
- Auto-scrolls to bottom on new entries, unless the user has manually scrolled up
- "Clear" button to reset the visible log (does not affect the backend buffer)
- Monospace font for the log area

**Acceptance criteria:**
- [ ] Log updates in real time as commands are sent/received
- [ ] Scroll-lock behaviour works (auto-scroll unless user scrolls up)
- [ ] Sent vs received lines are visually distinct
- [ ] Works on touch (scrollable via swipe)

---

### Step 4 — Frontend: G-code command input & quick commands

**Goal:** Allow the user to type and send arbitrary G-code, plus provide quick-access buttons for common commands.

**Files to create/modify:**
- `client/src/renderer/src/components/GcodeInput.tsx` *(new)* — Text input + Send button + command history
- `client/src/renderer/src/screens/TakeOverScreen.tsx` — Integrate GcodeInput and quick command buttons
- `client/src/renderer/src/api.ts` — Add `sendGcode(line: string)` method

**Behaviour:**
- Text input field with "Send" button (also submits on Enter key)
- Command history: Up/Down arrow keys cycle through previously sent commands (last 50, in-memory)
- Quick command buttons for the most useful commands:
  - **Home All** → `G28`
  - **Home XY** → `G28 X0 Y0`
  - **Position** → `M114` (report current position)
  - **STOP** → `M410` (emergency stop — styled in red)
  - **Settings** → `M503` (report firmware settings)
  - **Relative** → `G91`
  - **Absolute** → `G90`
- Input is disabled when the printer is not connected
- Response from `POST /gcode/send` is displayed inline (toast or highlighted in the log)

**Acceptance criteria:**
- [ ] User can type G-code, send it, and see the response in the log
- [ ] Quick command buttons send the correct G-code
- [ ] Command history works with Up/Down arrows
- [ ] M410 button is prominently styled as a danger action
- [ ] Input disabled when disconnected

---

### Step 5 — Backend: Fetch log history on screen entry

**Goal:** When the Take Over screen is opened mid-print, it should immediately show recent serial history (not start from a blank log).

**Files to modify:**
- `client/src/renderer/src/screens/TakeOverScreen.tsx` — On mount, call `GET /gcode/log?limit=200` and prepopulate the log
- `client/src/renderer/src/api.ts` — Add `getSerialLog(limit: number)` method

**Acceptance criteria:**
- [ ] Opening Take Over screen mid-print shows the recent serial history
- [ ] Log seamlessly transitions from historical entries to live WebSocket stream (no duplicates)

---

### Step 6 — Backend: G-code post-processor validation hardening

**Goal:** Ensure the G-code post-processor (Section 5 of the spec) is fully robust.

**Files to modify:**
- `backend/backend/gcode_processor.py` — Review and harden:
  - Confirm header stripping removes everything before the first `G0` (inclusive of that line or exclusive — verify against spec: "retain from that line onwards" = inclusive)
  - Confirm footer stripping uses exact multi-line match from the spec
  - Confirm E→B/C substitution handles negative values, decimal values, and scientific notation edge cases
  - Confirm F clamping logs to a sidecar `.log` file (not just in-memory)
  - Confirm G92 zeroing line matches syringe config exactly
  - Add Step 8 validation: assert file starts with G92+G91, no remaining E commands, no F>400

**Files to create:**
- `backend/tests/test_gcode_processor_validation.py` — Comprehensive test suite covering:
  - Left/right/both syringe configs
  - Edge cases: empty file, file with no G0, file with F values at exactly 400
  - Validation pass/fail scenarios

**Acceptance criteria:**
- [ ] All 8 pipeline steps from the spec are implemented and covered by tests
- [ ] Sidecar `.log` file is written with clamped F-rate entries
- [ ] Validation step rejects invalid G-code with clear error messages

---

### Step 7 — Backend: SQLite data logging

**Goal:** Implement the data logging schema from Section 10 of the spec.

**Files to modify:**
- `backend/backend/database.py` — Create `sessions` and `extrusion_events` tables per the spec schema
- `backend/backend/queue_worker.py` — On print start: insert a `sessions` row. On print end/stop: update `ended_at` and `completed`. On extrusion change: insert into `extrusion_events`
- `backend/backend/routers/print_control.py` — Pass session context through to the queue worker

**Schema (from spec):**

**`sessions`**: id, started_at, ended_at, filename, syringe_config, total_lines, completed
**`extrusion_events`**: id, session_id, timestamp, extrusion_rate, lines_sent

**Acceptance criteria:**
- [ ] A new session row is created on every `POST /print/start`
- [ ] Session is updated on stop/complete with correct `ended_at` and `completed` flag
- [ ] Every `POST /extrusion` during a print creates an `extrusion_events` row
- [ ] Database file is created at `octaris_log.db`

---

### Step 8 — Serial layer: disconnection resilience & reconnection

**Goal:** Harden the serial layer for real-world use (cable pulls, USB disconnects).

**Files to modify:**
- `backend/backend/serial_manager.py` — Ensure `_handle_disconnect` triggers auto-reconnect (3 retries, 1s apart) before emitting `disconnected` event
- `backend/backend/queue_worker.py` — On disconnect during printing: pause the queue, wait for reconnect result, then either resume or emit stopped
- `backend/backend/events.py` — Emit `{ "type": "disconnected" }` and `{ "type": "reconnecting", "attempt": N }` events

**Frontend changes:**
- `client/src/renderer/src/hooks/useWebSocket.ts` — Handle `reconnecting` event type
- `client/src/renderer/src/components/StatusBar.tsx` — Show "Reconnecting..." state with attempt count
- Error overlay on Print screen: "Printer disconnected. Check the cable. You can try to resume."

**Acceptance criteria:**
- [ ] Pulling the USB cable triggers reconnection attempts (not a crash)
- [ ] After 3 failed reconnect attempts, UI shows disconnected state with clear message
- [ ] If reconnect succeeds, printing can resume from where it left off
- [ ] StatusBar reflects connection state changes in real time

---

### Step 9 — Frontend: UX polish & touch mode

**Goal:** Meet all UX constraints from Section 9 of the spec.

**Files to modify:**
- `client/src/renderer/src/App.tsx` — Load `config.json` on startup, provide touch mode context
- All components — Apply touch mode rules:
  - Minimum 48×48px tap targets when `touch_mode: true`
  - No hover-only states
  - Visible pressed/active states on all buttons
  - Font sizes: body ≥16px, buttons ≥18px, status indicators ≥20px
- Colour & contrast: verify WCAG AA compliance
- Status states: always use colour + icon + text (never colour alone)

**Files to create:**
- `client/src/renderer/src/context/ConfigContext.tsx` *(new)* — React context providing `target` and `touch_mode` to all components

**Acceptance criteria:**
- [ ] Touch mode produces correctly sized tap targets
- [ ] All status indicators use colour + icon + text
- [ ] Error messages are in plain language (per Section 9.4 table)
- [ ] No hover-only interactions anywhere in the app

---

### Step 10 — Frontend: Error handling & user-facing messages

**Goal:** Implement the error state table from Section 9.4 across the entire app.

**Error mapping:**

| Error condition | User message |
|---|---|
| Serial port not found | "Printer not found. Check the USB cable and try again." |
| Slicing failed | "Something went wrong during slicing. Try a different file." |
| Connection lost mid-print | "Printer disconnected. Check the cable. You can try to resume." |
| F-rate clamped | *(silent — logged only)* |
| G-code send failed (Take Over) | "Command failed. Check the connection." |

**Files to modify:**
- `client/src/renderer/src/components/PortSelector.tsx` — Handle connection errors
- `client/src/renderer/src/components/STLUpload.tsx` — Handle slicing errors
- `client/src/renderer/src/screens/PrintScreen.tsx` — Handle disconnect errors
- `client/src/renderer/src/screens/TakeOverScreen.tsx` — Handle send errors

**Acceptance criteria:**
- [ ] Every error shows a plain-language message (no error codes, no stack traces)
- [ ] Error messages match the spec table exactly
- [ ] Errors are dismissible and don't block the UI permanently

---

### Step 11 — Electron: Platform configuration & kiosk mode

**Goal:** Make the Electron shell respect `config.json` for RPi kiosk vs macOS desktop.

**Files to modify:**
- `client/src/main/index.ts` — Read `config.json` at startup:
  - If `target: "rpi"`: launch in `--kiosk` mode, full screen, no window chrome
  - If `target: "macos"`: standard windowed mode
- `config.json` — Document all keys: `target`, `touch_mode`, `baud_rate`

**Acceptance criteria:**
- [ ] `target: "rpi"` launches kiosk mode (fullscreen, no title bar)
- [ ] `target: "macos"` launches windowed mode
- [ ] Baud rate from config is passed to the serial layer

---

### Step 12 — End-to-end integration testing

**Goal:** Verify the complete flow works with a simulated serial device.

**Files to create:**
- `backend/tests/test_integration.py` — Full flow test:
  1. Start backend
  2. Connect to a mock serial port
  3. Upload an STL (mock CuraEngine output)
  4. Start print
  5. Verify WebSocket events stream progress
  6. Adjust extrusion mid-print
  7. Stop print
  8. Verify session logged in SQLite
- `backend/tests/mock_serial.py` *(new)* — A mock serial device that responds with `ok` to all commands, emits position reports for M114

**Take Over screen tests:**
- Send raw G-code via `/gcode/send`, verify it appears in the log
- Verify log history is retrievable via `/gcode/log`

**Acceptance criteria:**
- [ ] Full print lifecycle passes in CI without a real printer
- [ ] Take Over screen endpoints are covered
- [ ] No flaky tests (deterministic mock serial timing)

---

## Summary

| Step | Area | Description |
|------|------|-------------|
| 1 | Backend | Serial logging ring buffer + raw G-code endpoint |
| 2 | Frontend | Take Over screen shell & navigation |
| 3 | Frontend | Live serial log panel |
| 4 | Frontend | G-code input & quick commands |
| 5 | Backend + Frontend | Log history fetch on screen entry |
| 6 | Backend | G-code post-processor hardening & tests |
| 7 | Backend | SQLite session & extrusion logging |
| 8 | Backend + Frontend | Disconnection resilience & reconnection |
| 9 | Frontend | UX polish & touch mode |
| 10 | Frontend | Error handling & plain-language messages |
| 11 | Electron | Platform config & kiosk mode |
| 12 | Testing | End-to-end integration tests |

**Steps 1–5** deliver the Take Over screen feature end-to-end.
**Steps 6–8** harden the backend for production reliability.
**Steps 9–11** polish the UX and platform support.
**Step 12** locks everything down with integration tests.
