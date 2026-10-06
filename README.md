# Amodel Viewer

A browser-based 3D viewer for AquaSim `.amodel` files, built with TypeScript,
Three.js, and Vite. Parsing and rendering run locally in the browser; model
files are never uploaded. The viewer starts empty and accepts local files.

[Open the viewer](https://hui-aqua.github.io/AmodelViewer/) |
[User guide](docs/user-guide.md) | [MIT license](LICENSE)

## Development

Use Node.js 24 (see `.nvmrc`), then:

```sh
npm ci
npm run dev
```

Open the URL printed by Vite. Choose **Open .amodel** or drop an `.amodel`
or XML model onto the viewport. The UI supports component visibility,
model statistics, camera alignment and projection, section display modes,
grid and axes, light/dark themes, and a collapsible sidebar.
`?focus=cage` frames the cage after loading a model.

## Commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the development server |
| `npm test` | Run parser, file loading, tree, geometry, and viewer tests |
| `npm run typecheck` | Check application and test TypeScript |
| `npm run build` | Type-check and create the production site in `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run check` | Run type checks, tests, and the production build |

## Structure

```text
src/
  main.ts                   Browser entry point
  app/initializeApp.ts      Model loading and UI orchestration
  parser/                   XML parsing, validation, and model types
  viewer/
    AquaSimViewer.ts         Scene, cameras, controls, and resource lifecycle
    cameraUtils.ts          Coordinate conversion and camera framing
    renderers/              Shared line rendering, beams, trusses, membranes
  ui/                       File loading, model tree, theme and sidebar
  styles/main.css           Application styles
tests/
  *.test.ts                 Behavior and regression tests
  fixtures/                 Model data used only by tests
public/branding/            Runtime branding assets
docs/                       User guide and screenshots
.github/workflows/          Pages deployment and release packaging
```

The parser depends on browser XML APIs and has no Three.js dependency.
Renderers consume normalized parser types. The viewer owns rendering
resources; the application connects it to the UI. Tests use Vitest with
happy-dom and include a real model fixture without shipping it to users.

## Local winch simulation

The Python solver is separate from the browser viewer. It reads the private
local target model at `public/Next_Otervika_NLS_AI.amodel` by default and
writes a binary `.aqwr` result locally. Install Python dependencies once:

```sh
python -m pip install -r requirements.txt
```

From the repository root, run a one-second simulation (1 m/min take-up,
results every 0.05 seconds):

```sh
python -m simulation.winch_solver
```

The default result is `simulation_results/winch-result.aqwr`. To choose a
model, output path, water density, duration, or sample interval:

```sh
python -m simulation.winch_solver "path/to/local-model.amodel" \
  --output "simulation_results/run.aqwr" \
  --water-density-kg-m3 1025 \
  --duration-minutes 125 \
  --frame-interval-seconds 1.0 \
  --solver xpbd
```

Run `npm run dev`, load the same `.amodel` with **Open .amodel**, then choose
**Load result** and select the `.aqwr` file. Playback provides a timeline,
play/pause, high-speed playback options (up to 300× for long lifts), per-element
rope tension colouring, and winch-tension/net-load plots. The model and result
remain local.

### Physics and methodology

The default solver uses **Extended Position Based Dynamics (XPBD)** with compliant
distance constraints, providing unconditional numerical stability for long-duration
operations (e.g. 125-minute slow lifts at 1 m/min).

Each of the seven beam components is modelled as a rigid body with translation,
quaternion rotation, material mass, gravity, buoyancy, and point loads. Ropes are
discretized as compliant axial truss elements using model area, Young's modulus,
material density, and submerged line mass with tension-only behaviour.

**Dynamic Net & Membrane Elements**:
Membrane elements (netting panels, dome nets, side nets, and flaps) are dynamically
coupled to their associated ropes and rigid ring beams via compliant distance constraints.
Net masses and apparent submerged weights are integrated, and the 3D meshes deform
and move smoothly during simulation playback rather than remaining static.

**Solvers**:
- **`xpbd` (default)**: Integrates compliant distance constraints, rigid body orientations,
  hydrodynamic drag damping in water, and dynamic netting. Enables stable simulation of
  extended lift durations (such as 125 minutes) with macroscopic timesteps (e.g. 0.02 s or 0.05 s)
  completing in minutes rather than hours.
- **`explicit`**: Legacy explicit transient integrator requiring microsecond timesteps.

Winch1/winch2 unstretched rope paths shorten at 1 m/min. All nodes initially
at Z=0 remain fixed; a rigid beam touching any such node is held fixed as a
whole. Rope/beam attachment nodes move with their rigid beam.

Beam weight and buoyancy use `materials.weightInAir` and
`materials.weightInWater` from the model where available. For the target,
the input-derived properties are:

| Ring | Mass | Weight in air | Buoyancy |
| --- | ---: | ---: | ---: |
| Bunnring | 44,463.1 kg | 436,033.8 N | 400,169.7 N |
| NEXT-ring | 113,619.1 kg | 1,114,222.6 N | 896,452.8 N |
| Innfestingsring | 1,034.1 kg | 10,140.6 N | 19,899.5 N |

Across all seven beam components, the input line properties give about
176,976.9 kg mass and 1,961,422.4 N buoyancy. `weightInWater` is treated as
apparent submerged line mass (including negative values). If it is absent,
geometry/material properties and the configurable 1025 kg/m³ default supply
an estimate; buoyancy for open I-beams is an outer-envelope approximation.

Warnings in each result identify assumptions and unsupported data. Do not
use these preview forces or motions for engineering design.

Run backend physics tests with `python -m unittest discover -s tests_python`.
`npm run check` validates the frontend, result-file parser, playback, and
production build. The local Vite server and production build exclude
`Next_Otervika_NLS_AI.amodel` from the public site and production bundle. The
supplied model is included in this private repository with the user's explicit
permission; do not publish it or generated results outside this repository
without further permission.

## Build and deployment

Production output is generated only in `dist/` and is ignored by Git.
GitHub Pages deploys that directory through `.github/workflows/deploy.yml`.
Configure the repository's Pages source as **GitHub Actions**.
`.github/workflows/release.yml` packages the same output into
`aquasim-web-viewer-standalone.zip` when a release is published.

For offline use, unpack the release and serve its directory with a local HTTP
server (for example, `python -m http.server`). ES modules require HTTP;
opening `index.html` directly with a `file://` URL is unsupported.
The relative asset base also supports hosting under a subdirectory.
Screenshots and test fixtures are excluded from the deployed bundle.

## Screenshots

| Cage | Mooring array |
| --- | --- |
| ![Cage](docs/images/aquasim_cage_preview.png) | ![Mooring array](docs/images/aquasim_viewer_preview.png) |

## Notices

AquaSim is a trademark of Aquastructure AS. This independent viewer is not
affiliated with or endorsed by Aquastructure AS. See
[third-party notices](THIRD_PARTY_NOTICES.md) for license and data notices.
