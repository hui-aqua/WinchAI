# User guide

The viewer starts empty. Click **Open .amodel** to select a local model, or
drop an `.amodel` or XML file onto the viewport. Data stays in your browser.
Loading another file replaces the current model.

## Camera controls

| Action | Mouse | Touch |
| --- | --- | --- |
| Rotate | Left button and drag | One finger |
| Pan | Right button and drag | Two fingers |
| Zoom | Scroll wheel | Pinch |

Use **Fit entire model** for an overview or **Focus cage** to frame beams
and membranes. The projection switch toggles perspective and orthographic
cameras. The floating VIEW panel provides X+, Y+, Z+, and isometric (3D) views.

## Display controls

Choose solid sections with centerlines, solid sections only, or centerlines
only for beams and trusses. Membranes remain translucent net surfaces.
Grid and axes buttons toggle orientation helpers. AquaSim coordinates are
preserved: Z is vertical, the sea surface is Z = 0, and negative Z is underwater.

The theme button switches dark and light backgrounds and saves the preference
when storage is available. The sidebar arrow hides or shows model information.
The sidebar starts collapsed on narrow screens.

## Winch simulation

Install dependencies with `python -m pip install -r requirements.txt` and
`npm ci`, then start the integrated local GUI and solver API from the
repository root with `npm run winch`. Open `http://127.0.0.1:8765/`, load an
`.amodel`. Properties are read immediately when the model is imported.
Click **Component Properties** to review or edit them, then click **Winch Simulation**
to configure the winches and timing.

The **Component Properties** dialog displays the imported component table. Edit
cable/net EA (N), total unstretched segment length (m), mass (kg), weight in
air (N/m), and fully submerged weight in water (N/m). Total air and submerged
weights (N) are calculated from length, with subtotals by component type and
a grand total. Mass and weights display no decimals; lengths display two.
Unedited source values retain their original precision. Mass and air weight are
linked by W = mass × 9.80665. Negative water weight represents net buoyancy.
Beam geometry and EA are read-only because beams are rigid in this solver;
their mass and water weight remain editable. Edits are retained for the loaded model and subsequent simulations; they do
not rewrite the source model. A component length edit scales its reference
segments uniformly; mass is a component total; air and water weights are specified per metre.

In the winch table, **Specified payout** is the change in unstretched wire
length over the simulation duration: positive pays out, negative hauls in,
and zero holds length constant. Each winch has an independent value. If a
winch has multiple wire paths, the specified change applies to each path.
The constant rate is payout / duration and is shown in m/s. Set the duration
in seconds and **Save ΔT** in seconds. Initial and final states are always
saved; intermediate states are solved exactly at the requested save times.

The initial cable increment defaults to 50 mm under equilibrium step settings.
The solver reduces internal steps near surface crossings, slack/taut changes,
and failed equilibrium solves. Save ΔT controls output sampling independently
of these internal steps. Each saved state must satisfy the equilibrium checks.
Zero-payout runs retain the same validated equilibrium for the full duration.
The timeline represents the specified winch schedule; inertia is omitted.
Playback speed changes presentation only. Use play/pause, the timeline, and
speed selector to inspect positions and cable tension colouring.
The model is sent only to the loopback Python process, used temporarily, and
not sent to a remote server. The GitHub Pages viewer is static and cannot run
local Python calculations.

The current solver remains under development and is not a certified
engineering package. Routing follows explicit model connectivity and omits roller radius,
wrap contact, and friction; membranes are approximated as cable-edge/diagonal
networks rather than shell finite elements. Validate materials, loads,
winch routing, and model assumptions before engineering use.

## Components and statistics

The sidebar groups beams, trusses, and membranes. Category checkboxes toggle
all components in a category; individual checkboxes toggle one component.
Expand arrows collapse lists. Swatches reflect model colors and section
badges show dimensions in millimeters.

Statistics include filename, node and element counts, component counts,
invalid references, and bounds in meters. Elements referencing missing nodes
are skipped and reported as warnings.

## Offline use

Unpack the standalone release ZIP and serve the directory over HTTP with a
local server. For example, run `python -m http.server` in that directory
and open `http://localhost:8000/`.

## Run diagnostics

Every Run simulation attempt saves its settings and diagnostics in browser
local storage. Use **Download run diagnostics** to export the last attempt,
including network errors, HTTP status, model filename, component properties,
controls and any server run ID. No source XML is stored in browser diagnostics.
The next attempt replaces the browser's previous report.

The local Python server saves each received simulation request under
`simulation_results/runs/<run-id>/`: `input.amodel`, `settings.json`,
`request.body`, `diagnostics.json`, `stdout.log`, `stderr.log`, and
`traceback.log` for unexpected server errors. Validation failures may have only
the request and diagnostic report. These files stay locally and are ignored
by Git. They remain available after failed runs.

“Failed to fetch” means no readable API response reached the browser. Start
`npm run winch` and use `http://127.0.0.1:8765/`. With `npm run dev`, start
`python -m simulation.local_server --api-only` separately. A static viewer
has no Python solver. Check the server terminal if the connection drops during
a run; the retained reports show the last recorded stage and Python errors.
