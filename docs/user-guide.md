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

### Run MoorDyn

The default `xpbd` path remains an experimental preview. The recommended
experimental cable-dynamics path uses MoorDyn-C v2. Supply a real site water
depth below Z=0; the solver deliberately has no guessed default:

```sh
SITE_DEPTH_M=REPLACE_WITH_SURVEYED_DEPTH
python -m simulation.winch_solver \
  --solver moordyn \
  --water-depth-m "$SITE_DEPTH_M" \
  --duration-seconds 1 \
  --frame-interval-seconds 0.05 \
  --output simulation_results/moordyn-test.aqwr
```

Replace the depth placeholder with surveyed/site data before running. Install
the dependency with the same Python interpreter used for the command:
`python -m pip install -r requirements.txt`.

Other useful options are `--duration-minutes`, `--max-internal-steps`,
`--step-seconds`, `--iterations`, `--water-density-kg-m3`, and
`--no-membrane`. The take-up rate is fixed at 1 m/min for winch paths.
`--solver xpbd` is still available for comparison but is not numerically
converged on the target model.

### Model assumptions and validation

The target's 20 upper winch endpoints are fixed at Z=0 per the system boundary
definition, fixing their connected Flytekrage/Klammer/Stuss beam assembly while
the Bunnring remains moving. Winch2's lower endpoints connect to nearest
Bunnring nodes as ideal frictionless point fairleads. MoorDyn models trusses as
lumped-mass lines, beams as rigid bodies, and membranes as cable-edge/diagonal
approximations. The `.amodel` has no roller radius, wrap surface, or friction
properties, so the fairleads are not finite-radius roller contacts.

The target has seven rigid beam groups, 37 truss components, and seven
membrane components. Degree-two truss chains are condensed into multi-segment
MoorDyn lines; their internal node positions and per-element tensions are
mapped back to the original node IDs. Membranes are represented as tension-only
edge and diagonal cable approximations, not membrane finite elements. Beam
inertia is diagonalized, and generic drag coefficients are used where material
coefficients are absent.

One short target diagnostic with membranes disabled and a test-only 100 m depth
returned finite coordinates after one 0.01 s step, but still reported MoorDyn
catenary-initialization fallbacks and a peak cable tension near 4.13 MN. The
100 m value was not supplied as site data. The full membrane-enabled target,
long-duration equilibrium, timestep/mesh convergence, measured loads, and
finite-radius roller mechanics are not validated. Treat both solver modes as
experimental visualization only; do not use their outputs for engineering
design. For the detailed solver scope and known limitations, see the README's
Physics and current scope section.

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
