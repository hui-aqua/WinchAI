import time
import math
import numpy as np
from numba import njit
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.winch_solver import GRAVITY_M_S2, _number, _read_model
from simulation.rope_dynamics import _beam_groups, _connected_edge_groups

@njit(cache=True)
def quat_to_matrix_scalar(qx, qy, qz, qw, R):
    R[0, 0] = 1.0 - 2.0 * (qy * qy + qz * qz)
    R[0, 1] = 2.0 * (qx * qy - qz * qw)
    R[0, 2] = 2.0 * (qx * qz + qy * qw)
    R[1, 0] = 2.0 * (qx * qy + qz * qw)
    R[1, 1] = 1.0 - 2.0 * (qx * qx + qz * qz)
    R[1, 2] = 2.0 * (qy * qz - qx * qw)
    R[2, 0] = 2.0 * (qx * qz - qy * qw)
    R[2, 1] = 2.0 * (qy * qz + qx * qw)
    R[2, 2] = 1.0 - 2.0 * (qx * qx + qy * qy)

@njit(cache=True)
def _fast_xpbd_kernel(
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
    body_centers_pred = np.zeros_like(body_centers)
    body_quats_pred = np.zeros_like(body_quats)
    body_R_pred = np.zeros((body_count, 3, 3))
    body_I_inv_world = np.zeros((body_count, 3, 3))

    while frame_index < frame_count:
        next_output = output_times[frame_index]
        while time_seconds < next_output - 1e-12:
            dt = min(step_seconds, next_output - time_seconds)
            dt2 = dt * dt
            inv_dt = 1.0 / dt
            inv_dt2 = 1.0 / dt2

            # 1. Prediction for free particles (scalar, no allocation)
            for i in range(node_count):
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

            # Prediction for rigid bodies
            for b in range(body_count):
                w = body_inv_mass[b]
                body_velocities[b, 0] = (body_velocities[b, 0] + dt * w * body_external_force[b, 0]) * (1.0 - drag_factor)
                body_velocities[b, 1] = (body_velocities[b, 1] + dt * w * body_external_force[b, 1]) * (1.0 - drag_factor)
                body_velocities[b, 2] = (body_velocities[b, 2] + dt * w * body_external_force[b, 2]) * (1.0 - drag_factor)
                body_centers_pred[b, 0] = body_centers[b, 0] + dt * body_velocities[b, 0]
                body_centers_pred[b, 1] = body_centers[b, 1] + dt * body_velocities[b, 1]
                body_centers_pred[b, 2] = body_centers[b, 2] + dt * body_velocities[b, 2]

                body_angular_vel[b, 0] *= (1.0 - drag_factor)
                body_angular_vel[b, 1] *= (1.0 - drag_factor)
                body_angular_vel[b, 2] *= (1.0 - drag_factor)

                wx = body_angular_vel[b, 0] * 0.5 * dt
                wy = body_angular_vel[b, 1] * 0.5 * dt
                wz = body_angular_vel[b, 2] * 0.5 * dt

                qx, qy, qz, qw = body_quats[b, 0], body_quats[b, 1], body_quats[b, 2], body_quats[b, 3]
                nqx = qw * wx + qy * wz - qz * wy + qx
                nqy = qw * wy + qz * wx - qx * wz + qy
                nqz = qw * wz + qx * wy - qy * wx + qz
                nqw = -qx * wx - qy * wy - qz * wz + qw
                qnorm = math.sqrt(nqx*nqx + nqy*nqy + nqz*nqz + nqw*nqw)
                if qnorm > 1e-12:
                    inv_q = 1.0 / qnorm
                    nqx *= inv_q
                    nqy *= inv_q
                    nqz *= inv_q
                    nqw *= inv_q

                body_quats_pred[b, 0] = nqx
                body_quats_pred[b, 1] = nqy
                body_quats_pred[b, 2] = nqz
                body_quats_pred[b, 3] = nqw

                quat_to_matrix_scalar(nqx, nqy, nqz, nqw, body_R_pred[b])
                R = body_R_pred[b]
                body_I_inv_world[b] = R @ body_inertia_inv[b] @ R.T

            # Project body nodes to world space
            for i in range(node_count):
                b = node_body[i]
                if b >= 0:
                    R = body_R_pred[b]
                    lx, ly, lz = body_local_nodes[i, 0], body_local_nodes[i, 1], body_local_nodes[i, 2]
                    pos_pred[i, 0] = body_centers_pred[b, 0] + R[0, 0]*lx + R[0, 1]*ly + R[0, 2]*lz
                    pos_pred[i, 1] = body_centers_pred[b, 1] + R[1, 0]*lx + R[1, 1]*ly + R[1, 2]*lz
                    pos_pred[i, 2] = body_centers_pred[b, 2] + R[2, 0]*lx + R[2, 1]*ly + R[2, 2]*lz

            # 2. XPBD Constraint Solver Loop
            rope_lambdas[:] = 0.0
            mem_lambdas[:] = 0.0

            for it in range(iterations):
                # Rope constraints
                for e in range(rope_count):
                    i1 = edge_start[e]
                    i2 = edge_end[e]
                    dx = pos_pred[i2, 0] - pos_pred[i1, 0]
                    dy = pos_pred[i2, 1] - pos_pred[i1, 1]
                    dz = pos_pred[i2, 2] - pos_pred[i1, 2]
                    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                    if dist < 1e-9:
                        continue

                    inv_dist = 1.0 / dist
                    nx = dx * inv_dist
                    ny = dy * inv_dist
                    nz = dz * inv_dist

                    rest = max(1e-4, edge_rest_length[e] - edge_takeup_rate[e] * time_seconds)
                    C = dist - rest
                    if edge_tension_only[e] and C <= 0.0 and rope_lambdas[e] <= 0.0:
                        continue

                    alpha_tilde = edge_compliance[e] * inv_dt2

                    w1 = 0.0
                    b1 = node_body[i1]
                    if not fixed_nodes[i1]:
                        if b1 >= 0:
                            r1x = pos_pred[i1, 0] - body_centers_pred[b1, 0]
                            r1y = pos_pred[i1, 1] - body_centers_pred[b1, 1]
                            r1z = pos_pred[i1, 2] - body_centers_pred[b1, 2]
                            u1x = r1y * nz - r1z * ny
                            u1y = r1z * nx - r1x * nz
                            u1z = r1x * ny - r1y * nx
                            I_inv = body_I_inv_world[b1]
                            Iu1x = I_inv[0, 0]*u1x + I_inv[0, 1]*u1y + I_inv[0, 2]*u1z
                            Iu1y = I_inv[1, 0]*u1x + I_inv[1, 1]*u1y + I_inv[1, 2]*u1z
                            Iu1z = I_inv[2, 0]*u1x + I_inv[2, 1]*u1y + I_inv[2, 2]*u1z
                            w1 = body_inv_mass[b1] + (u1x*Iu1x + u1y*Iu1y + u1z*Iu1z)
                        else:
                            w1 = node_inv_mass[i1]

                    w2 = 0.0
                    b2 = node_body[i2]
                    if not fixed_nodes[i2]:
                        if b2 >= 0:
                            r2x = pos_pred[i2, 0] - body_centers_pred[b2, 0]
                            r2y = pos_pred[i2, 1] - body_centers_pred[b2, 1]
                            r2z = pos_pred[i2, 2] - body_centers_pred[b2, 2]
                            u2x = -(r2y * nz - r2z * ny)
                            u2y = -(r2z * nx - r2x * nz)
                            u2z = -(r2x * ny - r2y * nx)
                            I_inv = body_I_inv_world[b2]
                            Iu2x = I_inv[0, 0]*u2x + I_inv[0, 1]*u2y + I_inv[0, 2]*u2z
                            Iu2y = I_inv[1, 0]*u2x + I_inv[1, 1]*u2y + I_inv[1, 2]*u2z
                            Iu2z = I_inv[2, 0]*u2x + I_inv[2, 1]*u2y + I_inv[2, 2]*u2z
                            w2 = body_inv_mass[b2] + (u2x*Iu2x + u2y*Iu2y + u2z*Iu2z)
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

                    if not fixed_nodes[i1]:
                        if b1 >= 0:
                            body_centers_pred[b1, 0] -= body_inv_mass[b1] * px
                            body_centers_pred[b1, 1] -= body_inv_mass[b1] * py
                            body_centers_pred[b1, 2] -= body_inv_mass[b1] * pz
                            r1x = pos_pred[i1, 0] - body_centers_pred[b1, 0]
                            r1y = pos_pred[i1, 1] - body_centers_pred[b1, 1]
                            r1z = pos_pred[i1, 2] - body_centers_pred[b1, 2]
                            tx = -(r1y * pz - r1z * py)
                            ty = -(r1z * px - r1x * pz)
                            tz = -(r1x * py - r1y * px)
                            I_inv = body_I_inv_world[b1]
                            dtheta_x = (I_inv[0, 0]*tx + I_inv[0, 1]*ty + I_inv[0, 2]*tz) * 0.5
                            dtheta_y = (I_inv[1, 0]*tx + I_inv[1, 1]*ty + I_inv[1, 2]*tz) * 0.5
                            dtheta_z = (I_inv[2, 0]*tx + I_inv[2, 1]*ty + I_inv[2, 2]*tz) * 0.5
                            qx, qy, qz, qw = body_quats_pred[b1, 0], body_quats_pred[b1, 1], body_quats_pred[b1, 2], body_quats_pred[b1, 3]
                            nqx = qw * dtheta_x + qy * dtheta_z - qz * dtheta_y + qx
                            nqy = qw * dtheta_y + qz * dtheta_x - qx * dtheta_z + qy
                            nqz = qw * dtheta_z + qx * dtheta_y - qy * dtheta_x + qz
                            nqw = -qx * dtheta_x - qy * dtheta_y - qz * dtheta_z + qw
                            qnorm = math.sqrt(nqx*nqx + nqy*nqy + nqz*nqz + nqw*nqw)
                            if qnorm > 1e-12:
                                inv_q = 1.0 / qnorm
                                body_quats_pred[b1, 0] = nqx * inv_q
                                body_quats_pred[b1, 1] = nqy * inv_q
                                body_quats_pred[b1, 2] = nqz * inv_q
                                body_quats_pred[b1, 3] = nqw * inv_q
                            quat_to_matrix_scalar(body_quats_pred[b1, 0], body_quats_pred[b1, 1], body_quats_pred[b1, 2], body_quats_pred[b1, 3], body_R_pred[b1])
                            body_I_inv_world[b1] = body_R_pred[b1] @ body_inertia_inv[b1] @ body_R_pred[b1].T
                        else:
                            pos_pred[i1, 0] -= node_inv_mass[i1] * px
                            pos_pred[i1, 1] -= node_inv_mass[i1] * py
                            pos_pred[i1, 2] -= node_inv_mass[i1] * pz

                    if not fixed_nodes[i2]:
                        if b2 >= 0:
                            body_centers_pred[b2, 0] += body_inv_mass[b2] * px
                            body_centers_pred[b2, 1] += body_inv_mass[b2] * py
                            body_centers_pred[b2, 2] += body_inv_mass[b2] * pz
                            r2x = pos_pred[i2, 0] - body_centers_pred[b2, 0]
                            r2y = pos_pred[i2, 1] - body_centers_pred[b2, 1]
                            r2z = pos_pred[i2, 2] - body_centers_pred[b2, 2]
                            tx = r2y * pz - r2z * py
                            ty = r2z * px - r2x * pz
                            tz = r2x * py - r2y * px
                            I_inv = body_I_inv_world[b2]
                            dtheta_x = (I_inv[0, 0]*tx + I_inv[0, 1]*ty + I_inv[0, 2]*tz) * 0.5
                            dtheta_y = (I_inv[1, 0]*tx + I_inv[1, 1]*ty + I_inv[1, 2]*tz) * 0.5
                            dtheta_z = (I_inv[2, 0]*tx + I_inv[2, 1]*ty + I_inv[2, 2]*tz) * 0.5
                            qx, qy, qz, qw = body_quats_pred[b2, 0], body_quats_pred[b2, 1], body_quats_pred[b2, 2], body_quats_pred[b2, 3]
                            nqx = qw * dtheta_x + qy * dtheta_z - qz * dtheta_y + qx
                            nqy = qw * dtheta_y + qz * dtheta_x - qx * dtheta_z + qy
                            nqz = qw * dtheta_z + qx * dtheta_y - qy * dtheta_x + qz
                            nqw = -qx * dtheta_x - qy * dtheta_y - qz * dtheta_z + qw
                            qnorm = math.sqrt(nqx*nqx + nqy*nqy + nqz*nqz + nqw*nqw)
                            if qnorm > 1e-12:
                                inv_q = 1.0 / qnorm
                                body_quats_pred[b2, 0] = nqx * inv_q
                                body_quats_pred[b2, 1] = nqy * inv_q
                                body_quats_pred[b2, 2] = nqz * inv_q
                                body_quats_pred[b2, 3] = nqw * inv_q
                            quat_to_matrix_scalar(body_quats_pred[b2, 0], body_quats_pred[b2, 1], body_quats_pred[b2, 2], body_quats_pred[b2, 3], body_R_pred[b2])
                            body_I_inv_world[b2] = body_R_pred[b2] @ body_inertia_inv[b2] @ body_R_pred[b2].T
                        else:
                            pos_pred[i2, 0] += node_inv_mass[i2] * px
                            pos_pred[i2, 1] += node_inv_mass[i2] * py
                            pos_pred[i2, 2] += node_inv_mass[i2] * pz

                    if b1 >= 0:
                        R = body_R_pred[b1]
                        lx, ly, lz = body_local_nodes[i1, 0], body_local_nodes[i1, 1], body_local_nodes[i1, 2]
                        pos_pred[i1, 0] = body_centers_pred[b1, 0] + R[0, 0]*lx + R[0, 1]*ly + R[0, 2]*lz
                        pos_pred[i1, 1] = body_centers_pred[b1, 1] + R[1, 0]*lx + R[1, 1]*ly + R[1, 2]*lz
                        pos_pred[i1, 2] = body_centers_pred[b1, 2] + R[2, 0]*lx + R[2, 1]*ly + R[2, 2]*lz
                    if b2 >= 0:
                        R = body_R_pred[b2]
                        lx, ly, lz = body_local_nodes[i2, 0], body_local_nodes[i2, 1], body_local_nodes[i2, 2]
                        pos_pred[i2, 0] = body_centers_pred[b2, 0] + R[0, 0]*lx + R[0, 1]*ly + R[0, 2]*lz
                        pos_pred[i2, 1] = body_centers_pred[b2, 1] + R[1, 0]*lx + R[1, 1]*ly + R[1, 2]*lz
                        pos_pred[i2, 2] = body_centers_pred[b2, 2] + R[2, 0]*lx + R[2, 1]*ly + R[2, 2]*lz

                # Membrane (net) constraints - pure scalar, ultra-fast!
                for e in range(mem_count):
                    i1 = mem_edge_start[e]
                    i2 = mem_edge_end[e]
                    dx = pos_pred[i2, 0] - pos_pred[i1, 0]
                    dy = pos_pred[i2, 1] - pos_pred[i1, 1]
                    dz = pos_pred[i2, 2] - pos_pred[i1, 2]
                    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                    if dist < 1e-9:
                        continue

                    inv_dist = 1.0 / dist
                    nx = dx * inv_dist
                    ny = dy * inv_dist
                    nz = dz * inv_dist

                    C = dist - mem_edge_rest_length[e]
                    if C <= 0.0 and mem_lambdas[e] <= 0.0:
                        continue

                    alpha_tilde = mem_edge_compliance[e] * inv_dt2
                    w1 = node_inv_mass[i1]
                    w2 = node_inv_mass[i2]
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

                    if w1 > 0.0:
                        pos_pred[i1, 0] -= w1 * px
                        pos_pred[i1, 1] -= w1 * py
                        pos_pred[i1, 2] -= w1 * pz
                    if w2 > 0.0:
                        pos_pred[i2, 0] += w2 * px
                        pos_pred[i2, 1] += w2 * py
                        pos_pred[i2, 2] += w2 * pz

            # 3. Velocity update and position commit
            for i in range(node_count):
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

                q_prev_x, q_prev_y, q_prev_z, q_prev_w = body_quats[b, 0], body_quats[b, 1], body_quats[b, 2], body_quats[b, 3]
                q_cur_x, q_cur_y, q_cur_z, q_cur_w = body_quats_pred[b, 0], body_quats_pred[b, 1], body_quats_pred[b, 2], body_quats_pred[b, 3]
                diff_x = q_cur_w * (-q_prev_x) + q_cur_x * q_prev_w + q_cur_y * (-q_prev_z) - q_cur_z * (-q_prev_y)
                diff_y = q_cur_w * (-q_prev_y) - q_cur_x * (-q_prev_z) + q_cur_y * q_prev_w + q_cur_z * (-q_prev_x)
                diff_z = q_cur_w * (-q_prev_z) + q_cur_x * (-q_prev_y) - q_cur_y * (-q_prev_x) + q_cur_z * q_prev_w
                body_angular_vel[b, 0] = 2.0 * diff_x * inv_dt
                body_angular_vel[b, 1] = 2.0 * diff_y * inv_dt
                body_angular_vel[b, 2] = 2.0 * diff_z * inv_dt
                body_quats[b, 0] = q_cur_x
                body_quats[b, 1] = q_cur_y
                body_quats[b, 2] = q_cur_z
                body_quats[b, 3] = q_cur_w

            # Update body nodes world positions
            for i in range(node_count):
                b = node_body[i]
                if b >= 0:
                    local = body_local_nodes[i]
                    R = body_R_pred[b]
                    r_world_x = R[0, 0]*local[0] + R[0, 1]*local[1] + R[0, 2]*local[2]
                    r_world_y = R[1, 0]*local[0] + R[1, 1]*local[1] + R[1, 2]*local[2]
                    r_world_z = R[2, 0]*local[0] + R[2, 1]*local[1] + R[2, 2]*local[2]
                    positions[i, 0] = body_centers[b, 0] + r_world_x
                    positions[i, 1] = body_centers[b, 1] + r_world_y
                    positions[i, 2] = body_centers[b, 2] + r_world_z
                    rot_x = body_angular_vel[b, 1]*r_world_z - body_angular_vel[b, 2]*r_world_y
                    rot_y = body_angular_vel[b, 2]*r_world_x - body_angular_vel[b, 0]*r_world_z
                    rot_z = body_angular_vel[b, 0]*r_world_y - body_angular_vel[b, 1]*r_world_x
                    velocities[i, 0] = body_velocities[b, 0] + rot_x
                    velocities[i, 1] = body_velocities[b, 1] + rot_y
                    velocities[i, 2] = body_velocities[b, 2] + rot_z

            time_seconds += dt

        # Record output frames
        if frame_index < frame_count and abs(time_seconds - output_times[frame_index]) <= 1e-8:
            for i in range(node_count):
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

print("Fast scalar XPBD defined.")
