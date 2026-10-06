# Third-Party Notices & Intellectual Property Clearance

This document details the intellectual property clearance, trademark notices, and third-party open-source software licenses used by **AquaSim Web Viewer**.

---

## 1. Trademark & Non-Affiliation Disclaimer

- **AquaSim®** is a registered trademark of **Aquastructure AS** (Trondheim, Norway).
- **AquaSim Web Viewer** is an independent, community-developed, open-source 3D visualization tool.
- This project is **NOT** affiliated with, authorized by, sponsored by, or in any way officially connected with Aquastructure AS or any of its subsidiaries or affiliates.
- The official Aquastructure AS website can be found at: [https://aquastructure.no/](https://aquastructure.no/)
- The reference to "AquaSim" and the `.amodel` file format is used strictly in an informational, descriptive, and nominative fair-use capacity to indicate file format compatibility.

---

## 2. Benchmark Datasets & Sample Models

The benchmark simulation model retained in `tests/fixtures/ENCC100323640.amodel` has undergone **IP sanitization**:
- All proprietary client references, commercial project identifiers, internal network/drive paths, and personal author metadata have been removed or replaced with generic open identifiers.
- The models serve solely as geometric benchmarks for verifying 3D finite-element parsing and rendering accuracy (nodes, beam collars, mooring line catenary curves, and net membrane triangulations).

---

## 3. Open Source Dependencies & Licenses

AquaSim Web Viewer is built with the following open-source libraries:

### Three.js
- **License:** MIT License
- **Copyright:** (c) 2010-2026 Three.js authors
- **URL:** [https://github.com/mrdoob/three.js](https://github.com/mrdoob/three.js)

### Vite
- **License:** MIT License
- **Copyright:** (c) 2019-present Yuxi (Evan) You & Vite Contributors
- **URL:** [https://github.com/vitejs/vite](https://github.com/vitejs/vite)

### Vitest
- **License:** MIT License
- **Copyright:** (c) 2021-present Anthony Fu & Vitest Contributors
- **URL:** [https://github.com/vitest-dev/vitest](https://github.com/vitest-dev/vitest)

### Happy DOM
- **License:** MIT License
- **Copyright:** (c) 2019-present Cap-go & Happy DOM Contributors
- **URL:** [https://github.com/capricorn86/happy-dom](https://github.com/capricorn86/happy-dom)

### TypeScript
- **License:** Apache License 2.0
- **Copyright:** (c) Microsoft Corporation
- **URL:** [https://github.com/microsoft/TypeScript](https://github.com/microsoft/TypeScript)
