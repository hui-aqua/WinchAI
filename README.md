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
simulation/
  winch_solver.py           Local simulation CLI and AQWR encoder
  rope_dynamics.py          Experimental XPBD/explicit backend
  moordyn_backend.py        Experimental MoorDyn-C cable backend
tests/
  *.test.ts                 Behavior and regression tests
  fixtures/                 Model data used only by tests
tests_python/
  test_winch_solver.py      Python backend and format regressions
public/branding/            Runtime branding assets
docs/                       User guide and screenshots
.github/workflows/          Pages deployment and release packaging
```

The parser depends on browser XML APIs and has no Three.js dependency.
Renderers consume normalized parser types. The viewer owns rendering
resources; the application connects it to the UI. Tests use Vitest with
happy-dom and include a real model fixture without shipping it to users.

## Local winch simulation

The Python simulator is separate from the browser viewer. It reads the private
target at `public/Next_Otervika_NLS_AI.amodel` by default and writes a local
binary `.aqwr` playback file. Install dependencies using the interpreter that
will run the command:

```sh
python -m pip install -r requirements.txt
```

The default result is `simulation_results/winch-result.aqwr`. The default
`xpbd` path is an experimental preview, not the recommended cable engine. To
try the MoorDyn-C v2 backend, use a surveyed water depth measured downward from
the Z=0 water surface. Do not copy an illustrative depth from another site:

```sh
SITE_DEPTH_M=REPLACE_WITH_SURVEYED_DEPTH
python -m simulation.winch_solver \
  --solver moordyn \
  --water-depth-m "$SITE_DEPTH_M" \
  --duration-seconds 1 \
  --frame-interval-seconds 0.05 \
  --output simulation_results/moordyn-test.aqwr
```

To run XPBD for comparison, omit `--water-depth-m` and select `--solver xpbd`.
Other controls include `--duration-minutes`, `--max-internal-steps`,
`--step-seconds`, `--iterations`, `--water-density-kg-m3`, and `--no-membrane`.
The take-up command is 1 m/min on winch1/winch2 paths.

Run `npm run dev`, load the matching `.amodel` with **Open .amodel**, then use
**Load result** to select the `.aqwr`. Playback provides a timeline,
play/pause, high-speed playback, per-element cable tension colouring, and
winch-tension/net-load plots. Model and result files remain local.

### Physics and current scope

MoorDyn-C v2 is an established lumped-mass mooring/cable dynamics library. The
new `--solver moordyn` path translates AquaSim trusses to MoorDyn lines, connected
beam groups to rigid bodies, and takes an initial static-relaxation step before
implicit `BEuler2` integration. Degree-two cable chains are represented as
multi-segment lines; original AquaSim node positions and truss-edge tensions are
mapped back into `.aqwr`. The backend applies the requested winch take-up by
shortening the unstretched length of winch paths.

The target has 37 truss components, seven beam components, and seven membrane
components. The 20 upper winch endpoints are fixed at Z=0 per the stated system
boundary condition. Their rigidly connected Flytekrage/Klammer/Stuss assembly
is fixed; Bunnring is a moving body. Z=0 remains the waterline, and all water
loads act below it. MoorDyn requires actual water depth, so `--water-depth-m` is
mandatory for this backend.

The target description has no roller radius, wrap surface, or friction values.
As an initial system-level approximation, winch2 lower endpoints connect to
nearest Bunnring nodes through fixed-length, frictionless point-fairlead legs.
They are not finite-radius roller contact. The user interface and result do not
simulate a drum. Membranes are approximated as tension-only cable edges with
diagonal bracing, not shell/membrane finite elements. Beam inertia is
diagonalized; generic drag coefficients fill missing material data. Negative
apparent submerged line mass and any non-gravitational point-load effects need
specific validation against the source model's intended semantics.

**Validation status:** all 5,034 target nodes and 3,212 source cable elements
map into a MoorDyn system. A one-step diagnostic with membranes disabled and an
assumed test depth of 100 m returned finite coordinates, but showed MoorDyn
catenary-initialization fallbacks and a peak cable tension of about 4.13 MN.
That depth was only a software test value, not site data. The full membrane model,
long-run equilibrium, timestep/mesh convergence, line loads, and fairlead
mechanics are not validated. The XPBD path also remains iteration-sensitive.
Do not use either backend for engineering design or realistic lift predictions.

Run Python backend regressions with
`python -m unittest tests_python.test_winch_solver`; frontend checks use
`npm run check`. The local Vite server and production build exclude
`Next_Otervika_NLS_AI.amodel` from the public site and bundle. Do not publish the
private target model or generated results outside this repository without
permission.

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
