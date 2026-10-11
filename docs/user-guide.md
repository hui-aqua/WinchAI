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
`.amodel`, and click **Winch Simulation**.

Set the cable change (positive lowers, negative retrieves) and initial cable-length increment. The
increment defaults to 50 mm. The quasi-static solver adapts this step size near
free-surface crossings, cable slack/taut transitions, constraint changes, and
failed equilibrium solves. Accepted equilibrium states are warm-started from
the previous state. Force/tension, displacement, buoyancy, and constraint
convergence are checked separately; failed solves are retried at a smaller
increment or reported as failures.

The solver is not tied to real elapsed time. The result timeline represents
the sequence of solved payout states. Use play/pause, the timeline, and speed
selector to inspect structural positions and cable tension colouring. Playback
speed affects presentation only and does not alter the equilibrium solution.
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
