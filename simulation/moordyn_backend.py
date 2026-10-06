"""MoorDyn-C backend for full AquaSim cable-network simulations."""

from __future__ import annotations

import math
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from .winch_solver import GRAVITY_M_S2, _read_model


def _truss_material(component: ET.Element, water_density: float) -> dict[str, float] | None:
    mooring = component.find("mooring")
    if mooring is None:
        return None
    area = _number(mooring, "areal", positive=True)
    young = _number(mooring, "young", positive=True)
    density = _number(mooring, "massdensity", positive=True)
    if area is None or young is None or density is None:
        return None
    mass_air = _number(mooring, "weightInAir")
    if mass_air is None or mass_air <= 0:
        mass_air = density * area
    mass_water = _number(mooring, "weightInWater")
    if mass_water is None:
        mass_water = mass_air - water_density * area
    displaced_area = max(0.0, (mass_air - mass_water) / water_density)
    diameter = math.sqrt(4.0 * displaced_area / math.pi) if displaced_area > 0 else 2.0 * math.sqrt(area / math.pi)
    added_mass = max(
        0.0,
        _number(mooring, "addedmasscoefflocaly") or 0.0,
        _number(mooring, "addedmasscoefflocalz") or 0.0,
    )
    return {
        "diameter": diameter,
        "massAir": mass_air,
        "massWater": mass_water,
        "ea": young * area,
        "ca": added_mass,
    }


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


def _element_nodes(element: ET.Element) -> tuple[int, int] | None:
    first = element.get("StartNode_ID") or element.get("startNodeId")
    second = element.get("EndNode_ID") or element.get("endNodeId")
    if first is None or second is None:
        return None
    try:
        return int(first), int(second)
    except ValueError:
        return None


