# Octaris

Control software for the Printess bioprinter. Load an STL or a G-code file, choose the syringes to print with, check the 3D preview, and print, with pause, stop and resume, manual control and temperature monitoring.

## Download and install

Download the latest version from [Releases](../../releases).

**macOS** (Apple Silicon, M1 or newer): open the `.dmg`, drag **Octaris** into **Applications** (replace an older version if asked), and open it with a double-click.

**Windows** (x64): run `Octaris-<version>-win-setup.exe`. Windows may show *"Windows protected your PC"* the first time: click **More info**, then **Run anyway**.

## Safety

- **The Stop button is not an emergency stop.** It only works while the app, the USB connection and the printer are all responding. The printer needs its own hardware emergency stop that cuts motor power, within reach.
- **Temperature warnings in Octaris are only warnings.** Keep the thermal protection in the printer's firmware switched on.

## Printing, step by step

1. **Connect**: plug in the printer, pick its port in the top-right corner of the Setup screen and click **Connect**.
2. **Choose the printer**: pick **Printess 1.0** or **Printess 2.0** in the *Printer* dropdown. For a different setup, choose **Custom (set up…)** (see below).
3. **Choose the syringes**: tap the syringes you want to print with. With several selected, each one prints its own copy of the same model, side by side.
4. **Zero the syringes** in *Manual Navigation*:
   - Jog the first (leftmost) selected syringe over the start point, lower it onto the bed and click **Zero**.
   - For each further syringe, lower it onto the bed and click **Zero**. Don't move X or Y in between.
5. **Load a file**:
   - **STL**: upload the model and click **Click to Slice**.
   - **G-code** (`.gcode` or `.txt`): upload the file. Choose *Send as uploaded* for files that are ready to print (e.g. from PrintessFlow).
6. **Check the preview**: the 3D view shows the path of each syringe. Then click **Proceed to Preview**.
7. **Print**: use **Pause**, **Resume** and **Stop** during the print. The **flow slider** changes the extrusion live (over- or underpumping). After a stop, **Resume** continues where it stopped; **Restart** goes back to the zero point and prints from the beginning.

Octaris checks every print, and every syringe's copy, against the bed limits before it starts, and refuses a print that would leave the bed.

## Manual Navigation

- **Steps**: 0.1, 1 or 5 mm, or your own value up to 200 mm.
- **Feed rates** (mm/min): one for X/Y, one for the heights and extruders.
- **Height** and **extruder** columns: tap the chips above each column to choose which syringes move. **Retract** (up) and **Extrude** (down) move the plungers. There's no limit on extruder moves, so be careful with large steps.
- **Go to Origin** moves X/Y back to the zero point at the current height. **Lower to zero height** then lowers the selected syringes to the height they were zeroed at (only after zeroing).

The **Take Over** screen (sidebar) shows everything sent to and from the printer, and lets you type G-code commands directly.

## Temperatures

Temperature control is off by default, for printers without sensors. To use it, in Setup under *Temperature control*:

1. Switch on **This printer has temperature sensors**.
2. Choose the **protocol**: *Marlin*, or *Printess lab firmware* for the lab's heater channels.
3. With the lab firmware: choose for each channel (I0–I3) what it heats: a syringe, the bed, or *Not used*.

The **Temperature** screen then shows each heater's temperature, target and status, with a chart and a CSV export. Set a target there, or switch a heater off. If a heater is off and has no reading, choose **Heat to** or **Cool to**.

## Your own printer setup

Choose **Custom (set up…)** in the Printer dropdown, or **Edit setup** on the Setup screen. For each syringe, enter its name, height axis, plunger axis and its offset from the first syringe. Below that, set the bed limits and the steps per mm, then click **Save**. Octaris remembers the setup, and you can switch between it and the built-in printers at any time.

## Troubleshooting

- **The printer isn't listed**: check the USB cable and that the printer is switched on, then reopen the port list.
- **The app doesn't open on macOS** (older versions only): open Terminal and run `xattr -cr /Applications/Octaris.app`.
- **Something went wrong during a print**: the log files are in `~/Library/Application Support/Octaris/logs` (macOS) or `%LOCALAPPDATA%\Octaris\logs` (Windows). Please include them when reporting a problem.

Developers: see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
