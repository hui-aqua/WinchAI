"""Independent CPU equilibrium samples and bounded local repair/refinement.

The coordinate is progress along a coordinated vector of winch payouts, not
elapsed time. Interpolate rigid poses (rather than nodes) to retain constraints.
"""
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import os
import numpy as np
from .equilibrium import EquilibriumSystem, EquilibriumError

_WORKER = None


def _initialize(model, tolerances):
    global _WORKER
    _WORKER = EquilibriumSystem(model, tolerances)
    # SciPy's dense kernels must not multiply the process-level CPU parallelism.
    try:
        from threadpoolctl import threadpool_limits
        threadpool_limits(1)
    except ImportError:
        pass


def _solve(task):
    coordinate, payout = task
    try:
        return coordinate, _WORKER.solve(payout)
    except EquilibriumError:
        return coordinate, None


def valid(system, state):
    return (all(np.all(np.isfinite(state[k])) for k in
                ('positions', 'tensions', 'residual', 'moment', 'buoyancy',
                 'netVerticalLoad', 'forceError', 'momentError', 'constraintError')) and
            state['forceError'] <= system.tol.force_n and
            state['momentError'] <= system.tol.moment_nm and
            state['constraintError'] <= system.tol.constraint_m)


def metrics(state):
    return dict(forceResidualN=state['forceError'], momentResidualNm=state['momentError'],
                constraintErrorM=state['constraintError'], displacementM=0.0,
                tensionChangeN=0.0, buoyancyChangeN=0.0, evaluations=0)


