"""AquaSim XML input metadata and SI material conversion."""
from __future__ import annotations
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GRAVITY_M_S2 = 9.80665


@dataclass(frozen=True)
class BeamLoad:
    component_id: int
    name: str
    length_m: float
    mass_kg: float
    weight_n: float
    buoyancy_n: float
    mass_per_m_kg: float
    submerged_mass_per_m_kg: float
    center_m: tuple[float, float, float]


def _number(element: ET.Element, key: str, *, positive: bool = False) -> float | None:
    raw = element.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _position(node: ET.Element) -> tuple[float, float, float]:
    return tuple(float(node.get(axis, "nan")) for axis in ("x", "y", "z"))


def _beam_properties(beam: ET.Element) -> tuple[float, float, float] | None:
    """Return cross-section area, displaced area, and material density in SI units."""
    wizard = beam.find("wizard")
    if wizard is None:
        return None

    density = _number(wizard, "massDensity", positive=True)
    kind = wizard.get("type", "").lower()
    if density is None:
        return None

    if kind == "circular":
        diameter_mm = _number(wizard, "outerDiameter", positive=True)
        wall_mm = _number(wizard, "thickness", positive=True)
        if diameter_mm is None or wall_mm is None:
            return None
        diameter = diameter_mm / 1000.0
        wall = wall_mm / 1000.0
        inner_diameter = diameter - 2.0 * wall
        if inner_diameter < 0:
            return None
        material_area = math.pi * (diameter**2 - inner_diameter**2) / 4.0
        displaced_area = math.pi * diameter**2 / 4.0
        return material_area, displaced_area, density

    if kind == "ibeam":
        flange_width = _number(wizard, "tfw", positive=True)
        web_height = _number(wizard, "wh", positive=True)
        web_thickness = _number(wizard, "wt", positive=True)
        top_flange = _number(wizard, "tft", positive=True)
        bottom_flange = _number(wizard, "bft", positive=True)
        if None in (flange_width, web_height, web_thickness, top_flange, bottom_flange):
            return None
        width = flange_width / 1000.0
        height = web_height / 1000.0
        web = web_thickness / 1000.0
        top = top_flange / 1000.0
        bottom = bottom_flange / 1000.0
        material_area = width * (top + bottom) + height * web
        # Open I profiles displace their solid material, not the bounding envelope.
        displaced_area = material_area
        return material_area, displaced_area, density

    return None


def _beam_line_loads(
    beam: ET.Element, water_density: float
) -> tuple[float, float] | None:
    """Return model-provided air and submerged line mass in kg/m."""
    material = beam.find("materials")
    if material is not None:
        mass_per_m = _number(material, "weightInAir")
        submerged_mass_per_m = _number(material, "weightInWater")
        if mass_per_m is not None and mass_per_m >= 0:
            if submerged_mass_per_m is not None:
                return mass_per_m, submerged_mass_per_m
            if mass_per_m == 0:
                return 0.0, 0.0
            displaced_area = _number(material, "crossectionvolume", positive=True)
            if displaced_area is None:
                properties = _beam_properties(beam)
                displaced_area = properties[1] if properties is not None else None
            if displaced_area is not None:
                return mass_per_m, mass_per_m - water_density * displaced_area

    properties = _beam_properties(beam)
    if properties is None:
        return None
    material_area, displaced_area, material_density = properties
    mass_per_m = material_area * material_density
    return mass_per_m, mass_per_m - displaced_area * water_density


def _line_length_and_center(
    component: ET.Element, nodes: dict[int, tuple[float, float, float]]
) -> tuple[float, tuple[float, float, float]]:
    length_sum = 0.0
    weighted_center = [0.0, 0.0, 0.0]
    for element in component.findall(".//element"):
        start = element.get("StartNode_ID") or element.get("startNodeId")
        end = element.get("EndNode_ID") or element.get("endNodeId")
        if start is None or end is None:
            continue
        try:
            a = nodes[int(start)]
            b = nodes[int(end)]
        except (ValueError, KeyError):
            continue
        length = math.dist(a, b)
        if length <= 0:
            continue
        length_sum += length
        for axis in range(3):
            weighted_center[axis] += (a[axis] + b[axis]) * 0.5 * length
    if length_sum == 0:
        raise ValueError(f"Beam {component.get('name', component.get('id', '?'))!r} has no valid nonzero-length elements.")
    return length_sum, tuple(value / length_sum for value in weighted_center)


