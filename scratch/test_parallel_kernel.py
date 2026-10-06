import time
import math
import numpy as np
from numba import njit, prange
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from simulation.winch_solver import GRAVITY_M_S2, _beam_line_loads, _number, _read_model
from simulation.rope_dynamics import _beam_groups, _connected_edge_groups, quat_to_matrix, quat_normalize, quat_mult, _cross

def color_edges(edges, node_count):
    node_colors = defaultdict(set)
    edge_colors = []
    for u, v in edges:
        c = 0
        used = node_colors[u].union(node_colors[v])
        while c in used:
            c += 1
        edge_colors.append(c)
        node_colors[u].add(c)
        node_colors[v].add(c)
    num_colors = max(edge_colors) + 1 if edge_colors else 0
    
    # Sort edges by color
    color_groups = [[] for _ in range(num_colors)]
    for idx, c in enumerate(edge_colors):
        color_groups[c].append(idx)
        
    offsets = []
    counts = []
    sorted_indices = []
    curr = 0
    for g in color_groups:
        offsets.append(curr)
        counts.append(len(g))
        sorted_indices.extend(g)
        curr += len(g)
        
    return np.asarray(offsets, dtype=np.int64), np.asarray(counts, dtype=np.int64), np.asarray(sorted_indices, dtype=np.int64), num_colors