class EquilibriumDatabase:
    def __init__(self, system, payouts, increment, minimum, max_steps, workers=None):
        self.system, self.payouts = system, payouts
        self.travel = float(np.max(np.abs(payouts), initial=0))
        intervals = self.travel/increment
        if not np.isfinite(intervals) or intervals+1 > max_steps:
            raise EquilibriumError('Equilibrium database exceeds maximum trial steps.')
        count = max(1, int(np.ceil(intervals))) if self.travel else 0
        if count+1 > max_steps:
            raise EquilibriumError('Equilibrium database exceeds maximum trial steps.')
        coordinates = np.linspace(0, 1, count+1) if count else np.array([0.0])
        self.workers = min(workers or min(4, os.cpu_count() or 1), len(coordinates))
        self.attempts, self.repairs, self.refinements, self.events = 0, 0, 0, 0
        self.samples = {}
        self.minimum, self.max_steps = minimum, max_steps
        tasks = [(float(c), payouts*c) for c in coordinates]
        if self.workers > 1:
            with ProcessPoolExecutor(self.workers, mp_context=get_context('spawn'),
                                     initializer=_initialize,
                                     initargs=(system.model, system.tol)) as pool:
                results = list(pool.map(_solve, tasks, chunksize=1))
        else:
            results = []
            for c, payout in tasks:
                try:
                    results.append((c, system.solve(payout)))
                except EquilibriumError:
                    results.append((c, None))
        self.attempts = len(tasks)
        for c, solution in results:
            if solution is not None and valid(system, solution[1]):
                self.samples[c] = solution
        pending = [c for c, _ in results if c not in self.samples]
        while pending:
            progress = False
            for c in pending[:]:
                if self.repair(c):
                    pending.remove(c)
                    progress = True
            if not progress:
                raise EquilibriumError(f'No validated neighboring solution for equilibrium states {pending}.')
        self.refine()
        self.coordinates = np.array(sorted(self.samples))

    def repair(self, c, seeds=None):
        if seeds is None:
            seeds = [self.samples[k][0] for k in sorted(self.samples, key=lambda k: abs(k-c))[:2]]
        for seed in seeds:
            if self.attempts >= self.max_steps:
                raise EquilibriumError('Equilibrium database exceeded maximum trial steps.')
            self.attempts += 1
            self.repairs += 1
            try:
                solution = self.system.solve(self.payouts*c, seed)
                if valid(self.system, solution[1]):
                    self.samples[c] = solution
                    return True
            except EquilibriumError:
                pass
        return False

    def discontinuous(self, sa, sb, span):
        return np.max(np.linalg.norm(sb['positions']-sa['positions'], axis=1), initial=0) > max(.05, 4*span*self.travel)

    def refine(self):
        intervals = list(zip(sorted(self.samples), sorted(self.samples)[1:]))
        while intervals:
            a, b = intervals.pop()
            xa, sa, _ = self.samples[a]
            xb, sb, _ = self.samples[b]
            c = (a+b)/2
            trial = self.system.evaluate((xa+xb)/2, self.payouts*c)
            event = (np.any(sa['active'] != sb['active']) or
                     np.any((sa['positions'][:, 2] < 0) != (sb['positions'][:, 2] < 0)))
            tension_change = np.max(np.abs(sb['tensions']-sa['tensions']), initial=0) > max(
                10.0, .05*np.max(np.abs(sa['tensions']), initial=0))
            response = self.discontinuous(sa, sb, b-a)
            buoyancy_change = abs(sb['buoyancy']-sa['buoyancy']) > max(10.0, .01*abs(sa['buoyancy']))
            suspicious = not valid(self.system, trial) or event or response or tension_change or buoyancy_change
            if not suspicious:
                continue
            self.events += int(event)
            # Re-solve suspicious endpoints from a nearby solution before refining.
            if response:
                if not self.repair(b, [xa]):
                    raise EquilibriumError(f'Unable to repair discontinuous state at {b}.')
                old_state = sb
                xb, sb, _ = self.samples[b]
                response = self.discontinuous(sa, sb, b-a)
                if np.max(np.linalg.norm(sb['positions']-old_state['positions'], axis=1), initial=0) > self.system.tol.displacement_m:
                    neighbors = sorted(self.samples)
                    index = neighbors.index(b)
                    if index+1 < len(neighbors):
                        intervals.append((b, neighbors[index+1]))
                trial = self.system.evaluate((xa+xb)/2, self.payouts*c)
            if (b-a)*self.travel <= self.minimum*(1+1e-9):
                if not valid(self.system, trial):
                    raise EquilibriumError('Equilibrium interpolation remains invalid at minimum rope increment.')
                if response:
                    raise EquilibriumError('Structural continuity remains unresolved at minimum rope increment.')
                continue
            if not self.repair(c, [(xa+xb)/2, xa, xb]):
                raise EquilibriumError(f'Unable to resolve equilibrium interval {a}..{b}.')
            self.refinements += 1
            intervals.extend(((a, c), (c, b)))

    def at(self, coordinate):
        if not 0 <= coordinate <= 1:
            raise ValueError('Velocity profile leaves the precomputed rope-length range.')
        if len(self.coordinates) == 1:
            return self.samples[0.0][1:]
        upper = min(int(np.searchsorted(self.coordinates, coordinate)), len(self.coordinates)-1)
        b = self.coordinates[upper]
        if coordinate == b:
            return self.samples[b][1:]
        a = self.coordinates[upper-1]
        fraction = (coordinate-a)/(b-a)
        x = self.samples[a][0]*(1-fraction)+self.samples[b][0]*fraction
        state = self.system.evaluate(x, self.payouts*coordinate)
        if not valid(self.system, state):
            # Midpoint checks cannot guarantee every point of a nonlinear interval.
            if not self.repair(float(coordinate), [x, self.samples[a][0], self.samples[b][0]]):
                raise EquilibriumError('Playback equilibrium failed local validation.')
            repaired = self.samples[float(coordinate)][1]
            if np.max(np.linalg.norm(repaired['positions']-state['positions'], axis=1), initial=0) > max(.05, 4*(b-a)*self.travel):
                raise EquilibriumError('Playback repair introduced an unresolved structural discontinuity.')
            self.coordinates = np.array(sorted(self.samples))
            return self.samples[float(coordinate)][1:]
        return state, dict(metrics(state), interpolated=True)

    def serialize(self):
        output_indices = [i for i,e in enumerate(self.system.model.edges) if e['output']]
        properties = {r['componentId']: r for r in self.system.model.metadata['componentProperties']}
        lengths = [properties[w['componentId']]['pathLengthsM']
                   for w in self.system.model.metadata['winches']]
        return dict(coordinate='coordinated-winch-payout',
                    winchComponentIds=[w['componentId'] for w in self.system.model.metadata['winches']],
                    pathTravelRangeM=[0.0,self.travel],
                    baseRopeLengthsM=lengths,
                    rigidBodies=[dict(nodes=body.nodes.tolist(),centerM=body.center.tolist(),
                                      localPositionsM=body.local.tolist(),scale=body.scale)
                                 for body in self.system.model.bodies],
                    particleNodes=self.system.model.particles.tolist(), states=[
            dict(progress=c, ropeLengthsM=[[length+c*self.payouts[i] for length in paths]
                                              for i,paths in enumerate(lengths)], winchPayoutsM=(self.payouts*c).tolist(),
                 generalizedCoordinates=x.tolist(),
                 ropeTensionsN=state['tensions'][output_indices].tolist(),
                 buoyancyN=state['buoyancy'], netVerticalLoadN=state['netVerticalLoad'],
                 momentNm=state['moment'].tolist(), **metric)
            for c, (x, state, metric) in sorted(self.samples.items())])