def _winch_top_nodes(
    components: ET.Element, nodes: dict[int, tuple[float, float, float]]
) -> list[dict[str, Any]]:
    winches = []
    for name in ("winch1", "winch2"):
        component = next(
            (
                comp
                for comp in components.findall("./truss")
                if (comp.get("name") or "").strip().casefold() == name
            ),
            None,
        )
        if component is None:
            raise ValueError(f"Required truss component {name!r} was not found.")
        degree: dict[int, int] = {}
        for element in component.findall(".//element"):
            first = element.get("StartNode_ID") or element.get("startNodeId")
            second = element.get("EndNode_ID") or element.get("endNodeId")
            if first is None or second is None:
                continue
            try:
                a, b = int(first), int(second)
            except ValueError:
                continue
            if a in nodes and b in nodes:
                degree[a] = degree.get(a, 0) + 1
                degree[b] = degree.get(b, 0) + 1
        endpoints = [node_id for node_id, count in degree.items() if count == 1]
        if not endpoints:
            raise ValueError(f"Winch rope {name!r} has no valid endpoint nodes.")
        highest_z = max(nodes[node_id][2] for node_id in endpoints)
        top_node_ids = sorted(
            node_id for node_id in endpoints if math.isclose(nodes[node_id][2], highest_z, abs_tol=1e-6)
        )
        winches.append({"name": name, "componentId": int(component.get("id", "0")), "topNodeIds": top_node_ids})
    return winches