@njit(parallel=True, cache=True)
def _parallel_xpbd_kernel(
    initial_positions: np.ndarray,
    fixed_nodes: np.ndarray,
    node_body: np.ndarray,
    node_inv_mass: np.ndarray,
    node_external_force: np.ndarray,
    initial_body_centers: np.ndarray,
    body_mass: np.ndarray,
    body_inv_mass: np.ndarray,
    body_inertia: np.ndarray,
    body_inertia_inv: np.ndarray,
    body_external_force: np.ndarray,
    body_local_nodes: np.ndarray,
    edge_start: np.ndarray,
    edge_end: np.ndarray,
    edge_stiffness: np.ndarray,
    edge_rest_length: np.ndarray,
    edge_takeup_rate: np.ndarray,
    edge_compliance: np.ndarray,
    edge_tension_only: np.ndarray,
    edge_winch_index: np.ndarray,
    mem_edge_start: np.ndarray,
    mem_edge_end: np.ndarray,
    mem_edge_rest_length: np.ndarray,
    mem_edge_compliance: np.ndarray,
    mem_color_offsets: np.ndarray,
    mem_color_counts: np.ndarray,
    mem_sorted_indices: np.ndarray,
    output_times: np.ndarray,
    step_seconds: float,
    iterations: int,
    drag_coeff: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    node_count = initial_positions.shape[0]
    body_count = initial_body_centers.shape[0]
    rope_count = edge_start.size
    mem_count = mem_edge_start.size
    frame_count = output_times.size
    num_mem_colors = mem_color_offsets.size

    positions = initial_positions.copy()
    velocities = np.zeros_like(positions)
    body_centers = initial_body_centers.copy()
    body_velocities = np.zeros((body_count, 3))
    body_angular_vel = np.zeros((body_count, 3))
    body_quats = np.zeros((body_count, 4))
    for b in range(body_count):
        body_quats[b, 3] = 1.0

    frame_positions = np.zeros((frame_count, node_count, 3), dtype=np.float32)
    frame_tensions = np.zeros((frame_count, rope_count), dtype=np.float32)
    frame_winch_tensions = np.zeros((frame_count, 2), dtype=np.float64)
    frame_lift = np.zeros(frame_count, dtype=np.float64)

    rope_lambdas = np.zeros(rope_count)
    mem_lambdas = np.zeros(mem_count)

    time_seconds = 0.0
    frame_index = 0
    drag_factor = min(0.99, max(0.0, drag_coeff * step_seconds))

    pos_pred = np.zeros_like(positions)

    while frame_index < frame_count:
        next_output = output_times[frame_index]
        while time_seconds < next_output - 1e-12:
            dt = min(step_seconds, next_output - time_seconds)
            dt2 = dt * dt
            inv_dt = 1.0 / dt
            inv_dt2 = 1.0 / dt2

            # 1. Parallel external forces & position prediction for free nodes
            for i in prange(node_count):
                if fixed_nodes[i] or node_body[i] >= 0:
                    pos_pred[i, 0] = positions[i, 0]
                    pos_pred[i, 1] = positions[i, 1]
                    pos_pred[i, 2] = positions[i, 2]
                else:
                    w = node_inv_mass[i]
                    if w > 0.0:
                        v0 = (velocities[i, 0] + dt * w * node_external_force[i, 0]) * (1.0 - drag_factor)
                        v1 = (velocities[i, 1] + dt * w * node_external_force[i, 1]) * (1.0 - drag_factor)
                        v2 = (velocities[i, 2] + dt * w * node_external_force[i, 2]) * (1.0 - drag_factor)
                        velocities[i, 0] = v0
                        velocities[i, 1] = v1
                        velocities[i, 2] = v2
                        pos_pred[i, 0] = positions[i, 0] + dt * v0
                        pos_pred[i, 1] = positions[i, 1] + dt * v1
                        pos_pred[i, 2] = positions[i, 2] + dt * v2
                    else:
                        pos_pred[i, 0] = positions[i, 0]
                        pos_pred[i, 1] = positions[i, 1]
                        pos_pred[i, 2] = positions[i, 2]

            body_centers_pred = body_centers.copy()
            body_quats_pred = body_quats.copy()
            body_R_pred = np.zeros((body_count, 3, 3))
            body_I_inv_world = np.zeros((body_count, 3, 3))

            for b in range(body_count):
                w = body_inv_mass[b]
                body_velocities[b, 0] = (body_velocities[b, 0] + dt * w * body_external_force[b, 0]) * (1.0 - drag_factor)
                body_velocities[b, 1] = (body_velocities[b, 1] + dt * w * body_external_force[b, 1]) * (1.0 - drag_factor)
                body_velocities[b, 2] = (body_velocities[b, 2] + dt * w * body_external_force[b, 2]) * (1.0 - drag_factor)
                body_centers_pred[b, 0] += dt * body_velocities[b, 0]
                body_centers_pred[b, 1] += dt * body_velocities[b, 1]
                body_centers_pred[b, 2] += dt * body_velocities[b, 2]

                body_angular_vel[b, 0] *= (1.0 - drag_factor)
                body_angular_vel[b, 1] *= (1.0 - drag_factor)
                body_angular_vel[b, 2] *= (1.0 - drag_factor)

                w_rot = np.array([
                    body_angular_vel[b, 0] * 0.5 * dt,
                    body_angular_vel[b, 1] * 0.5 * dt,
                    body_angular_vel[b, 2] * 0.5 * dt,
                    0.0
                ])
                q_step = quat_mult(w_rot, body_quats[b])
                q_new = body_quats[b] + q_step
                body_quats_pred[b] = quat_normalize(q_new)

                R = quat_to_matrix(body_quats_pred[b])
                body_R_pred[b] = R
                body_I_inv_world[b] = R @ body_inertia_inv[b] @ R.T

            # Project body nodes to world space
            for i in range(node_count):
                b = node_body[i]
                if b >= 0:
                    local = body_local_nodes[i]
                    R = body_R_pred[b]
                    pos_pred[i, 0] = body_centers_pred[b, 0] + R[0, 0]*local[0] + R[0, 1]*local[1] + R[0, 2]*local[2]
                    pos_pred[i, 1] = body_centers_pred[b, 1] + R[1, 0]*local[0] + R[1, 1]*local[1] + R[1, 2]*local[2]
                    pos_pred[i, 2] = body_centers_pred[b, 2] + R[2, 0]*local[0] + R[2, 1]*local[1] + R[2, 2]*local[2]

            # 2. XPBD Constraint Solver Loop
            rope_lambdas[:] = 0.0
            mem_lambdas[:] = 0.0

            for it in range(iterations):
                # Ropes
                for e in range(rope_count):
                    i1 = edge_start[e]
                    i2 = edge_end[e]
                    dx = pos_pred[i2, 0] - pos_pred[i1, 0]
                    dy = pos_pred[i2, 1] - pos_pred[i1, 1]
                    dz = pos_pred[i2, 2] - pos_pred[i1, 2]
                    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                    if dist < 1e-9:
                        continue

                    nx = dx / dist
                    ny = dy / dist
                    nz = dz / dist
                    n = np.array([nx, ny, nz])

                    rest = max(1e-4, edge_rest_length[e] - edge_takeup_rate[e] * time_seconds)
                    C = dist - rest
                    if edge_tension_only[e] and C <= 0.0 and rope_lambdas[e] <= 0.0:
                        continue

                    alpha_tilde = edge_compliance[e] * inv_dt2

                    w1 = 0.0
                    b1 = node_body[i1]
                    if not fixed_nodes[i1]:
                        if b1 >= 0:
                            r1 = pos_pred[i1] - body_centers_pred[b1]
                            u1 = _cross(r1, n)
                            w1 = body_inv_mass[b1] + float(u1 @ body_I_inv_world[b1] @ u1)
                        else:
                            w1 = node_inv_mass[i1]

                    w2 = 0.0
                    b2 = node_body[i2]
                    if not fixed_nodes[i2]:
                        if b2 >= 0:
                            r2 = pos_pred[i2] - body_centers_pred[b2]
                            u2 = _cross(r2, -n)
                            w2 = body_inv_mass[b2] + float(u2 @ body_I_inv_world[b2] @ u2)
                        else:
                            w2 = node_inv_mass[i2]

                    w_sum = w1 + w2 + alpha_tilde
                    if w_sum < 1e-14:
                        continue

                    d_lambda = (-C - alpha_tilde * rope_lambdas[e]) / w_sum
                    if edge_tension_only[e]:
                        new_lambda = max(0.0, rope_lambdas[e] + d_lambda)
                        d_lambda = new_lambda - rope_lambdas[e]
                        rope_lambdas[e] = new_lambda
                    else:
                        rope_lambdas[e] += d_lambda

                    px = d_lambda * nx
                    py = d_lambda * ny
                    pz = d_lambda * nz
                    p = np.array([px, py, pz])

                    if not fixed_nodes[i1]:
                        if b1 >= 0:
                            body_centers_pred[b1] -= body_inv_mass[b1] * p
                            r1 = pos_pred[i1] - body_centers_pred[b1]
                            d_theta1 = body_I_inv_world[b1] @ _cross(r1, -p)
                            q_step = np.array([d_theta1[0]*0.5, d_theta1[1]*0.5, d_theta1[2]*0.5, 0.0])
                            body_quats_pred[b1] = quat_normalize(body_quats_pred[b1] + quat_mult(q_step, body_quats_pred[b1]))
                            R = quat_to_matrix(body_quats_pred[b1])
                            body_R_pred[b1] = R
                            body_I_inv_world[b1] = R @ body_inertia_inv[b1] @ R.T
                        else:
                            pos_pred[i1, 0] -= node_inv_mass[i1] * px
                            pos_pred[i1, 1] -= node_inv_mass[i1] * py
                            pos_pred[i1, 2] -= node_inv_mass[i1] * pz

                    if not fixed_nodes[i2]:
                        if b2 >= 0:
                            body_centers_pred[b2] += body_inv_mass[b2] * p
                            r2 = pos_pred[i2] - body_centers_pred[b2]
                            d_theta2 = body_I_inv_world[b2] @ _cross(r2, p)
                            q_step = np.array([d_theta2[0]*0.5, d_theta2[1]*0.5, d_theta2[2]*0.5, 0.0])
                            body_quats_pred[b2] = quat_normalize(body_quats_pred[b2] + quat_mult(q_step, body_quats_pred[b2]))
                            R = quat_to_matrix(body_quats_pred[b2])
                            body_R_pred[b2] = R
                            body_I_inv_world[b2] = R @ body_inertia_inv[b2] @ R.T
                        else:
                            pos_pred[i2, 0] += node_inv_mass[i2] * px
                            pos_pred[i2, 1] += node_inv_mass[i2] * py
                            pos_pred[i2, 2] += node_inv_mass[i2] * pz

                    if b1 >= 0:
                        R = body_R_pred[b1]
                        local = body_local_nodes[i1]
                        pos_pred[i1, 0] = body_centers_pred[b1, 0] + R[0, 0]*local[0] + R[0, 1]*local[1] + R[0, 2]*local[2]
                        pos_pred[i1, 1] = body_centers_pred[b1, 1] + R[1, 0]*local[0] + R[1, 1]*local[1] + R[1, 2]*local[2]
                        pos_pred[i1, 2] = body_centers_pred[b1, 2] + R[2, 0]*local[0] + R[2, 1]*local[1] + R[2, 2]*local[2]
                    if b2 >= 0:
                        R = body_R_pred[b2]
                        local = body_local_nodes[i2]
                        pos_pred[i2, 0] = body_centers_pred[b2, 0] + R[0, 0]*local[0] + R[0, 1]*local[1] + R[0, 2]*local[2]
                        pos_pred[i2, 1] = body_centers_pred[b2, 1] + R[1, 0]*local[0] + R[1, 1]*local[1] + R[1, 2]*local[2]
                        pos_pred[i2, 2] = body_centers_pred[b2, 2] + R[2, 0]*local[0] + R[2, 1]*local[1] + R[2, 2]*local[2]

                # Membrane (net) constraints solved in PARALLEL by graph color groups!
                for c in range(num_mem_colors):
                    c_start = mem_color_offsets[c]
                    c_count = mem_color_counts[c]
                    for idx in prange(c_count):
                        e = mem_sorted_indices[c_start + idx]
                        i1 = mem_edge_start[e]
                        i2 = mem_edge_end[e]
                        dx = pos_pred[i2, 0] - pos_pred[i1, 0]
                        dy = pos_pred[i2, 1] - pos_pred[i1, 1]
                        dz = pos_pred[i2, 2] - pos_pred[i1, 2]
                        dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                        if dist < 1e-9:
                            continue

                        nx = dx / dist
                        ny = dy / dist
                        nz = dz / dist

                        C = dist - mem_edge_rest_length[e]
                        if C <= 0.0 and mem_lambdas[e] <= 0.0:
                            continue

                        alpha_tilde = mem_edge_compliance[e] * inv_dt2

                        w1 = 0.0 if fixed_nodes[i1] or node_body[i1] >= 0 else node_inv_mass[i1]
                        w2 = 0.0 if fixed_nodes[i2] or node_body[i2] >= 0 else node_inv_mass[i2]

                        w_sum = w1 + w2 + alpha_tilde
                        if w_sum < 1e-14:
                            continue

                        d_lambda = (-C - alpha_tilde * mem_lambdas[e]) / w_sum
                        new_lambda = max(0.0, mem_lambdas[e] + d_lambda)
                        d_lambda = new_lambda - mem_lambdas[e]
                        mem_lambdas[e] = new_lambda

                        px = d_lambda * nx
                        py = d_lambda * ny
                        pz = d_lambda * nz

                        if not fixed_nodes[i1] and node_body[i1] < 0:
                            pos_pred[i1, 0] -= w1 * px
                            pos_pred[i1, 1] -= w1 * py
                            pos_pred[i1, 2] -= w1 * pz
                        if not fixed_nodes[i2] and node_body[i2] < 0:
                            pos_pred[i2, 0] += w2 * px
                            pos_pred[i2, 1] += w2 * py
                            pos_pred[i2, 2] += w2 * pz

            # 3. Parallel velocity update and position commit
            for i in prange(node_count):
                if fixed_nodes[i]:
                    pos_pred[i, 0] = initial_positions[i, 0]
                    pos_pred[i, 1] = initial_positions[i, 1]
                    pos_pred[i, 2] = initial_positions[i, 2]
                    velocities[i, 0] = 0.0
                    velocities[i, 1] = 0.0
                    velocities[i, 2] = 0.0
                elif node_body[i] < 0:
                    velocities[i, 0] = (pos_pred[i, 0] - positions[i, 0]) * inv_dt
                    velocities[i, 1] = (pos_pred[i, 1] - positions[i, 1]) * inv_dt
                    velocities[i, 2] = (pos_pred[i, 2] - positions[i, 2]) * inv_dt
                    positions[i, 0] = pos_pred[i, 0]
                    positions[i, 1] = pos_pred[i, 1]
                    positions[i, 2] = pos_pred[i, 2]

            for b in range(body_count):
                body_velocities[b, 0] = (body_centers_pred[b, 0] - body_centers[b, 0]) * inv_dt
                body_velocities[b, 1] = (body_centers_pred[b, 1] - body_centers[b, 1]) * inv_dt
                body_velocities[b, 2] = (body_centers_pred[b, 2] - body_centers[b, 2]) * inv_dt
                body_centers[b, 0] = body_centers_pred[b, 0]
                body_centers[b, 1] = body_centers_pred[b, 1]
                body_centers[b, 2] = body_centers_pred[b, 2]

                q_prev = body_quats[b]
                q_cur = body_quats_pred[b]
                q_diff = quat_mult(q_cur, np.array([-q_prev[0], -q_prev[1], -q_prev[2], q_prev[3]]))
                body_angular_vel[b, 0] = 2.0 * q_diff[0] * inv_dt
                body_angular_vel[b, 1] = 2.0 * q_diff[1] * inv_dt
                body_angular_vel[b, 2] = 2.0 * q_diff[2] * inv_dt
                body_quats[b] = q_cur

            # Update body nodes world positions
            for i in range(node_count):
                b = node_body[i]
                if b >= 0:
                    local = body_local_nodes[i]
                    R = quat_to_matrix(body_quats[b])
                    r_world = np.array([
                        R[0, 0]*local[0] + R[0, 1]*local[1] + R[0, 2]*local[2],
                        R[1, 0]*local[0] + R[1, 1]*local[1] + R[1, 2]*local[2],
                        R[2, 0]*local[0] + R[2, 1]*local[1] + R[2, 2]*local[2]
                    ])
                    positions[i, 0] = body_centers[b, 0] + r_world[0]
                    positions[i, 1] = body_centers[b, 1] + r_world[1]
                    positions[i, 2] = body_centers[b, 2] + r_world[2]
                    v_rot = _cross(body_angular_vel[b], r_world)
                    velocities[i, 0] = body_velocities[b, 0] + v_rot[0]
                    velocities[i, 1] = body_velocities[b, 1] + v_rot[1]
                    velocities[i, 2] = body_velocities[b, 2] + v_rot[2]

            time_seconds += dt

        # Record output frames
        if frame_index < frame_count and abs(time_seconds - output_times[frame_index]) <= 1e-8:
            for i in prange(node_count):
                frame_positions[frame_index, i, 0] = positions[i, 0]
                frame_positions[frame_index, i, 1] = positions[i, 1]
                frame_positions[frame_index, i, 2] = positions[i, 2]

            for e in range(rope_count):
                first = edge_start[e]
                second = edge_end[e]
                dx = positions[second, 0] - positions[first, 0]
                dy = positions[second, 1] - positions[first, 1]
                dz = positions[second, 2] - positions[first, 2]
                length = math.sqrt(dx*dx + dy*dy + dz*dz)
                rest = max(1e-8, edge_rest_length[e] - edge_takeup_rate[e] * time_seconds)
                geom_tension = edge_stiffness[e] * (length - rest)
                xpbd_tension = rope_lambdas[e] / (step_seconds * step_seconds)
                tension = max(geom_tension, xpbd_tension)
                if edge_tension_only[e] and tension < 0.0:
                    tension = 0.0
                frame_tensions[frame_index, e] = tension
                w_idx = edge_winch_index[e]
                if w_idx >= 0:
                    frame_winch_tensions[frame_index, w_idx] += tension

            lift = 0.0
            for b in range(body_count):
                lift += body_centers[b, 2] - initial_body_centers[b, 2]
            if body_count > 0:
                lift /= body_count
            frame_lift[frame_index] = lift

            frame_index += 1

    return frame_positions, frame_tensions, frame_winch_tensions, frame_lift

def benchmark_parallel():
    model_path = Path("public/Next_Otervika_NLS_AI.amodel")
    water_density = 1025.0
    duration_seconds = 10.0
    frame_interval_seconds = 1.0
    step_seconds = 0.05
    iterations = 2
    drag_coeff = 2.0

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

    beams = components.findall("./beam")
    beam_edges = []
    beam_body_nodes = []
    for beam_index, beam in enumerate(beams):
        this_nodes = set()
        for element in beam.findall(".//element"):
            first = element.get("StartNode_ID") or element.get("startNodeId")
            second = element.get("EndNode_ID") or element.get("endNodeId")
            if first is None or second is None: continue
            try:
                a = node_index[int(first)]
                b = node_index[int(second)]
            except (ValueError, KeyError): continue
            this_nodes.update((a, b))
            beam_edges.append((a, b, beam_index))
        beam_body_nodes.append(this_nodes)

    beam_groups = _beam_groups(beams, node_index)
    fixed = np.zeros(node_count, dtype=np.bool_)
    for node_id in model_data["fixedNodeIds"]:
        fixed[node_index[node_id]] = True

    body_membership = np.full(node_count, -1, dtype=np.int64)
    body_centers = []
    body_masses = []
    body_inertia = []
    body_inertia_inv = []
    body_submerged_weights = []
    body_local_nodes = np.zeros((node_count, 3), dtype=np.float64)

    beam_line_loads = {
        int(load["componentId"]): (load["massPerMkg"], load["submergedMassPerMkg"])
        for load in model_data["beamLoads"]
    }
    for group in beam_groups:
        group_nodes = set().union(*(beam_body_nodes[index] for index in group))
        is_fixed = any(fixed[node] for node in group_nodes)
        total_mass = 0.0
        total_submerged_weight = 0.0
        weighted_center = np.zeros(3)
        segment_data = []
        for beam_index in group:
            beam_id = int(beams[beam_index].get("id", "0"))
            mass_per_m, submerged_per_m = beam_line_loads.get(beam_id, (0.0, 0.0))
            for a, b, owner in beam_edges:
                if owner != beam_index: continue
                delta = initial_positions[b] - initial_positions[a]
                length = float(np.linalg.norm(delta))
                mass = mass_per_m * length
                center = (initial_positions[a] + initial_positions[b]) * 0.5
                segment_data.append((length, mass, center, delta))
                total_mass += mass
                weighted_center += mass * center
                total_submerged_weight += submerged_per_m * length * GRAVITY_M_S2
        if is_fixed:
            for node in group_nodes: fixed[node] = True
            continue
        if total_mass <= 0:
            for node in group_nodes: fixed[node] = True
            continue
        center = weighted_center / total_mass
        inertia = np.zeros((3, 3), dtype=np.float64)
        identity = np.eye(3)
        for length, mass, segment_center, delta in segment_data:
            if mass <= 0: continue
            inertia += mass / 12.0 * (length*length*identity - np.outer(delta, delta))
            offset = segment_center - center
            inertia += mass * (np.dot(offset, offset)*identity - np.outer(offset, offset))
        eigenvalues, eigenvectors = np.linalg.eigh(inertia)
        largest = max(float(np.max(eigenvalues)), 1e-9)
        eigenvalues = np.maximum(eigenvalues, largest * 1e-9)
        inv_inertia = eigenvectors @ np.diag(1.0 / eigenvalues) @ eigenvectors.T
        b_idx = len(body_centers)
        body_centers.append(center)
        body_masses.append(total_mass)
        body_inertia.append(inertia)
        body_inertia_inv.append(inv_inertia)
        body_submerged_weights.append(total_submerged_weight)
        for node in group_nodes:
            body_membership[node] = b_idx
            body_local_nodes[node] = initial_positions[node] - center

    line_edges = []
    particle_mass = np.zeros(node_count, dtype=np.float64)
    particle_submerged_force = np.zeros((node_count, 3), dtype=np.float64)
    winch_endpoint_ids = {}
    winch_component_index = {}
    for w_idx, winch in enumerate(model_data["winches"]):
        winch_endpoint_ids[winch["componentId"]] = set(winch["topNodeIds"])
        winch_component_index[winch["componentId"]] = w_idx

    for component in components.findall("./truss"):
        mooring = component.find("mooring")
        if mooring is None: continue
        area = _number(mooring, "areal", positive=True)
        young = _number(mooring, "young", positive=True)
        density = _number(mooring, "massdensity", positive=True)
        if area is None or young is None or density is None: continue
        mass_air = _number(mooring, "weightInAir") or density * area
        mass_water = _number(mooring, "weightInWater") or (mass_air - water_density * area)
        cid = int(component.get("id", "0"))
        for el in component.findall(".//element"):
            first = el.get("StartNode_ID") or el.get("startNodeId")
            second = el.get("EndNode_ID") or el.get("endNodeId")
            if first is None or second is None: continue
            try:
                a = node_index[int(first)]
                b = node_index[int(second)]
            except (ValueError, KeyError): continue
            length = float(np.linalg.norm(initial_positions[b] - initial_positions[a]))
            if length <= 1e-8: continue
            w_idx = winch_component_index.get(cid, -1) if (int(first) in winch_endpoint_ids.get(cid, set()) or int(second) in winch_endpoint_ids.get(cid, set())) else -1
            line_edges.append({
                "start": a,
                "end": b,
                "length": length,
                "stiffness": young * area / length,
                "compliance": length / (young * area),
                "tensionOnly": mooring.get("noCompressionForces", "true").casefold() == "true",
                "winchIndex": w_idx,
            })
            particle_mass[a] += mass_air * length * 0.5
            particle_mass[b] += mass_air * length * 0.5
            sub_weight = mass_water * length * 0.5 * GRAVITY_M_S2
            particle_submerged_force[a, 2] -= sub_weight
            particle_submerged_force[b, 2] -= sub_weight

    # Winch takeup
    for cid, w_idx in winch_component_index.items():
        matching = [i for i, e in enumerate(line_edges) if e["winchIndex"] == w_idx]
        m_edges = [line_edges[i] for i in matching]
        groups = _connected_edge_groups(m_edges)
        for g in groups:
            p_len = sum(m_edges[i]["length"] for i in g)
            if p_len <= 0: continue
            for local_idx in g:
                e_idx = matching[local_idx]
                line_edges[e_idx]["takeupRate"] = (1.0 / 60.0) * line_edges[e_idx]["length"] / p_len

    # Membranes
    mem_edges_dict = {}
    for comp in components.findall("./membrane"):
        emodule = _number(comp, "emodule", positive=True) or 1.0e9
        areal = _number(comp, "areal", positive=True) or 2.5e-6
        density = _number(comp, "massDensity", positive=True) or 1000.0
        sub_mass_per_m = (density - water_density) * areal
        twine_mass_per_m = density * areal
        for el in comp.findall(".//element"):
            raw = [el.get(k) for k in ("nodeA", "nodeB", "nodeC", "nodeD") if el.get(k) is not None]
            el_nodes = []
            for r in raw:
                try: el_nodes.append(node_index[int(r)])
                except (ValueError, KeyError): pass
            pairs = []
            if len(el_nodes) == 3:
                pairs = [(el_nodes[0], el_nodes[1]), (el_nodes[1], el_nodes[2]), (el_nodes[2], el_nodes[0])]
            elif len(el_nodes) == 4:
                pairs = [
                    (el_nodes[0], el_nodes[1]), (el_nodes[1], el_nodes[2]),
                    (el_nodes[2], el_nodes[3]), (el_nodes[3], el_nodes[0]),
                    (el_nodes[0], el_nodes[2]), (el_nodes[1], el_nodes[3])
                ]
            for u, v in pairs:
                k = tuple(sorted([u, v]))
                if k not in mem_edges_dict:
                    l = float(np.linalg.norm(initial_positions[v] - initial_positions[u]))
                    if l > 1e-6:
                        mem_edges_dict[k] = {
                            "start": k[0], "end": k[1], "length": l,
                            "compliance": l / (emodule * areal),
                            "mass": twine_mass_per_m * l,
                            "sub_weight": sub_mass_per_m * l * GRAVITY_M_S2
                        }
                        particle_mass[k[0]] += twine_mass_per_m * l * 0.5
                        particle_mass[k[1]] += twine_mass_per_m * l * 0.5
                        particle_submerged_force[k[0], 2] -= sub_mass_per_m * l * 0.5 * GRAVITY_M_S2
                        particle_submerged_force[k[1], 2] -= sub_mass_per_m * l * 0.5 * GRAVITY_M_S2

    for i in range(node_count):
        if not fixed[i] and body_membership[i] < 0 and particle_mass[i] <= 0:
            particle_mass[i] = 1.0

    node_inv_mass = np.zeros(node_count, dtype=np.float64)
    for i in range(node_count):
        if not fixed[i] and body_membership[i] < 0 and particle_mass[i] > 0:
            node_inv_mass[i] = 1.0 / particle_mass[i]

    mem_list = list(mem_edges_dict.values())
    mem_edges_pairs = [(m["start"], m["end"]) for m in mem_list]
    offsets, counts, sorted_indices, num_colors = color_edges(mem_edges_pairs, node_count)
    print(f"Coloring done: {len(mem_list)} edges into {num_colors} parallel color groups.")

    edge_start = np.asarray([e["start"] for e in line_edges], dtype=np.int64)
    edge_end = np.asarray([e["end"] for e in line_edges], dtype=np.int64)
    edge_stiffness = np.asarray([e["stiffness"] for e in line_edges], dtype=np.float64)
    edge_rest_length = np.asarray([e["length"] for e in line_edges], dtype=np.float64)
    edge_takeup_rate = np.asarray([e.get("takeupRate", 0.0) for e in line_edges], dtype=np.float64)
    edge_compliance = np.asarray([e["compliance"] for e in line_edges], dtype=np.float64)
    edge_tension_only = np.asarray([e["tensionOnly"] for e in line_edges], dtype=np.bool_)
    edge_winch_index = np.asarray([e["winchIndex"] for e in line_edges], dtype=np.int64)

    m_start = np.asarray([m["start"] for m in mem_list], dtype=np.int64)
    m_end = np.asarray([m["end"] for m in mem_list], dtype=np.int64)
    m_len = np.asarray([m["length"] for m in mem_list], dtype=np.float64)
    m_comp = np.asarray([m["compliance"] for m in mem_list], dtype=np.float64)

    b_centers = np.asarray(body_centers, dtype=np.float64)
    b_mass = np.asarray(body_masses, dtype=np.float64)
    b_inv_mass = np.asarray([1.0/m if m > 0 else 0.0 for m in body_masses], dtype=np.float64)
    b_inertia = np.asarray(body_inertia, dtype=np.float64)
    b_inertia_inv = np.asarray(body_inertia_inv, dtype=np.float64)
    b_ext = np.zeros((len(body_centers), 3), dtype=np.float64)
    for b in range(len(body_centers)):
        b_ext[b, 2] = -body_submerged_weights[b]

    frame_count = math.ceil(duration_seconds / frame_interval_seconds) + 1
    output_times = np.minimum(np.arange(frame_count, dtype=np.float64) * frame_interval_seconds, duration_seconds)

    print("Warming up JIT...")
    _parallel_xpbd_kernel(
        initial_positions, fixed, body_membership, node_inv_mass, particle_submerged_force,
        b_centers, b_mass, b_inv_mass, b_inertia, b_inertia_inv, b_ext, body_local_nodes,
        edge_start, edge_end, edge_stiffness, edge_rest_length, edge_takeup_rate, edge_compliance, edge_tension_only, edge_winch_index,
        m_start, m_end, m_len, m_comp, offsets, counts, sorted_indices,
        output_times[:2], step_seconds, iterations, drag_coeff
    )

    print(f"Benchmarking parallel XPBD for {duration_seconds}s...")
    t0 = time.perf_counter()
    res = _parallel_xpbd_kernel(
        initial_positions, fixed, body_membership, node_inv_mass, particle_submerged_force,
        b_centers, b_mass, b_inv_mass, b_inertia, b_inertia_inv, b_ext, body_local_nodes,
        edge_start, edge_end, edge_stiffness, edge_rest_length, edge_takeup_rate, edge_compliance, edge_tension_only, edge_winch_index,
        m_start, m_end, m_len, m_comp, offsets, counts, sorted_indices,
        output_times, step_seconds, iterations, drag_coeff
    )
    t1 = time.perf_counter()
    print(f"Parallel {duration_seconds}s completed in {t1 - t0:.3f} s!")
    lift = res[3][-1]
    print(f"Final lift: {lift:.4f} m, Winch tension: {res[2][-1]}")

if __name__ == "__main__":
    benchmark_parallel()

