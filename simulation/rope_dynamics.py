"""Lumped-mass, axial-rope and membrane dynamics for AquaSim networks."""

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
def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q[0], q[1], q[2], q[3]
    R = np.zeros((3, 3))
    R[0, 0] = 1.0 - 2.0 * (y * y + z * z)
    R[0, 1] = 2.0 * (x * y - z * w)
    R[0, 2] = 2.0 * (x * z + y * w)
    R[1, 0] = 2.0 * (x * y + z * w)
    R[1, 1] = 1.0 - 2.0 * (x * x + z * z)
    R[1, 2] = 2.0 * (y * z - x * w)
    R[2, 0] = 2.0 * (x * z - y * w)
    R[2, 1] = 2.0 * (y * z + x * w)
    R[2, 2] = 1.0 - 2.0 * (x * x + y * y)
    return R


@njit(cache=True)
def quat_normalize(q: np.ndarray) -> np.ndarray:
    norm = math.sqrt(q[0]*q[0] + q[1]*q[1] + q[2]*q[2] + q[3]*q[3])
    if norm < 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0])
    return q / norm


@njit(cache=True)
def quat_mult(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    x1, y1, z1, w1 = q1[0], q1[1], q1[2], q1[3]
    x2, y2, z2, w2 = q2[0], q2[1], q2[2], q2[3]
    return np.array([
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
    ])


@njit(cache=True)
def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.array([
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0]
    ])


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


