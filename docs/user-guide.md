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

## Winch simulation playback

The simulation panel plays local `.aqwr` binary results produced by the
Python solver. Install its dependencies with
`python -m pip install -r requirements.txt`, then run
`python -m simulation.winch_solver` from the repository root. Load the
matching `.amodel` and select **Load result**.
Use play/pause, the timeline, and speed selector to inspect node motion.
Winch rope elements are coloured by their computed axial tension; the plot
shows the two winch endpoint tensions and the instantaneous net vertical
load. Neither model nor result is sent to a server.

The solver integrates transient motion of rigid beam bodies and axial,
lumped-mass rope elements. Beam gravity and buoyancy use the input's
`materials.weightInAir` and `materials.weightInWater` line masses where
available. Winch1 and winch2 unstretched rope paths are taken up at 1 m/min.
Every node initially on Z=0 remains fixed in world coordinates; a rigid beam
touching a fixed node is held fixed as a whole. Ropes attached to moving beam
nodes follow their parent body.

This is not a quasi-static equilibrium or design solver. Membranes remain
static and do not contribute force. Drag, added mass, waves, currents, and
unsupported damping are omitted. Model pretension fields with ambiguous
units/reference are reported and ignored, so ropes start stress-free at the
input geometry. Transient oscillations may persist; fixed-node reactions,
beam stress, and elastic deformation are not solved. Water density defaults
to 1025 kg/m³ where the model lacks submerged line-mass data, and buoyancy
for open I-beams is approximated using an outer envelope. The result's
warnings and assumptions list these limitations. Do not use preview forces
or motion for engineering design.

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
