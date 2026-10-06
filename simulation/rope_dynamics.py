"""Lumped-mass, axial-rope dynamics for AquaSim truss networks."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from numba import njit

from .winch_solver import GRAVITY_M_S2, _beam_line_loads, _number, _read_model


@njit(cache=True)
def _rotation_increment(wx: float, wy: float, wz: float, dt: float) -> np.ndarray:
    angle = math.sqrt(wx * wx + wy * wy + wz * wz) * dt
    result = np.eye(3)
    if angle < 1e-14:
        result[0, 1] = -wz * dt
        result[0, 2] = wy * dt
        result[1, 0] = wz * dt
        result[1, 2] = -wx * dt
        result[2, 0] = -wy * dt
        result[2, 1] = wx * dt
        return result
    ux = wx / math.sqrt(wx * wx + wy * wy + wz * wz)
    uy = wy / math.sqrt(wx * wx + wy * wy + wz * wz)
    uz = wz / math.sqrt(wx * wx + wy * wy + wz * wz)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    one_minus = 1.0 - cosine
    result[0, 0] = cosine + ux * ux * one_minus
    result[0, 1] = ux * uy * one_minus - uz * sine
    result[0, 2] = ux * uz * one_minus + uy * sine
    result[1, 0] = uy * ux * one_minus + uz * sine
    result[1, 1] = cosine + uy * uy * one_minus
    result[1, 2] = uy * uz * one_minus - ux * sine
    result[2, 0] = uz * ux * one_minus - uy * sine
    result[2, 1] = uz * uy * one_minus + ux * sine
    result[2, 2] = cosine + uz * uz * one_minus
    return result


@njit(cache=True)
def _submerged_fraction(z0: float, z1: float) -> float:
    if z0 <= 0.0 and z1 <= 0.0:
        return 1.0
    if z0 >= 0.0 and z1 >= 0.0:
        return 0.0
    crossing = -z0 / (z1 - z0)
    if z0 < 0.0:
        return crossing
    return 1.0 - crossing


@njit(cache=True)
def _dynamic_kernel(
    initial_positions: np.ndarray,
    fixed_nodes: np.ndarray,
    node_body: np.ndarray,
    particle_mass: np.ndarray,
    initial_body_centers: np.ndarray,
    body_mass: np.ndarray,
    body_inertia: np.ndarray,
    body_inertia_inverse: np.ndarray,
    body_local_nodes: np.ndarray,
    edge_start: np.ndarray,
    edge_end: np.ndarray,
    edge_stiffness: np.ndarray,
    edge_rest_length: np.ndarray,
    edge_takeup_rate: np.ndarray,
    edge_tension_only: np.ndarray,
    edge_mass_air: np.ndarray,
    edge_mass_water: np.ndarray,
    beam_start: np.ndarray,
    beam_end: np.ndarray,
    beam_mass_air: np.ndarray,
    beam_mass_water: np.ndarray,
    point_forces: np.ndarray,
    point_moments: np.ndarray,
    edge_winch_index: np.ndarray,
    output_times: np.ndarray,
    step_seconds: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    node_count = initial_positions.shape[0]
    body_count = initial_body_centers.shape[0]
    edge_count = edge_start.size
    beam_edge_count = beam_start.size
    frame_count = output_times.size
    positions = initial_positions.copy()
    velocities = np.zeros_like(positions)
    body_centers = initial_body_centers.copy()
    body_velocities = np.zeros((body_count, 3))
    body_angular_velocities = np.zeros((body_count, 3))
    body_rotations = np.zeros((body_count, 3, 3))
    for body in range(body_count):
        for axis in range(3):
            body_rotations[body, axis, axis] = 1.0

    frame_positions = np.zeros((frame_count, node_count, 3), dtype=np.float32)
    frame_tensions = np.zeros((frame_count, edge_count), dtype=np.float32)
    frame_winch_tensions = np.zeros((frame_count, 2), dtype=np.float64)
    frame_lift = np.zeros(frame_count, dtype=np.float64)
    node_force = np.zeros((node_count, 3))
    body_force = np.zeros((body_count, 3))
    body_torque = np.zeros((body_count, 3))
    element_tension = np.zeros(edge_count)
    time_seconds = 0.0
    frame_index = 0
    maximum_time = output_times[-1]
    while frame_index < frame_count:
        next_output = output_times[frame_index]
        while time_seconds < next_output - 1e-12:
            dt = min(step_seconds, next_output - time_seconds)

            for node in range(node_count):
                body = node_body[node]
                if body >= 0:
                    rotation = body_rotations[body]
                    local = body_local_nodes[node]
                    for axis in range(3):
                        positions[node, axis] = body_centers[body, axis]
                        for local_axis in range(3):
                            positions[node, axis] += rotation[axis, local_axis] * local[local_axis]
                    rx = positions[node, 0] - body_centers[body, 0]
                    ry = positions[node, 1] - body_centers[body, 1]
                    rz = positions[node, 2] - body_centers[body, 2]
                    velocities[node, 0] = body_velocities[body, 0] + body_angular_velocities[body, 1] * rz - body_angular_velocities[body, 2] * ry
                    velocities[node, 1] = body_velocities[body, 1] + body_angular_velocities[body, 2] * rx - body_angular_velocities[body, 0] * rz
                    velocities[node, 2] = body_velocities[body, 2] + body_angular_velocities[body, 0] * ry - body_angular_velocities[body, 1] * rx

            for node in range(node_count):
                for axis in range(3):
                    node_force[node, axis] = point_forces[node, axis]
            for body in range(body_count):
                for axis in range(3):
                    body_force[body, axis] = 0.0
                    body_torque[body, axis] = 0.0

            for edge in range(edge_count):
                first = edge_start[edge]
                second = edge_end[edge]
                dx = positions[second, 0] - positions[first, 0]
                dy = positions[second, 1] - positions[first, 1]
                dz = positions[second, 2] - positions[first, 2]
                length = math.sqrt(dx * dx + dy * dy + dz * dz)
                if length <= 1e-10:
                    continue
                rest = edge_rest_length[edge] - edge_takeup_rate[edge] * time_seconds
                if rest <= 0.0:
                    rest = 1e-8
                tension = edge_stiffness[edge] * (length - rest)
                if edge_tension_only[edge] and tension < 0.0:
                    tension = 0.0
                element_tension[edge] = tension
                fx = tension * dx / length
                fy = tension * dy / length
                fz = tension * dz / length
                node_force[first, 0] += fx
                node_force[first, 1] += fy
                node_force[first, 2] += fz
                node_force[second, 0] -= fx
                node_force[second, 1] -= fy
                node_force[second, 2] -= fz

                fraction = _submerged_fraction(positions[first, 2], positions[second, 2])
                effective_mass = edge_mass_air[edge] * (1.0 - fraction) + edge_mass_water[edge] * fraction
                weight = effective_mass * length * 9.80665 * 0.5
                node_force[first, 2] -= weight
                node_force[second, 2] -= weight

            for edge in range(beam_edge_count):
                first = beam_start[edge]
                second = beam_end[edge]
                length = math.sqrt(
                    (positions[second, 0] - positions[first, 0]) ** 2
                    + (positions[second, 1] - positions[first, 1]) ** 2
                    + (positions[second, 2] - positions[first, 2]) ** 2
                )
                fraction = _submerged_fraction(positions[first, 2], positions[second, 2])
                effective_mass = beam_mass_air[edge] * (1.0 - fraction) + beam_mass_water[edge] * fraction
                weight = effective_mass * length * 9.80665 * 0.5
                node_force[first, 2] -= weight
                node_force[second, 2] -= weight

            for node in range(node_count):
                if fixed_nodes[node]:
                    continue
                body = node_body[node]
                if body >= 0:
                    for axis in range(3):
                        body_force[body, axis] += node_force[node, axis]
                    rx = positions[node, 0] - body_centers[body, 0]
                    ry = positions[node, 1] - body_centers[body, 1]
                    rz = positions[node, 2] - body_centers[body, 2]
                    body_torque[body, 0] += ry * node_force[node, 2] - rz * node_force[node, 1] + point_moments[node, 0]
                    body_torque[body, 1] += rz * node_force[node, 0] - rx * node_force[node, 2] + point_moments[node, 1]
                    body_torque[body, 2] += rx * node_force[node, 1] - ry * node_force[node, 0] + point_moments[node, 2]
                elif particle_mass[node] > 0.0:
                    inverse_mass = 1.0 / particle_mass[node]
                    for axis in range(3):
                        velocities[node, axis] += node_force[node, axis] * inverse_mass * dt
                        positions[node, axis] += velocities[node, axis] * dt

            for body in range(body_count):
                for axis in range(3):
                    body_velocities[body, axis] += body_force[body, axis] / body_mass[body] * dt
                    body_centers[body, axis] += body_velocities[body, axis] * dt
                rotation = body_rotations[body]
                inverse_inertia_world = np.zeros((3, 3))
                for row in range(3):
                    for column in range(3):
                        for local_row in range(3):
                            for local_column in range(3):
                                inverse_inertia_world[row, column] += (
                                    rotation[row, local_row]
                                    * body_inertia_inverse[body, local_row, local_column]
                                    * rotation[column, local_column]
                                )
                angular_velocity_local = rotation.T @ body_angular_velocities[body]
                angular_momentum_local = body_inertia[body] @ angular_velocity_local
                angular_momentum_world = rotation @ angular_momentum_local
                gyroscopic_moment = np.cross(body_angular_velocities[body], angular_momentum_world)
                angular_acceleration = inverse_inertia_world @ (body_torque[body] - gyroscopic_moment)
                for axis in range(3):
                    body_angular_velocities[body, axis] += angular_acceleration[axis] * dt
                increment = _rotation_increment(
                    body_angular_velocities[body, 0],
                    body_angular_velocities[body, 1],
                    body_angular_velocities[body, 2],
                    dt,
                )
                body_rotations[body] = increment @ body_rotations[body]

            time_seconds += dt

        for node in range(node_count):
            body = node_body[node]
            if body >= 0:
                rotation = body_rotations[body]
                local = body_local_nodes[node]
                for axis in range(3):
                    positions[node, axis] = body_centers[body, axis]
                    for local_axis in range(3):
                        positions[node, axis] += rotation[axis, local_axis] * local[local_axis]
                rx = positions[node, 0] - body_centers[body, 0]
                ry = positions[node, 1] - body_centers[body, 1]
                rz = positions[node, 2] - body_centers[body, 2]
                velocities[node, 0] = body_velocities[body, 0] + body_angular_velocities[body, 1] * rz - body_angular_velocities[body, 2] * ry
                velocities[node, 1] = body_velocities[body, 1] + body_angular_velocities[body, 2] * rx - body_angular_velocities[body, 0] * rz
                velocities[node, 2] = body_velocities[body, 2] + body_angular_velocities[body, 0] * ry - body_angular_velocities[body, 1] * rx

        for node in range(node_count):
            if fixed_nodes[node]:
                positions[node, 0] = initial_positions[node, 0]
                positions[node, 1] = initial_positions[node, 1]
                positions[node, 2] = initial_positions[node, 2]
                velocities[node, 0] = 0.0
                velocities[node, 1] = 0.0
                velocities[node, 2] = 0.0
        if frame_index < frame_count and abs(time_seconds - output_times[frame_index]) <= 1e-8:
            for node in range(node_count):
                for axis in range(3):
                    frame_positions[frame_index, node, axis] = positions[node, axis]
            for edge in range(edge_count):
                first = edge_start[edge]
                second = edge_end[edge]
                dx = positions[second, 0] - positions[first, 0]
                dy = positions[second, 1] - positions[first, 1]
                dz = positions[second, 2] - positions[first, 2]
                length = math.sqrt(dx * dx + dy * dy + dz * dz)
                rest = max(1e-8, edge_rest_length[edge] - edge_takeup_rate[edge] * time_seconds)
                tension = edge_stiffness[edge] * (length - rest)
                if edge_tension_only[edge] and tension < 0.0:
                    tension = 0.0
                frame_tensions[frame_index, edge] = tension
                if edge_winch_index[edge] >= 0:
                    frame_winch_tensions[frame_index, edge_winch_index[edge]] += tension
            for body in range(body_count):
                frame_lift[frame_index] += body_centers[body, 2] - initial_body_centers[body, 2]
            if body_count > 0:
                frame_lift[frame_index] /= body_count
            frame_index += 1

    return frame_positions, frame_tensions, frame_winch_tensions, frame_lift


def _line_length(nodes: dict[int, tuple[float, float, float]], first: int, second: int) -> float:
    return math.dist(nodes[first], nodes[second])


def _beam_groups(beams: list[ET.Element], node_id_to_index: dict[int, int]) -> list[list[int]]:
    parents = list(range(len(beams)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owner: dict[int, int] = {}
    for index, beam in enumerate(beams):
        for element in beam.findall(".//element"):
            for key in ("StartNode_ID", "EndNode_ID", "startNodeId", "endNodeId"):
                raw = element.get(key)
                if raw is None:
                    continue
                try:
                    node_index = node_id_to_index[int(raw)]
                except (ValueError, KeyError):
                    continue
                if node_index in owner:
                    parents[find(index)] = find(owner[node_index])
                else:
                    owner[node_index] = index
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(beams)):
        groups[find(index)].append(index)
    return list(groups.values())


def _connected_edge_groups(edges: list[dict[str, Any]]) -> list[list[int]]:
    adjacency: dict[int, list[int]] = defaultdict(list)
    for edge_index, edge in enumerate(edges):
        adjacency[edge["start"]].append(edge_index)
        adjacency[edge["end"]].append(edge_index)
    unseen = set(range(len(edges)))
    groups = []
    while unseen:
        first_edge = unseen.pop()
        component = [first_edge]
        stack = [edges[first_edge]["start"], edges[first_edge]["end"]]
        seen_nodes = set(stack)
        while stack:
            node = stack.pop()
            for edge_index in adjacency[node]:
                if edge_index in unseen:
                    unseen.remove(edge_index)
                    component.append(edge_index)
                    other = edges[edge_index]["end"] if edges[edge_index]["start"] == node else edges[edge_index]["start"]
                    if other not in seen_nodes:
                        seen_nodes.add(other)
                        stack.append(other)
        groups.append(component)
    return groups


def simulate_model(
    model_path: Path,
    water_density: float,
    duration_seconds: float,
    frame_interval_seconds: float,
    max_internal_steps: int,
) -> dict[str, Any]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Duration must be finite and greater than zero.")
    if not math.isfinite(frame_interval_seconds) or frame_interval_seconds <= 0:
        raise ValueError("Frame interval must be finite and greater than zero.")
    if max_internal_steps < 1:
        raise ValueError("Maximum internal steps must be greater than zero.")

    model_data = _read_model(model_path, water_density)
    root = ET.parse(model_path).getroot()
    raw_nodes = {
        int(node.get("id")): (float(node.get("x")), float(node.get("y")), float(node.get("z")))
        for node in root.findall("./Nodes/node")
    }
    node_ids = model_data["nodeIds"]
    node_index = {node_id: index for index, node_id in enumerate(node_ids)}
    initial_positions = np.asarray([raw_nodes[node_id] for node_id in node_ids], dtype=np.float64)
    node_count = len(node_ids)
    components = root.find("./Components")
    assert components is not None
    warnings_out = list(model_data["warnings"])

    beams = components.findall("./beam")
    beam_edges: list[tuple[int, int, float, float, int]] = []
    beam_body_nodes: list[set[int]] = []
    beam_node_owner: dict[int, int] = {}
    for beam_index, beam in enumerate(beams):
        this_nodes: set[int] = set()
        for element in beam.findall(".//element"):
            first = element.get("StartNode_ID") or element.get("startNodeId")
            second = element.get("EndNode_ID") or element.get("endNodeId")
            if first is None or second is None:
                continue
            try:
                a = node_index[int(first)]
                b = node_index[int(second)]
            except (ValueError, KeyError):
                continue
            this_nodes.update((a, b))
            beam_edges.append((a, b, 0.0, 0.0, beam_index))
        beam_body_nodes.append(this_nodes)

    beam_groups = _beam_groups(beams, node_index)
    fixed = np.zeros(node_count, dtype=np.bool_)
    for node_id in model_data["fixedNodeIds"]:
        fixed[node_index[node_id]] = True
    body_membership = np.full(node_count, -1, dtype=np.int64)
    body_centers: list[np.ndarray] = []
    body_masses: list[float] = []
    body_inertia: list[np.ndarray] = []
    body_inertia_inverse: list[np.ndarray] = []
    body_local_nodes = np.zeros((node_count, 3), dtype=np.float64)
    beam_component_body = np.full(len(beams), -1, dtype=np.int64)

    beam_line_loads = {
        int(load["componentId"]): (
            load["massPerMkg"],
            load["submergedMassPerMkg"],
        )
        for load in model_data["beamLoads"]
    }
    for group in beam_groups:
        group_nodes = set().union(*(beam_body_nodes[index] for index in group))
        is_fixed = any(fixed[node] for node in group_nodes)
        total_mass = 0.0
        weighted_center = np.zeros(3)
        segment_data = []
        for beam_index in group:
            beam_id = int(beams[beam_index].get("id", "0"))
            mass_per_m, submerged_per_m = beam_line_loads.get(beam_id, (0.0, 0.0))
            for a, b, _, _, owner in beam_edges:
                if owner != beam_index:
                    continue
                delta = initial_positions[b] - initial_positions[a]
                length = float(np.linalg.norm(delta))
                mass = mass_per_m * length
                center = (initial_positions[a] + initial_positions[b]) * 0.5
                segment_data.append((a, b, length, mass, mass_per_m, submerged_per_m, center, delta))
                total_mass += mass
                weighted_center += mass * center
        if is_fixed:
            for node in group_nodes:
                fixed[node] = True
            continue
        if total_mass <= 0:
            warnings_out.append(
                f"Beam rigid body {', '.join(str(beams[index].get('name')) for index in group)} has no positive mass and is held fixed."
            )
            for node in group_nodes:
                fixed[node] = True
            continue
        center = weighted_center / total_mass
        inertia = np.zeros((3, 3), dtype=np.float64)
        identity = np.eye(3)
        for _, _, length, mass, _, _, segment_center, delta in segment_data:
            if mass <= 0:
                continue
            inertia += mass / 12.0 * (length * length * identity - np.outer(delta, delta))
            offset = segment_center - center
            inertia += mass * (np.dot(offset, offset) * identity - np.outer(offset, offset))
        eigenvalues, eigenvectors = np.linalg.eigh(inertia)
        largest = max(float(np.max(eigenvalues)), 1e-9)
        eigenvalues = np.maximum(eigenvalues, largest * 1e-9)
        inverse_inertia = eigenvectors @ np.diag(1.0 / eigenvalues) @ eigenvectors.T
        body_index = len(body_centers)
        body_centers.append(center)
        body_masses.append(total_mass)
        body_inertia.append(inertia)
        body_inertia_inverse.append(inverse_inertia)
        for node in group_nodes:
            if body_membership[node] >= 0:
                raise ValueError("A node belongs to multiple independent moving rigid bodies.")
            body_membership[node] = body_index
            body_local_nodes[node] = initial_positions[node] - center
        for beam_index in group:
            beam_component_body[beam_index] = body_index

    line_edges: list[dict[str, Any]] = []
    winch_endpoint_ids: dict[int, set[int]] = {}
    winch_component_index: dict[int, int] = {}
    for winch_index, winch in enumerate(model_data["winches"]):
        winch_endpoint_ids[winch["componentId"]] = set(winch["topNodeIds"])
        winch_component_index[winch["componentId"]] = winch_index

    for component in components.findall("./truss"):
        mooring = component.find("mooring")
        if mooring is None:
            warnings_out.append(f"Truss {component.get('name', component.get('id', '?'))!r} has no mooring material; its dynamic rope elements are skipped.")
            continue
        area = _number(mooring, "areal", positive=True)
        young = _number(mooring, "young", positive=True)
        density = _number(mooring, "massdensity", positive=True)
        if area is None or young is None or density is None:
            if component.findall(".//element"):
                warnings_out.append(f"Truss {component.get('name', component.get('id', '?'))!r} lacks area, Young's modulus, or material density; its dynamic rope elements are skipped.")
            continue
        mass_air = _number(mooring, "weightInAir")
        if mass_air is None or mass_air <= 0:
            mass_air = density * area
        mass_water = _number(mooring, "weightInWater")
        if mass_water is None:
            mass_water = mass_air - water_density * area
        pretension = _number(mooring, "pretension") or 0.0
        if abs(pretension) > 1e-12:
            warning = f"Truss {component.get('name', component.get('id', '?'))!r} specifies pretension {pretension:g}; its units/reference are ambiguous, so dynamic initialization uses the undeformed geometry and ignores this value."
            if warning not in warnings_out:
                warnings_out.append(warning)
        load_model = component.find("loadmodel")
        if load_model is not None:
            damping = _number(load_model, "constructionDamping") or 0.0
            rayleigh = _number(load_model, "rayleighStiffness") or 0.0
            if abs(damping) > 1e-12 or abs(rayleigh) > 1e-12:
                warning = f"Truss {component.get('name', component.get('id', '?'))!r} has non-zero damping fields whose units are unsupported; dynamic damping is omitted."
                if warning not in warnings_out:
                    warnings_out.append(warning)
        component_id = int(component.get("id", "0"))
        for element in component.findall(".//element"):
            first = element.get("StartNode_ID") or element.get("startNodeId")
            second = element.get("EndNode_ID") or element.get("endNodeId")
            if first is None or second is None:
                continue
            try:
                a = node_index[int(first)]
                b = node_index[int(second)]
            except (ValueError, KeyError):
                continue
            length = float(np.linalg.norm(initial_positions[b] - initial_positions[a]))
            if length <= 1e-8:
                warnings_out.append(f"Truss element {element.get('id', '?')} in {component.get('name', '?')!r} has zero initial length and is omitted.")
                continue
            line_edges.append(
                {
                    "start": a,
                    "end": b,
                    "startNodeId": int(first),
                    "endNodeId": int(second),
                    "componentId": component_id,
                    "componentName": component.get("name", f"truss_{component_id}"),
                    "elementId": int(element.get("id", "0")),
                    "length": length,
                    "stiffness": young * area / length,
                    "tensionOnly": mooring.get("noCompressionForces", "true").casefold() == "true",
                    "massAir": mass_air,
                    "massWater": mass_water,
                    "winchIndex": (
                        winch_component_index.get(component_id, -1)
                        if int(first) in winch_endpoint_ids.get(component_id, set())
                        or int(second) in winch_endpoint_ids.get(component_id, set())
                        else -1
                    ),
                }
            )
    if not line_edges:
        raise ValueError("No truss elements have valid axial stiffness and mass properties.")

    for component_id, winch_index in winch_component_index.items():
        matching_indices = [index for index, edge in enumerate(line_edges) if edge["componentId"] == component_id]
        matching_edges = [line_edges[index] for index in matching_indices]
        take_up_rate = 1.0 / 60.0
        connected_groups = _connected_edge_groups(matching_edges)
        if not connected_groups:
            raise ValueError(f"Winch component {component_id} contains no valid rope paths.")
        for group in connected_groups:
            path_length = sum(matching_edges[index]["length"] for index in group)
            if path_length <= 0:
                continue
            for local_index in group:
                edge_index = matching_indices[local_index]
                line_edges[edge_index]["takeupRate"] = take_up_rate * line_edges[edge_index]["length"] / path_length
        if len(connected_groups) != len(winch_endpoint_ids[component_id]):
            warnings_out.append(
                f"Winch component {component_id} has {len(connected_groups)} disconnected rope paths and {len(winch_endpoint_ids[component_id])} reported top-end nodes; take-up is applied once per connected path."
            )

    particle_mass = np.zeros(node_count, dtype=np.float64)
    for edge in line_edges:
        segment_mass = edge["massAir"] * edge["length"] * 0.5
        particle_mass[edge["start"]] += segment_mass
        particle_mass[edge["end"]] += segment_mass
    for node in range(node_count):
        if fixed[node] or body_membership[node] >= 0:
            particle_mass[node] = 0.0

    node_forces = np.zeros((node_count, 3), dtype=np.float64)
    node_moments = np.zeros((node_count, 3), dtype=np.float64)
    for load in model_data["nodeLoads"]:
        node = node_index[load["nodeId"]]
        node_forces[node] += np.asarray(load["forceN"], dtype=np.float64)
        node_moments[node] += np.asarray(load["momentNm"], dtype=np.float64)

    edge_start = np.asarray([edge["start"] for edge in line_edges], dtype=np.int64)
    edge_end = np.asarray([edge["end"] for edge in line_edges], dtype=np.int64)
    edge_stiffness = np.asarray([edge["stiffness"] for edge in line_edges], dtype=np.float64)
    edge_rest_length = np.asarray([edge["length"] for edge in line_edges], dtype=np.float64)
    edge_takeup_rate = np.asarray([edge.get("takeupRate", 0.0) for edge in line_edges], dtype=np.float64)
    edge_tension_only = np.asarray([edge["tensionOnly"] for edge in line_edges], dtype=np.bool_)
    edge_mass_air = np.asarray([edge["massAir"] for edge in line_edges], dtype=np.float64)
    edge_mass_water = np.asarray([edge["massWater"] for edge in line_edges], dtype=np.float64)
    edge_winch_index = np.asarray([edge["winchIndex"] for edge in line_edges], dtype=np.int64)

    beam_mass_air = []
    beam_mass_water = []
    beam_start = []
    beam_end = []
    beam_output_edges = []
    for beam_index, beam in enumerate(beams):
        beam_id = int(beam.get("id", "0"))
        mass_air, mass_water = beam_line_loads.get(beam_id, (0.0, 0.0))
        for element in beam.findall(".//element"):
            first = element.get("StartNode_ID") or element.get("startNodeId")
            second = element.get("EndNode_ID") or element.get("endNodeId")
            if first is None or second is None:
                continue
            try:
                a = node_index[int(first)]
                b = node_index[int(second)]
            except (ValueError, KeyError):
                continue
            beam_start.append(a)
            beam_end.append(b)
            beam_mass_air.append(mass_air)
            beam_mass_water.append(mass_water)
            beam_output_edges.append((a, b, beam_index))
    beam_start_array = np.asarray(beam_start, dtype=np.int64)
    beam_end_array = np.asarray(beam_end, dtype=np.int64)

    effective_mass = particle_mass.copy()
    for node in range(node_count):
        body = body_membership[node]
        if body >= 0:
            body_node_count = int(np.count_nonzero(body_membership == body))
            effective_mass[node] = body_masses[body] / max(1, body_node_count)
    stable_dt = math.inf
    for edge in line_edges:
        first_mass = effective_mass[edge["start"]]
        second_mass = effective_mass[edge["end"]]
        if first_mass <= 0 and second_mass <= 0:
            continue
        reduced_mass = (
            second_mass
            if first_mass <= 0
            else first_mass
            if second_mass <= 0
            else first_mass * second_mass / (first_mass + second_mass)
        )
        candidate = 0.25 * math.sqrt(reduced_mass / edge["stiffness"])
        stable_dt = min(stable_dt, candidate)
    if not math.isfinite(stable_dt) or stable_dt <= 0:
        raise ValueError("Could not derive a stable dynamic timestep from rope masses and axial stiffness.")

    frame_count = math.ceil(duration_seconds / frame_interval_seconds) + 1
    output_times = np.minimum(np.arange(frame_count, dtype=np.float64) * frame_interval_seconds, duration_seconds)
    output_times[-1] = duration_seconds
    internal_steps = math.ceil(duration_seconds / stable_dt)
    if internal_steps > max_internal_steps:
        raise ValueError(
            f"Model rope stiffness requires an estimated {stable_dt:.2e} s explicit timestep "
            f"({internal_steps:,} steps for {duration_seconds:g} s), above --max-internal-steps "
            f"({max_internal_steps:,}). Use a shorter duration or raise the limit."
        )

    dynamic = _dynamic_kernel(
        initial_positions,
        fixed,
        body_membership,
        particle_mass,
        np.asarray(body_centers, dtype=np.float64).reshape((-1, 3)),
        np.asarray(body_masses, dtype=np.float64),
        np.asarray(body_inertia, dtype=np.float64).reshape((-1, 3, 3)),
        np.asarray(body_inertia_inverse, dtype=np.float64).reshape((-1, 3, 3)),
        body_local_nodes,
        edge_start,
        edge_end,
        edge_stiffness,
        edge_rest_length,
        edge_takeup_rate,
        edge_tension_only,
        edge_mass_air,
        edge_mass_water,
        beam_start_array,
        beam_end_array,
        np.asarray(beam_mass_air, dtype=np.float64),
        np.asarray(beam_mass_water, dtype=np.float64),
        node_forces,
        node_moments,
        edge_winch_index,
        output_times,
        stable_dt,
    )
    frame_positions, frame_tensions, winch_tensions, frame_lift = dynamic
    frame_net_vertical = np.zeros(frame_count, dtype=np.float64)
    applied_point_load_z = sum(load["forceN"][2] for load in model_data["nodeLoads"])
    for frame_index, current_positions in enumerate(frame_positions):
        net_load = -applied_point_load_z
        for edge_index, edge in enumerate(line_edges):
            first_z = float(current_positions[edge["start"], 2])
            second_z = float(current_positions[edge["end"], 2])
            length = float(np.linalg.norm(current_positions[edge["end"]] - current_positions[edge["start"]]))
            submerged = 1.0 if first_z <= 0 and second_z <= 0 else 0.0 if first_z >= 0 and second_z >= 0 else (
                -first_z / (second_z - first_z) if first_z < 0 else 1.0 + first_z / (second_z - first_z)
            )
            effective_mass = edge["massAir"] * (1.0 - submerged) + edge["massWater"] * submerged
            net_load += effective_mass * length * GRAVITY_M_S2
        for beam_index, edge in enumerate(beam_output_edges):
            first, second, owner = edge
            first_z = float(current_positions[first, 2])
            second_z = float(current_positions[second, 2])
            length = float(np.linalg.norm(current_positions[second] - current_positions[first]))
            submerged = 1.0 if first_z <= 0 and second_z <= 0 else 0.0 if first_z >= 0 and second_z >= 0 else (
                -first_z / (second_z - first_z) if first_z < 0 else 1.0 + first_z / (second_z - first_z)
            )
            effective_mass = beam_mass_air[beam_index] * (1.0 - submerged) + beam_mass_water[beam_index] * submerged
            net_load += effective_mass * length * GRAVITY_M_S2
        frame_net_vertical[frame_index] = net_load
    settings = {
        "durationSeconds": duration_seconds,
        "frameIntervalSeconds": frame_interval_seconds,
        "integrationStepSeconds": stable_dt,
        "internalSteps": internal_steps,
        "takeUpSpeedMPerMin": 1.0,
        "membraneDynamics": False,
        "hydrodynamicDrag": False,
        "addedMass": False,
    }
    result_warnings = warnings_out + [
        f"Explicit rope timestep selected from axial stiffness and lumped mass: {stable_dt:.3e} s ({internal_steps:,} internal steps).",
        "Rope initial pretension values are ignored because their units/reference length are unspecified; each rope starts stress-free at its input geometry.",
        "Membranes are excluded from dynamic forces and motion.",
        "Hydrodynamic drag, added mass, waves, and currents are omitted as requested for the 1 m/min lift.",
        "Rope damping is zero because the supported model damping coefficients are zero; non-zero damping parameters are reported and not interpreted.",
    ]
    return {
        "settings": settings,
        "warnings": result_warnings,
        "ropeElements": [
            {"componentId": edge["componentId"], "componentName": edge["componentName"], "elementId": edge["elementId"]}
            for edge in line_edges
        ],
        "frames": {
            "timeS": output_times,
            "takeUpM": output_times / 60.0,
            "translationZM": frame_lift,
            "winchTensionsN": winch_tensions,
            "netVerticalLoadN": frame_net_vertical,
            "nodePositionsM": frame_positions,
            "ropeTensionsN": frame_tensions,
        },
    }