@njit(cache=True)
def _xpbd_kernel(
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

    while frame_index < frame_count:
        next_output = output_times[frame_index]
        while time_seconds < next_output - 1e-12:
            dt = min(step_seconds, next_output - time_seconds)
            dt2 = dt * dt
            inv_dt = 1.0 / dt
            inv_dt2 = 1.0 / dt2

            # 1. External forces & initial position prediction
            pos_pred = positions.copy()
            for i in range(node_count):
                if fixed_nodes[i] or node_body[i] >= 0:
                    continue
                w = node_inv_mass[i]
                if w <= 0.0:
                    continue
                velocities[i, 0] += dt * w * node_external_force[i, 0]
                velocities[i, 1] += dt * w * node_external_force[i, 1]
                velocities[i, 2] += dt * w * node_external_force[i, 2]
                velocities[i, 0] *= (1.0 - drag_factor)
                velocities[i, 1] *= (1.0 - drag_factor)
                velocities[i, 2] *= (1.0 - drag_factor)
                pos_pred[i, 0] += dt * velocities[i, 0]
                pos_pred[i, 1] += dt * velocities[i, 1]
                pos_pred[i, 2] += dt * velocities[i, 2]

            body_centers_pred = body_centers.copy()
            body_quats_pred = body_quats.copy()
            body_R_pred = np.zeros((body_count, 3, 3))
            body_I_inv_world = np.zeros((body_count, 3, 3))

            for b in range(body_count):
                w = body_inv_mass[b]
                body_velocities[b, 0] += dt * w * body_external_force[b, 0]
                body_velocities[b, 1] += dt * w * body_external_force[b, 1]
                body_velocities[b, 2] += dt * w * body_external_force[b, 2]
                body_velocities[b, 0] *= (1.0 - drag_factor)
                body_velocities[b, 1] *= (1.0 - drag_factor)
                body_velocities[b, 2] *= (1.0 - drag_factor)
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
                    for axis in range(3):
                        pos_pred[i, axis] = body_centers_pred[b, axis] + R[axis, 0]*local[0] + R[axis, 1]*local[1] + R[axis, 2]*local[2]

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

                # Membrane (net) constraints
                for e in range(mem_count):
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

            # 3. Update velocities and commit positions
            for i in range(node_count):
                if fixed_nodes[i]:
                    pos_pred[i] = initial_positions[i]
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
    solver_method: str = "xpbd",
    step_seconds: float = 0.02,
    iterations: int = 3,
    drag_coeff: float = 2.0,
    include_membrane: bool = True,
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
    body_submerged_weights: list[float] = []
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
        total_submerged_weight = 0.0
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
                total_submerged_weight += submerged_per_m * length * GRAVITY_M_S2
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
        body_submerged_weights.append(total_submerged_weight)
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
                    "compliance": length / (young * area),
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
    particle_submerged_force = np.zeros((node_count, 3), dtype=np.float64)
    for edge in line_edges:
        segment_mass = edge["massAir"] * edge["length"] * 0.5
        particle_mass[edge["start"]] += segment_mass
        particle_mass[edge["end"]] += segment_mass
        sub_weight = edge["massWater"] * edge["length"] * 0.5 * GRAVITY_M_S2
        particle_submerged_force[edge["start"], 2] -= sub_weight
        particle_submerged_force[edge["end"], 2] -= sub_weight

    # Extract membrane elements (nets)
    mem_edges_dict = {}
    if include_membrane:
        for comp in components.findall("./membrane"):
            emodule = _number(comp, "emodule", positive=True) or 1.0e9
            areal = _number(comp, "areal", positive=True) or 2.5e-6
            density = _number(comp, "massDensity", positive=True) or 1000.0
            sub_mass_per_m = (density - water_density) * areal
            twine_mass_per_m = density * areal

            for el in comp.findall(".//element"):
                raw = [el.get(k) for k in ("nodeA", "nodeB", "nodeC", "nodeD", "NodeA", "NodeB", "NodeC", "NodeD") if el.get(k) is not None]
                el_nodes = []
                for r in raw:
                    try:
                        el_nodes.append(node_index[int(r)])
                    except (ValueError, KeyError):
                        pass
                pairs = []
                if len(el_nodes) == 3:
                    pairs = [(el_nodes[0], el_nodes[1]), (el_nodes[1], el_nodes[2]), (el_nodes[2], el_nodes[0])]
                elif len(el_nodes) == 4:
                    pairs = [
                        (el_nodes[0], el_nodes[1]), (el_nodes[1], el_nodes[2]),
                        (el_nodes[2], el_nodes[3]), (el_nodes[3], el_nodes[0]),
                        (el_nodes[0], el_nodes[2]), (el_nodes[1], el_nodes[3]) # shear diagonals
                    ]
                for u, v in pairs:
                    edge_key = tuple(sorted([u, v]))
                    if edge_key not in mem_edges_dict:
                        length = float(np.linalg.norm(initial_positions[v] - initial_positions[u]))
                        if length > 1e-6:
                            mem_edges_dict[edge_key] = {
                                "start": edge_key[0],
                                "end": edge_key[1],
                                "length": length,
                                "compliance": length / (emodule * areal),
                                "mass": twine_mass_per_m * length,
                                "sub_weight": sub_mass_per_m * length * GRAVITY_M_S2,
                            }

        for (u, v), m_data in mem_edges_dict.items():
            particle_mass[u] += m_data["mass"] * 0.5
            particle_mass[v] += m_data["mass"] * 0.5
            particle_submerged_force[u, 2] -= m_data["sub_weight"] * 0.5
            particle_submerged_force[v, 2] -= m_data["sub_weight"] * 0.5

    # Fallback mass for any free nodes
    for node in range(node_count):
        if not fixed[node] and body_membership[node] < 0 and particle_mass[node] <= 0.0:
            particle_mass[node] = 1.0

    node_forces = np.zeros((node_count, 3), dtype=np.float64)
    node_moments = np.zeros((node_count, 3), dtype=np.float64)
    for load in model_data["nodeLoads"]:
        node = node_index[load["nodeId"]]
        node_forces[node] += np.asarray(load["forceN"], dtype=np.float64)
        node_moments[node] += np.asarray(load["momentNm"], dtype=np.float64)

    # Net external force on free particles
    particle_external_forces = particle_submerged_force + node_forces

    node_inv_mass = np.zeros(node_count, dtype=np.float64)
    for node in range(node_count):
        if not fixed[node] and body_membership[node] < 0 and particle_mass[node] > 0.0:
            node_inv_mass[node] = 1.0 / particle_mass[node]

    edge_start = np.asarray([edge["start"] for edge in line_edges], dtype=np.int64)
    edge_end = np.asarray([edge["end"] for edge in line_edges], dtype=np.int64)
    edge_stiffness = np.asarray([edge["stiffness"] for edge in line_edges], dtype=np.float64)
    edge_rest_length = np.asarray([edge["length"] for edge in line_edges], dtype=np.float64)
    edge_takeup_rate = np.asarray([edge.get("takeupRate", 0.0) for edge in line_edges], dtype=np.float64)
    edge_compliance = np.asarray([edge["compliance"] for edge in line_edges], dtype=np.float64)
    edge_tension_only = np.asarray([edge["tensionOnly"] for edge in line_edges], dtype=np.bool_)
    edge_mass_air = np.asarray([edge["massAir"] for edge in line_edges], dtype=np.float64)
    edge_mass_water = np.asarray([edge["massWater"] for edge in line_edges], dtype=np.float64)
    edge_winch_index = np.asarray([edge["winchIndex"] for edge in line_edges], dtype=np.int64)

    mem_start = np.asarray([m["start"] for m in mem_edges_dict.values()], dtype=np.int64)
    mem_end = np.asarray([m["end"] for m in mem_edges_dict.values()], dtype=np.int64)
    mem_len = np.asarray([m["length"] for m in mem_edges_dict.values()], dtype=np.float64)
    mem_comp = np.asarray([m["compliance"] for m in mem_edges_dict.values()], dtype=np.float64)

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

    frame_count = math.ceil(duration_seconds / frame_interval_seconds) + 1
    output_times = np.minimum(np.arange(frame_count, dtype=np.float64) * frame_interval_seconds, duration_seconds)
    output_times[-1] = duration_seconds

    if solver_method == "xpbd":
        effective_step = min(step_seconds, frame_interval_seconds)
        internal_steps = math.ceil(duration_seconds / effective_step)
        if internal_steps > max_internal_steps:
            raise ValueError(
                f"XPBD step {effective_step:.2e} s requires ({internal_steps:,} steps for {duration_seconds:g} s), "
                f"above --max-internal-steps ({max_internal_steps:,}). Use a shorter duration or raise the limit."
            )

        body_ext_force = np.zeros((len(body_centers), 3), dtype=np.float64)
        for b_idx in range(len(body_centers)):
            body_ext_force[b_idx, 2] = -body_submerged_weights[b_idx]

        b_centers = np.asarray(body_centers, dtype=np.float64).reshape((-1, 3))
        b_mass = np.asarray(body_masses, dtype=np.float64)
        b_inv_mass = np.asarray([1.0 / m if m > 0 else 0.0 for m in body_masses], dtype=np.float64)
        b_inertia = np.asarray(body_inertia, dtype=np.float64).reshape((-1, 3, 3))
        b_inertia_inv = np.asarray(body_inertia_inverse, dtype=np.float64).reshape((-1, 3, 3))

        dynamic = _xpbd_kernel(
            initial_positions,
            fixed,
            body_membership,
            node_inv_mass,
            particle_external_forces,
            b_centers,
            b_mass,
            b_inv_mass,
            b_inertia,
            b_inertia_inv,
            body_ext_force,
            body_local_nodes,
            edge_start,
            edge_end,
            edge_stiffness,
            edge_rest_length,
            edge_takeup_rate,
            edge_compliance,
            edge_tension_only,
            edge_winch_index,
            mem_start,
            mem_end,
            mem_len,
            mem_comp,
            output_times,
            effective_step,
            iterations,
            drag_coeff,
        )
        actual_step_seconds = effective_step
    else:
        # Explicit solver
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
        actual_step_seconds = stable_dt

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
        "integrationStepSeconds": actual_step_seconds,
        "internalSteps": internal_steps,
        "takeUpSpeedMPerMin": 1.0,
        "solverMethod": solver_method,
        "membraneDynamics": include_membrane and len(mem_start) > 0,
        "hydrodynamicDrag": True if solver_method == "xpbd" else False,
        "addedMass": False,
    }
    result_warnings = warnings_out + [
        f"{solver_method.upper()} timestep: {actual_step_seconds:.3e} s ({internal_steps:,} internal steps for {duration_seconds:g} s).",
        "Rope initial pretension values are ignored because their units/reference length are unspecified; each rope starts stress-free at its input geometry.",
        f"Membrane net dynamics: {'Included (' + str(len(mem_start)) + ' compliant edges coupled to boundary ropes and rings)' if include_membrane and len(mem_start) > 0 else 'Excluded'}.",
        f"Hydrodynamic drag damping: {'Applied in water' if solver_method == 'xpbd' else 'Omitted'}.",
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