def _compress_line_specs(
    line_specs: list[dict[str, Any]],
    body_for_node: dict[int, int],
    fixed_node_ids: set[int],
    loaded_node_ids: set[int],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    groups_by_node: dict[int, set[tuple[Any, ...]]] = defaultdict(set)
    for spec in line_specs:
        group_key = (
            spec["componentId"],
            spec["typeName"],
            bool(spec.get("isMembrane")),
            spec["componentName"],
        )
        groups[group_key].append(spec)
        groups_by_node[spec["startNodeId"]].add(group_key)
        groups_by_node[spec["endNodeId"]].add(group_key)

    compressed: list[dict[str, Any]] = []
    for group_key, group_edges in groups.items():
        adjacency: dict[int, list[int]] = defaultdict(list)
        for edge_index, spec in enumerate(group_edges):
            adjacency[spec["startNodeId"]].append(edge_index)
            adjacency[spec["endNodeId"]].append(edge_index)
        junctions = {
            node_id
            for node_id, incident in adjacency.items()
            if len(incident) != 2
            or node_id in body_for_node
            or node_id in fixed_node_ids
            or node_id in loaded_node_ids
            or len(groups_by_node[node_id]) > 1
        }
        unseen = set(range(len(group_edges)))
        chains: list[tuple[list[int], list[int]]] = []
        for start_node in sorted(junctions):
            for first_edge in adjacency[start_node]:
                if first_edge not in unseen:
                    continue
                path_edges = []
                path_nodes = [start_node]
                current_node = start_node
                edge_index = first_edge
                while edge_index in unseen:
                    unseen.remove(edge_index)
                    path_edges.append(edge_index)
                    edge = group_edges[edge_index]
                    next_node = edge["endNodeId"] if edge["startNodeId"] == current_node else edge["startNodeId"]
                    path_nodes.append(next_node)
                    if next_node in junctions:
                        break
                    next_edges = [candidate for candidate in adjacency[next_node] if candidate in unseen]
                    if len(next_edges) != 1:
                        break
                    current_node = next_node
                    edge_index = next_edges[0]
                chains.append((path_edges, path_nodes))

        # A closed degree-two loop has no natural endpoints; keep its original edges.
        for edge_index in sorted(unseen):
            edge = group_edges[edge_index]
            chains.append(([edge_index], [edge["startNodeId"], edge["endNodeId"]]))

        for path_edges, path_nodes in chains:
            source_specs = [group_edges[index] for index in path_edges]
            first_spec = source_specs[0]
            compressed.append({
                **first_spec,
                "startNodeId": path_nodes[0],
                "endNodeId": path_nodes[-1],
                "pathNodeIds": path_nodes,
                "sourceSpecs": source_specs,
                "length": sum(spec["length"] for spec in source_specs),
                "takeupRate": 0.0,
                "winchIndex": max((spec.get("winchIndex", -1) for spec in source_specs), default=-1),
            })
    return compressed


def _membrane_edges(components: ET.Element, node_ids: set[int], water_density: float) -> dict[tuple[int, int], dict[str, float]]:
    edges: dict[tuple[int, int], dict[str, float]] = {}
    for component in components.findall("./membrane"):
        young = _number(component, "emodule", positive=True) or 1.0e9
        area = _number(component, "areal", positive=True) or 2.5e-6
        density = _number(component, "massDensity", positive=True) or 1000.0
        diameter = 2.0 * math.sqrt(area / math.pi)
        line_type = {
            "diameter": diameter,
            "massAir": density * area,
            "massWater": (density - water_density) * area,
            "ea": young * area,
            "ca": 1.0,
        }
        for element in component.findall(".//element"):
            raw = [element.get(key) for key in ("nodeA", "nodeB", "nodeC", "nodeD", "NodeA", "NodeB", "NodeC", "NodeD") if element.get(key) is not None]
            try:
                element_node_ids = [int(value) for value in raw if int(value) in node_ids]
            except ValueError:
                continue
            pairs: list[tuple[int, int]] = []
            if len(element_node_ids) == 3:
                a, b, c = element_node_ids
                pairs = [(a, b), (b, c), (c, a)]
            elif len(element_node_ids) == 4:
                a, b, c, d = element_node_ids
                pairs = [(a, b), (b, c), (c, d), (d, a), (a, c), (b, d)]
            for first, second in pairs:
                edges.setdefault(tuple(sorted((first, second))), line_type)
    return edges


def _build_moordyn_input(
    model_path: Path,
    water_density: float,
    water_depth_m: float,
    step_seconds: float,
    include_membrane: bool,
) -> tuple[str, list[int], list[int], list[dict[str, Any]], list[str]]:
    model_data = _read_model(model_path, water_density)
    root = ET.parse(model_path).getroot()
    components = root.find("./Components")
    if components is None:
        raise ValueError("The model has no Components section.")
    node_ids = model_data["nodeIds"]
    known_nodes = set(node_ids)
    positions = {
        int(node.get("id")): np.asarray([float(node.get(axis)) for axis in ("x", "y", "z")], dtype=np.float64)
        for node in root.findall("./Nodes/node")
    }
    if water_depth_m <= max(-position[2] for position in positions.values()):
        raise ValueError("MoorDyn water depth must be greater than the model's deepest initial node depth.")

    beams = components.findall("./beam")
    from .rope_dynamics import _beam_groups

    node_index = {node_id: index for index, node_id in enumerate(node_ids)}
    beam_groups = _beam_groups(beams, node_index)
    beam_nodes: list[set[int]] = []
    beam_segments: list[list[tuple[int, int, float, float, float]]] = []
    beam_properties = {int(load["componentId"]): load for load in model_data["beamLoads"]}
    for beam in beams:
        node_set: set[int] = set()
        segments = []
        load = beam_properties.get(int(beam.get("id", "0")))
        if load is None:
            mass_air_per_m = mass_water_per_m = 0.0
        else:
            mass_air_per_m = float(load["massPerMkg"])
            mass_water_per_m = float(load["submergedMassPerMkg"])
        for element in beam.findall(".//element"):
            pair = _element_nodes(element)
            if pair is None or pair[0] not in known_nodes or pair[1] not in known_nodes:
                continue
            first, second = pair
            length = float(np.linalg.norm(positions[second] - positions[first]))
            if length <= 1e-9:
                continue
            node_set.update(pair)
            segments.append((first, second, length, mass_air_per_m, mass_water_per_m))
        beam_nodes.append(node_set)
        beam_segments.append(segments)

    fixed_ids = set(model_data["fixedNodeIds"])
    body_for_node: dict[int, int] = {}
    body_rows: list[str] = []
    body_centers: dict[int, np.ndarray] = {}
    beam_body_names: dict[int, int] = {}
    for body_id, group in enumerate(beam_groups, start=1):
        group_node_ids = set().union(*(beam_nodes[index] for index in group))
        fixed_body = bool(group_node_ids & fixed_ids)
        total_mass = 0.0
        total_displaced_volume = 0.0
        weighted_center = np.zeros(3)
        segments = []
        for beam_index in group:
            for first, second, length, mass_per_m, water_mass_per_m in beam_segments[beam_index]:
                mass = mass_per_m * length
                center = (positions[first] + positions[second]) * 0.5
                total_mass += mass
                weighted_center += mass * center
                total_displaced_volume += max(0.0, mass_per_m - water_mass_per_m) * length / water_density
                segments.append((first, second, length, mass, center, positions[second] - positions[first]))
        if fixed_body:
            center = np.mean(np.asarray([positions[node_id] for node_id in group_node_ids]), axis=0)
            inertia_diagonal = np.ones(3)
            body_mass = 0.0
            body_volume = 0.0
            attachment = "Fixed"
        else:
            if total_mass <= 0.0:
                raise ValueError(f"Moving beam body {body_id} has no positive mass; MoorDyn requires a physical body mass.")
            center = weighted_center / total_mass
            inertia = np.zeros((3, 3), dtype=np.float64)
            identity = np.eye(3)
            for _, _, length, mass, segment_center, delta in segments:
                inertia += mass / 12.0 * (length * length * identity - np.outer(delta, delta))
                offset = segment_center - center
                inertia += mass * (np.dot(offset, offset) * identity - np.outer(offset, offset))
            inertia_diagonal = np.maximum(np.diag(inertia), max(float(np.max(np.diag(inertia))), 1.0) * 1e-9)
            body_mass = total_mass
            body_volume = total_displaced_volume
            attachment = "Free"
        body_centers[body_id] = center
        for node_id in group_node_ids:
            body_for_node[node_id] = body_id
        for beam_index in group:
            beam_body_names[beam_index] = body_id
        body_rows.append(
            f"{body_id} {attachment} {center[0]:.12g} {center[1]:.12g} {center[2]:.12g} 0 0 0 "
            f"{body_mass:.12g} 0 {inertia_diagonal[0]:.12g}|{inertia_diagonal[1]:.12g}|{inertia_diagonal[2]:.12g} "
            f"{body_volume:.12g} 0 0"
        )

    node_loads: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    node_moments: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for load in model_data["nodeLoads"]:
        node_loads[int(load["nodeId"])] = list(load["forceN"])
        node_moments[int(load["nodeId"])] = list(load["momentNm"])

    truss_components = components.findall("./truss")
    component_by_id = {int(component.get("id", "0")): component for component in truss_components}
    winch_by_component = {int(winch["componentId"]): winch_index for winch_index, winch in enumerate(model_data["winches"])}
    line_types: dict[str, dict[str, float]] = {}
    line_specs: list[dict[str, Any]] = []
    warnings = list(model_data["warnings"])
    for component in truss_components:
        component_id = int(component.get("id", "0"))
        properties = _truss_material(component, water_density)
        if properties is None:
            if component.findall(".//element"):
                warnings.append(f"MoorDyn skipped truss {component.get('name', component_id)!r}: material area, Young's modulus, or density is missing.")
            continue
        type_name = f"T{component_id}"
        line_types[type_name] = properties
        for element in component.findall(".//element"):
            pair = _element_nodes(element)
            if pair is None or pair[0] not in known_nodes or pair[1] not in known_nodes:
                continue
            first, second = pair
            length = float(np.linalg.norm(positions[second] - positions[first]))
            if length <= 1e-8:
                continue
            line_specs.append({
                "componentId": component_id,
                "componentName": component.get("name", f"truss_{component_id}"),
                "elementId": int(element.get("id", "0")),
                "startNodeId": first,
                "endNodeId": second,
                "length": length,
                "typeName": type_name,
                "winchIndex": (
                    winch_by_component[component_id]
                    if component_id in winch_by_component
                    and (first in set(model_data["winches"][winch_by_component[component_id]]["topNodeIds"])
                         or second in set(model_data["winches"][winch_by_component[component_id]]["topNodeIds"]))
                    else -1
                ),
                "takeupRate": 0.0,
            })

    membrane_specs: list[dict[str, Any]] = []
    if include_membrane:
        membrane_edge_props = _membrane_edges(components, known_nodes, water_density)
        membrane_types: dict[tuple[float, ...], str] = {}
        for edge, properties in membrane_edge_props.items():
            material_key = tuple(properties[key] for key in ("diameter", "massAir", "massWater", "ea", "ca"))
            type_name = membrane_types.get(material_key)
            if type_name is None:
                type_name = f"M{len(membrane_types) + 1}"
                membrane_types[material_key] = type_name
                line_types[type_name] = properties
            first, second = edge
            length = float(np.linalg.norm(positions[second] - positions[first]))
            if length <= 1e-8:
                continue
            membrane_specs.append({
                "componentId": -1,
                "componentName": "membrane cable approximation",
                "elementId": len(membrane_specs) + 1,
                "startNodeId": first,
                "endNodeId": second,
                "length": length,
                "typeName": type_name,
                "winchIndex": -1,
                "takeupRate": 0.0,
                "isMembrane": True,
            })
        line_specs.extend(membrane_specs)

    # Model the stated winch2-to-Bunnring guide as an ideal point fairlead. MoorDyn
    # does not resolve roller radius, wrap angle, or friction without contact geometry.
    bunnring_index = next((index for index, beam in enumerate(beams) if "bunnring" in (beam.get("name") or "").casefold()), None)
    fairlead_specs: list[dict[str, Any]] = []
    if bunnring_index is not None and beam_body_names.get(bunnring_index) is not None:
        bunnring_body_id = beam_body_names[bunnring_index]
        bunnring_node_ids = beam_nodes[bunnring_index]
        if bunnring_node_ids:
            lower_nodes = _winch_lower_endpoints(component_by_id[model_data["winches"][1]["componentId"]], positions)
            winch2_id = model_data["winches"][1]["componentId"]
            winch2_type = f"T{winch2_id}"
            next_element_id = 1 + max((spec["elementId"] for spec in line_specs), default=0)
            for lower_node_id in lower_nodes:
                fairlead_node_id = min(
                    bunnring_node_ids,
                    key=lambda node_id: float(np.linalg.norm((positions[node_id] - positions[lower_node_id])[:2])),
                )
                length = float(np.linalg.norm(positions[fairlead_node_id] - positions[lower_node_id]))
                if length <= 1e-8:
                    continue
                line_specs.append({
                    "componentId": winch2_id,
                    "componentName": "winch2 Bunnring fairlead leg",
                    "elementId": next_element_id,
                    "startNodeId": lower_node_id,
                    "endNodeId": fairlead_node_id,
                    "length": length,
                    "typeName": winch2_type,
                    "winchIndex": -1,
                    "takeupRate": 0.0,
                    "syntheticFairlead": True,
                })
                next_element_id += 1
            warnings.append("Winch2-to-Bunnring rollers are approximated as frictionless point fairleads at nearest XY Bunnring nodes; roller radius, wrap contact, and friction are not represented.")

    line_specs = _compress_line_specs(line_specs, body_for_node, fixed_ids, set(node_loads))

    for component_id, winch_index in winch_by_component.items():
        matching_indices = [
            index
            for index, spec in enumerate(line_specs)
            if spec["componentId"] == component_id and not spec.get("syntheticFairlead")
        ]
        adjacency: dict[int, list[int]] = defaultdict(list)
        for index in matching_indices:
            adjacency[line_specs[index]["startNodeId"]].append(index)
            adjacency[line_specs[index]["endNodeId"]].append(index)
        unseen = set(matching_indices)
        while unseen:
            seed = unseen.pop()
            component_edges = [seed]
            stack = [line_specs[seed]["startNodeId"], line_specs[seed]["endNodeId"]]
            visited_nodes = set(stack)
            while stack:
                node_id = stack.pop()
                for edge_index in adjacency[node_id]:
                    if edge_index in unseen:
                        unseen.remove(edge_index)
                        component_edges.append(edge_index)
                        spec = line_specs[edge_index]
                        other = spec["endNodeId"] if spec["startNodeId"] == node_id else spec["startNodeId"]
                        if other not in visited_nodes:
                            visited_nodes.add(other)
                            stack.append(other)
            path_length = sum(line_specs[index]["length"] for index in component_edges)
            if path_length > 0:
                for index in component_edges:
                    spec = line_specs[index]
                    spec["takeupRate"] = (1.0 / 60.0) * spec["length"] / path_length
                    if spec["startNodeId"] in set(model_data["winches"][winch_index]["topNodeIds"]) or spec["endNodeId"] in set(model_data["winches"][winch_index]["topNodeIds"]):
                        spec["winchIndex"] = winch_index

    internal_node_ids = {
        node_id
        for spec in line_specs
        for node_id in spec["pathNodeIds"][1:-1]
    }
    point_node_ids = [node_id for node_id in node_ids if node_id not in internal_node_ids]
    point_id_by_node = {node_id: index for index, node_id in enumerate(point_node_ids, start=1)}
    point_rows = []
    used_node_ids = {node_id for spec in line_specs for node_id in spec["pathNodeIds"]}
    for node_id in point_node_ids:
        position = positions[node_id]
        force = node_loads[node_id]
        if node_id in body_for_node:
            attachment = f"Body{body_for_node[node_id]}"
            point_position = position - body_centers[body_for_node[node_id]]
            point_mass = 0.0
        else:
            attachment = "Fixed" if node_id in fixed_ids or node_id not in used_node_ids else "Free"
            point_position = position
            point_mass = max(0.0, -force[2] / GRAVITY_M_S2)
        point_rows.append(
            f"{point_id_by_node[node_id]} {attachment} {point_position[0]:.12g} {point_position[1]:.12g} "
            f"{point_position[2]:.12g} {point_mass:.12g} 0 0 0"
        )

    for type_name, properties in line_types.items():
        for key in ("diameter", "massAir", "ea"):
            if not math.isfinite(properties[key]) or properties[key] <= 0:
                raise ValueError(f"MoorDyn line type {type_name} has invalid {key}={properties[key]}.")
        if properties["massWater"] > properties["massAir"]:
            warnings.append(f"MoorDyn line type {type_name} has submerged mass above dry mass; negative buoyancy is not represented by its equivalent diameter.")

    input_lines = [
        "WinchAI generated MoorDyn-C v2 input; Z positive upward, water surface at Z=0.",
        "---------------------- LINE TYPES ----------------------",
        "TypeName Diam Mass/m EA BA/-zeta EI Cd Ca CdAx CaAx",
        "(-) (m) (kg/m) (N) (N-s/-) (N-m^2) (-) (-) (-) (-)",
    ]
    for type_name, properties in line_types.items():
        input_lines.append(
            f"{type_name} {properties['diameter']:.12g} {properties['massAir']:.12g} {properties['ea']:.12g} "
            f"-0.7 0 1.2 {properties['ca']:.12g} 0.1 0"
        )
    input_lines.extend([
        "---------------------- BODIES ----------------------",
        "ID Attachment X0 Y0 Z0 r0 p0 y0 Mass CG I Volume CdA Ca",
        "(-) (-) (m) (m) (m) (deg) (deg) (deg) (kg) (m) (kg-m^2) (m^3) (m^2) (-)",
        *body_rows,
        "---------------------- POINTS ----------------------",
        "ID Attachment X Y Z Mass Volume CdA Ca",
        "(-) (-) (m) (m) (m) (kg) (m^3) (m^2) (-)",
        *point_rows,
        "---------------------- LINES ----------------------",
        "ID LineType AttachA AttachB UnstrLen NumSegs LineOutputs",
        "(-) (-) (-) (-) (m) (-) (-)",
    ])
    for line_id, spec in enumerate(line_specs, start=1):
        spec["moordynLineId"] = line_id
        input_lines.append(
            f"{line_id} {spec['typeName']} {point_id_by_node[spec['startNodeId']]} {point_id_by_node[spec['endNodeId']]} "
            f"{spec['length']:.12g} {len(spec['pathNodeIds']) - 1} -"
        )
    input_lines.extend([
        "---------------------- OPTIONS ----------------------",
        "0 writeLog",
        "1 disableOutput",
        f"{water_depth_m:.12g} WtrDpth",
        f"{water_density:.12g} rho",
        f"{GRAVITY_M_S2:.12g} g",
        f"{max(step_seconds, 1e-4):.12g} dtM",
        "BEuler2 tScheme",
        "30 TmaxIC",
        "0.5 dtIC",
        "0.01 threshIC",
        "0 WaveKin",
        "0 Currents",
        "---------------------- OUTPUTS ----------------------",
        "---------------------- END ----------------------",
    ])
    if len(line_specs) == 0:
        raise ValueError("MoorDyn model generation produced no line elements.")
    if not any(body_row.startswith("1 Free") or " Free " in body_row for body_row in body_rows):
        warnings.append("MoorDyn input has no free beam body; all represented beam bodies are fixed.")
    warnings.append("Membrane panels are approximated as tension-only cable-edge networks with diagonal bracing, not full membrane/shell elements.")
    warnings.append("MoorDyn line drag uses generic coefficients Cd=1.2 and CdAx=0.1 because model-specific drag coefficients are absent.")
    return "\n".join(input_lines) + "\n", node_ids, point_node_ids, line_specs, warnings


def _winch_lower_endpoints(component: ET.Element, positions: dict[int, np.ndarray]) -> list[int]:
    degree: dict[int, int] = defaultdict(int)
    for element in component.findall(".//element"):
        pair = _element_nodes(element)
        if pair is not None and pair[0] in positions and pair[1] in positions:
            degree[pair[0]] += 1
            degree[pair[1]] += 1
    endpoints = [node_id for node_id, count in degree.items() if count == 1]
    if not endpoints:
        return []
    lowest_z = min(positions[node_id][2] for node_id in endpoints)
    return sorted(node_id for node_id in endpoints if math.isclose(positions[node_id][2], lowest_z, abs_tol=1e-6))


def simulate_moordyn_model(
    model_path: Path,
    water_density: float,
    water_depth_m: float,
    duration_seconds: float,
    frame_interval_seconds: float,
    max_internal_steps: int,
    step_seconds: float,
    include_membrane: bool,
) -> dict[str, Any]:
    if not math.isfinite(water_depth_m) or water_depth_m <= 0:
        raise ValueError("MoorDyn requires a finite water depth greater than zero; provide --water-depth-m from the model/site data.")
    moordyn_input, node_ids, point_node_ids, line_specs, warnings = _build_moordyn_input(
        model_path, water_density, water_depth_m, step_seconds, include_membrane
    )
    effective_step = min(step_seconds, frame_interval_seconds)
    internal_steps = math.ceil(duration_seconds / effective_step)
    if internal_steps > max_internal_steps:
        raise ValueError(
            f"MoorDyn step {effective_step:.2e} s requires {internal_steps:,} steps, above --max-internal-steps ({max_internal_steps:,})."
        )

    import moordyn

    model_data = _read_model(model_path, water_density)
    input_positions = {
        node_id: np.asarray(position, dtype=np.float64)
        for node_id, position in zip(model_data["nodeIds"], model_data["nodePositionsM"])
    }
    node_index_by_id = {node_id: index for index, node_id in enumerate(node_ids)}
    point_id_by_node = {node_id: index for index, node_id in enumerate(point_node_ids, start=1)}
    frame_count = math.ceil(duration_seconds / frame_interval_seconds) + 1
    output_times = np.minimum(np.arange(frame_count, dtype=np.float64) * frame_interval_seconds, duration_seconds)
    output_times[-1] = duration_seconds
    node_positions = np.zeros((frame_count, len(node_ids), 3), dtype=np.float32)
    rope_specs = [
        {**source, "moordynLineId": spec["moordynLineId"], "segmentIndex": segment_index}
        for spec in line_specs if not spec.get("isMembrane")
        for segment_index, source in enumerate(spec["sourceSpecs"])
    ]
    frame_tensions = np.zeros((frame_count, len(rope_specs)), dtype=np.float32)
    frame_winch_tensions = np.zeros((frame_count, 2), dtype=np.float64)
    frame_lift = np.zeros(frame_count, dtype=np.float64)
    time_seconds = 0.0
    with tempfile.TemporaryDirectory(prefix="winchai-moordyn-") as temp_dir:
        input_path = Path(temp_dir) / "moordyn.dat"
        input_path.write_text(moordyn_input, encoding="ascii")
        system = moordyn.Create(str(input_path))
        if system is None:
            raise RuntimeError("MoorDyn failed to create a simulation system from the generated input.")
        try:
            moordyn.SetVerbosity(system, moordyn.LEVEL_WRN)
            moordyn.SetLogLevel(system, moordyn.LEVEL_WRN)
            coupled_dof = moordyn.NCoupledDOF(system)
            if coupled_dof != 0:
                raise ValueError(f"Generated MoorDyn model unexpectedly has {coupled_dof} coupled DOFs; the host coupling is incomplete.")
            moordyn.Init(system, [], [])
            points = {node_id: moordyn.GetPoint(system, point_id_by_node[node_id]) for node_id in point_node_ids}
            initial_point_positions = {
                node_id: np.asarray(moordyn.GetPointPos(points[node_id]), dtype=np.float64)
                for node_id in point_node_ids
            }
            lines = {spec["moordynLineId"]: moordyn.GetLine(system, spec["moordynLineId"]) for spec in line_specs}
            fixed_node_ids = set(_read_model(model_path, water_density)["fixedNodeIds"])
            for frame_index, output_time in enumerate(output_times):
                while time_seconds < output_time - 1e-12:
                    dt = min(effective_step, output_time - time_seconds)
                    for spec in line_specs:
                        if spec["takeupRate"] > 0:
                            new_length = max(1e-4, spec["length"] - spec["takeupRate"] * time_seconds)
                            moordyn.SetLineUnstretchedLength(lines[spec["moordynLineId"]], new_length)
                    moordyn.Step(system, [], [], time_seconds, dt)
                    time_seconds += dt
                node_positions[frame_index] = np.asarray([input_positions[node_id] for node_id in node_ids], dtype=np.float32)
                for node_id, point in points.items():
                    node_positions[frame_index, node_index_by_id[node_id]] = moordyn.GetPointPos(point)
                segment_tensions: dict[tuple[int, int], float] = {}
                for line_spec in line_specs:
                    line = lines[line_spec["moordynLineId"]]
                    node_tensions = [
                        float(np.linalg.norm(moordyn.GetLineNodeTen(line, line_node_index)))
                        for line_node_index in range(len(line_spec["pathNodeIds"]))
                    ]
                    for line_node_index, node_id in enumerate(line_spec["pathNodeIds"]):
                        node_positions[frame_index, node_index_by_id[node_id]] = moordyn.GetLineNodePos(line, line_node_index)
                    for segment_index in range(len(line_spec["sourceSpecs"])):
                        segment_tensions[(line_spec["moordynLineId"], segment_index)] = 0.5 * (
                            node_tensions[segment_index] + node_tensions[segment_index + 1]
                        )
                for edge_index, spec in enumerate(rope_specs):
                    tension = segment_tensions[(spec["moordynLineId"], spec["segmentIndex"])]
                    frame_tensions[frame_index, edge_index] = tension
                    if spec["winchIndex"] >= 0:
                        frame_winch_tensions[frame_index, spec["winchIndex"]] += tension
                if not np.isfinite(node_positions[frame_index]).all() or not np.isfinite(frame_tensions[frame_index]).all():
                    raise RuntimeError(
                        f"MoorDyn produced non-finite positions or tensions at t={output_time:g} s; result was rejected."
                    )
                if frame_index > 0:
                    moving_body_z = [
                        node_positions[frame_index, node_index_by_id[node_id], 2] - initial_point_positions[node_id][2]
                        for node_id in point_node_ids
                        if node_id not in fixed_node_ids
                    ]
                    frame_lift[frame_index] = float(np.mean(moving_body_z)) if moving_body_z else 0.0
        finally:
            moordyn.Close(system)

    warnings.extend(model_data["warnings"])
    warnings.append(f"MoorDyn-C v2 implicit BEuler2 integration, dt={effective_step:.3e} s, {internal_steps:,} steps.")
    return {
        "settings": {
            "durationSeconds": duration_seconds,
            "frameIntervalSeconds": frame_interval_seconds,
            "integrationStepSeconds": effective_step,
            "internalSteps": internal_steps,
            "takeUpSpeedMPerMin": 1.0,
            "solverMethod": "moordyn",
            "membraneDynamics": include_membrane,
            "hydrodynamicDrag": True,
            "addedMass": True,
            "waterDepthM": water_depth_m,
        },
        "warnings": warnings,
        "ropeElements": [
            {"componentId": spec["componentId"], "componentName": spec["componentName"], "elementId": spec["elementId"]}
            for spec in rope_specs
        ],
        "frames": {
            "timeS": output_times,
            "takeUpM": output_times / 60.0,
            "translationZM": frame_lift,
            "winchTensionsN": frame_winch_tensions,
            "netVerticalLoadN": np.full(frame_count, model_data["netVerticalLoadN"], dtype=np.float64),
            "nodePositionsM": node_positions,
            "ropeTensionsN": frame_tensions,
        },
    }