def _read_model(model_path: Path, water_density: float, *, root: ET.Element | None = None) -> dict[str, Any]:
    if root is None:
        try:
            root = ET.parse(model_path).getroot()
        except (ET.ParseError, OSError) as error:
            raise ValueError(f"Cannot read AquaSim model {model_path}: {error}") from error

    nodes: dict[int, tuple[float, float, float]] = {}
    for node in root.findall("./Nodes/node"):
        try:
            node_id = int(node.attrib["id"])
            position = _position(node)
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid node record: {node.attrib}") from error
        if not all(math.isfinite(value) for value in position):
            raise ValueError(f"Node {node_id} has a missing or non-finite coordinate.")
        if node_id in nodes:
            raise ValueError(f"Duplicate node ID {node_id}.")
        nodes[node_id] = position
    if not nodes:
        raise ValueError("The model has no valid nodes.")

    components = root.find("./Components")
    if components is None:
        raise ValueError("The model has no Components section.")

    warnings: list[str] = []
    surface_node_ids = sorted(
        node_id for node_id, position in nodes.items() if math.isclose(position[2], 0.0, abs_tol=1e-9)
    )
    beams = components.findall("./beam")
    rigid_beam_node_ids: dict[int, set[int]] = {}
    adjacency: dict[int, set[int]] = {}
    component_ids = set()
    for component in components:
        if component.tag not in {'beam', 'truss', 'membrane'}:
            continue
        try:
            component_id = int(component.attrib['id'])
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid {component.tag} component ID.") from error
        if component_id in component_ids:
            raise ValueError(f"Duplicate component ID {component_id}.")
        component_ids.add(component_id)
        element_ids = set()
        for element in component.findall('.//element'):
            try:
                element_id = int(element.attrib['id'])
            except (KeyError, ValueError) as error:
                raise ValueError(f"Component {component_id} has an invalid element ID.") from error
            if element_id in element_ids:
                raise ValueError(f"Component {component_id} has duplicate element ID {element_id}.")
            element_ids.add(element_id)
            if component.tag in {'beam', 'truss'}:
                try:
                    a = int(element.get('StartNode_ID') or element.get('startNodeId'))
                    b = int(element.get('EndNode_ID') or element.get('endNodeId'))
                except (ValueError, TypeError) as error:
                    raise ValueError(f"Element {element_id} has invalid endpoint IDs.") from error
                if a not in nodes or b not in nodes:
                    raise ValueError(f"Element {element_id} references an unknown node.")
                if component.tag == 'beam':
                    adjacency.setdefault(a, set()).add(b)
                    adjacency.setdefault(b, set()).add(a)
    for index, beam in enumerate(beams):
        rigid_beam_node_ids[index] = {
            int(element.get(key) or element.get(alias))
            for element in beam.findall('.//element')
            for key, alias in (('StartNode_ID','startNodeId'),('EndNode_ID','endNodeId'))
        }
    winches = _winch_top_nodes(components, nodes)
    fixed_node_set = {
        int(node.get("id", "-1"))
        for node in root.findall("./Nodes/node")
        if node.get("fixed", "").casefold() in {"true", "1", "yes"}
    }
    fixed_node_set.update(node_id for winch in winches for node_id in winch["topNodeIds"])
    # Propagate supports through actual beam edges. A component can contain
    # disconnected sections, so sharing a component ID does not constrain them.
    pending = list(fixed_node_set)
    while pending:
        node_id = pending.pop()
        for other in adjacency.get(node_id, ()):
            if other not in fixed_node_set:
                fixed_node_set.add(other)
                pending.append(other)
    fixed_node_ids = sorted(fixed_node_set)
    assumptions = [
        "Z=0 is the waterline and water is below it; only explicit supports and winch upper attachments constrain motion.",
        "Nodes marked fixed and all winch1/winch2 upper endpoints are stationary supports; beam assemblies rigidly connected to a support are fixed as a whole.",
        "Truss components use axial elasticity and tension-only behavior when specified by the model.",
        "Winch control changes unstretched cable length along each explicit connected path.",
        "Netting uses tension-only axial edges and diagonal bracing in quasi-static equilibrium.",
        "Winch points are the highest-elevation endpoint nodes of the winch1 and winch2 truss networks.",
        "Beam air and submerged line masses use materials.weightInAir and materials.weightInWater when present; geometry and material density fill missing values.",
        "Hydrodynamic drag, added mass, waves, and currents are omitted for the prescribed slow lift.",
        "Beam assemblies solve translational and rotational equilibrium; beam stress and elastic deformation are not calculated.",
    ]
    warnings.append(
        f"Water density is not defined in the model; configured value {water_density:g} kg/m^3 is used. Override it with --water-density-kg-m3."
    )
    warnings.append("Rope pretension values with ambiguous units are not used to alter initial rope lengths; ropes initialize stress-free.")

    beam_loads: list[BeamLoad] = []
    fixed_beam_names: list[str] = []
    moving_beam_names: list[str] = []
    for beam_index, beam in enumerate(beams):
        name = beam.get("name", f"beam_{beam.get('id', '?')}")
        try:
            component_id = int(beam.get("id", ""))
        except ValueError:
            component_id = len(beam_loads) + 1
        beam_node_ids = rigid_beam_node_ids[beam_index]
        fixed_count = len(beam_node_ids.intersection(fixed_node_ids))
        if fixed_count == len(beam_node_ids):
            fixed_beam_names.append(name)
        elif fixed_count == 0:
            moving_beam_names.append(name)
        if beam_node_ids.intersection(fixed_node_ids) and len(beam_node_ids.intersection(fixed_node_ids)) != len(beam_node_ids):
            warnings.append(
                f"Beam {name!r} contains supported and disconnected free sections; only connected supported sections are fixed."
            )
        length, center = _line_length_and_center(beam, nodes)
        line_loads = _beam_line_loads(beam, water_density)
        if line_loads is None:
            warnings.append(f"Beam {name!r}: material density or supported section dimensions are missing; its weight and buoyancy are omitted.")
            beam_loads.append(BeamLoad(component_id, name, length, 0.0, 0.0, 0.0, 0.0, 0.0, center))
            continue
        mass_per_m, submerged_mass_per_m = line_loads
        mass = mass_per_m * length
        weight_n = mass * GRAVITY_M_S2
        buoyancy_n = (mass_per_m - submerged_mass_per_m) * length * GRAVITY_M_S2
        beam_loads.append(
            BeamLoad(
                component_id,
                name,
                length,
                mass,
                weight_n,
                buoyancy_n,
                mass_per_m,
                submerged_mass_per_m,
                center,
            )
        )

    if not beams:
        raise ValueError("No beam components were found; the rigid-body simulation requires beam bodies.")
    assumptions.append(
        f"Rigid beams held fixed: {', '.join(fixed_beam_names) or 'none'}; translated: {', '.join(moving_beam_names) or 'none'}."
    )

    if any(len(winch["topNodeIds"]) > 1 for winch in winches):
        warnings.append(
            "Each winch component contains multiple parallel top endpoints; each disconnected cable path is reeled at the prescribed speed."
        )
    node_loads: list[dict[str, Any]] = []
    for node in root.findall("./Nodes/node"):
        try:
            node_id = int(node.attrib["id"])
        except (KeyError, ValueError):
            continue
        for point_load in node.findall("./pointLoad"):
            if point_load.get("active", "true").casefold() == "false":
                continue
            force = point_load.find("Force")
            moment = point_load.find("Momentum")
            if force is None:
                continue
            vector = tuple(_number(force, axis) or 0.0 for axis in ("x", "y", "z"))
            applied_moment = (
                tuple(_number(moment, axis) or 0.0 for axis in ("x", "y", "z"))
                if moment is not None
                else (0.0, 0.0, 0.0)
            )
            node_loads.append({"nodeId": node_id, "forceN": vector, "momentNm": applied_moment})

    total_mass = sum(load.mass_kg for load in beam_loads)
    if total_mass <= 0:
        warnings.append("No beam masses could be derived; reported winch tensions omit beam weight.")
        center_of_mass = tuple(sum(point[axis] for point in nodes.values()) / len(nodes) for axis in range(3))
    else:
        center_of_mass = tuple(
            sum(load.mass_kg * load.center_m[axis] for load in beam_loads) / total_mass for axis in range(3)
        )
    weight = sum(load.weight_n for load in beam_loads)
    buoyancy = sum(load.buoyancy_n for load in beam_loads)
    point_force = [sum(load["forceN"][axis] for load in node_loads) for axis in range(3)]
    net_vertical = weight - buoyancy - point_force[2]
    moment = [0.0, 0.0, 0.0]
    for load in beam_loads:
        upward_buoyancy = load.buoyancy_n
        downward_weight = load.weight_n
        z_force = upward_buoyancy - downward_weight
        rx, ry, _ = (load.center_m[axis] - center_of_mass[axis] for axis in range(3))
        moment[0] += ry * z_force
        moment[1] -= rx * z_force
    for load in node_loads:
        point = nodes[load["nodeId"]]
        rx, ry, rz = (point[axis] - center_of_mass[axis] for axis in range(3))
        fx, fy, fz = load["forceN"]
        mx, my, mz = load["momentNm"]
        moment[0] += ry * fz - rz * fy + mx
        moment[1] += rz * fx - rx * fz + my
        moment[2] += rx * fy - ry * fx + mz

    return {
        "modelName": root.get("name") or "AquaSim Model",
        "nodeIds": sorted(nodes),
        "nodePositionsM": [list(nodes[node_id]) for node_id in sorted(nodes)],
        "surfaceNodeIds": surface_node_ids,
        "fixedNodeIds": fixed_node_ids,
        "beamLoads": [
            {
                "componentId": load.component_id,
                "name": load.name,
                "lengthM": load.length_m,
                "massKg": load.mass_kg,
                "weightN": load.weight_n,
                "buoyancyN": load.buoyancy_n,
                "massPerMkg": load.mass_per_m_kg,
                "submergedMassPerMkg": load.submerged_mass_per_m_kg,
                "centerM": list(load.center_m),
            }
            for load in beam_loads
        ],
        "winches": winches,
        "nodeLoads": node_loads,
        "centerOfMassM": list(center_of_mass),
        "totalMassKg": total_mass,
        "weightN": weight,
        "buoyancyN": buoyancy,
        "netVerticalLoadN": net_vertical,
        "momentNm": moment,
        "warnings": warnings,
        "assumptions": assumptions,
        "waterDensityKgM3": water_density,
    }


