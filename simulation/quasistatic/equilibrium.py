"""Sparse nonlinear equilibrium in rigid poses and flexible node coordinates."""
from dataclasses import dataclass
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation
from .hydrostatics import segment_buoyancy
from simulation.model_io import GRAVITY_M_S2


@dataclass(frozen=True)
class Tolerances:
    force_n: float = 1.0
    moment_nm: float = 1.0
    displacement_m: float = 1e-5
    tension_n: float = 1.0
    buoyancy_n: float = 1.0
    constraint_m: float = 1e-8
    max_evaluations: int = 300

    def validate(self):
        for key, value in vars(self).items():
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be finite and positive.')
        if not isinstance(self.max_evaluations, int):
            raise ValueError('max_evaluations must be an integer.')


class EquilibriumError(RuntimeError):
    pass


class EquilibriumSystem:
    def __init__(self, model, tolerances=Tolerances()):
        tolerances.validate()
        self.model, self.tol = model, tolerances
        self.body_size = 6*len(model.bodies)
        self.size = self.body_size+3*len(model.particles)
        self.x0 = np.zeros(self.size)
        self.x0[self.body_size:] = model.positions[model.particles].ravel()
        self.node_slice = [None]*len(model.positions)
        for i, body in enumerate(model.bodies):
            for n in body.nodes:
                self.node_slice[n] = slice(6*i,6*i+6)
        for i, n in enumerate(model.particles):
            self.node_slice[n] = slice(self.body_size+3*i,self.body_size+3*i+3)
        pattern = lil_matrix((self.size,self.size), dtype=int)
        for s in self.node_slice:
            if s is not None:
                pattern[s,s] = 1
        for edge in model.edges+model.beams:
            a, b = self.node_slice[edge['a']], self.node_slice[edge['b']]
            if a is not None and b is not None:
                pattern[a,b] = 1
                pattern[b,a] = 1
        self.sparsity = pattern.tocsr()
        self.edge_arrays = {key: np.asarray([e[key] for e in model.edges]) for key in
                            ('a','b','length','ea','fraction','air','water','tensionOnly','winch')}

    def positions(self, x):
        pos = self.model.positions.copy()
        centers = []
        for i, body in enumerate(self.model.bodies):
            center = body.center+x[6*i:6*i+3]
            rotation = Rotation.from_rotvec(x[6*i+3:6*i+6]/body.scale)
            pos[body.nodes] = center+rotation.apply(body.local)
            centers.append(center)
        pos[self.model.particles] = x[self.body_size:].reshape(-1,3)
        return pos, centers

    def evaluate(self, x, payout):
        model = self.model
        pos, centers = self.positions(x)
        forces = model.forces.copy()
        external = model.forces.copy()
        e = self.edge_arrays
        a, b = e['a'].astype(int), e['b'].astype(int)
        delta = pos[b]-pos[a]
        lengths = np.linalg.norm(delta,axis=1)
        payouts = np.broadcast_to(np.asarray(payout,dtype=float), (len(model.metadata['winches']),))
        rest = e['length']+payouts[np.maximum(e['winch'].astype(int),0)]*e['fraction']
        if np.any(rest <= 1e-6):
            raise ValueError('Retrieval exceeds available unstretched cable length.')
        extension = lengths-rest
        tensions = e['ea']*extension/rest
        tensions = np.where(e['tensionOnly'],np.maximum(tensions,0),tensions)
        active = np.where(e['tensionOnly'],extension > 0,True)
        axial = tensions[:,None]*delta/np.maximum(lengths[:,None],1e-12)
        np.add.at(forces,a,axial)
        np.add.at(forces,b,-axial)
        z0,z1 = pos[a,2],pos[b,2]
        crossing = z0/np.where(np.abs(z0-z1)>1e-12,z0-z1,1)
        lo = np.where((z0>0)&(z1<=0),crossing,0.0)
        hi = np.where((z0<=0)&(z1>0),crossing,1.0)
        hi = np.where((z0>0)&(z1>0),0.0,hi)
        up = (e['air']-e['water'])*rest*GRAVITY_M_S2*(hi-lo)
        weight = e['air']*rest*GRAVITY_M_S2
        t = (lo+hi)/2
        np.add.at(external[:,2],a,up*(1-t)-weight/2)
        np.add.at(external[:,2],b,up*t-weight/2)
        buoyancy = float(up.sum())
        for beam in model.beams:
            a, b = beam['a'], beam['b']
            delta = pos[b]-pos[a]
            up, center = segment_buoyancy(pos[a],pos[b],beam['buoyancy'],beam['radius'])
            buoyancy += up
            t = np.dot(center-pos[a],delta)/(beam['length']**2)
            weight = beam['mass']*GRAVITY_M_S2
            external[a,2] += up*(1-t)-weight/2
            external[b,2] += up*t-weight/2
        forces += external-model.forces
        balance, moments = [], []
        for i, body in enumerate(model.bodies):
            f = forces[body.nodes].sum(axis=0)
            m = (np.cross(pos[body.nodes]-centers[i],forces[body.nodes])+model.moments[body.nodes]).sum(axis=0)
            balance.extend(f)
            balance.extend(m/body.scale)
            moments.extend(m)
        balance.extend(forces[model.particles].ravel())
        free_force = np.concatenate([forces[model.particles].ravel()]+[
            forces[b.nodes].sum(axis=0) for b in model.bodies])
        moving = np.flatnonzero(~model.fixed)
        external_moment = (np.cross(pos[moving]-np.asarray(model.metadata['centerOfMassM']),external[moving])+
                           model.moments[moving]).sum(axis=0)
        constraint = max([0.0]+[abs(np.linalg.norm(pos[e['b']]-pos[e['a']])-e['length']) for e in model.beams])
        return dict(residual=np.asarray(balance), positions=pos, tensions=np.asarray(tensions),
                    buoyancy=float(buoyancy), active=np.asarray(active),
                    forceError=float(np.max(np.abs(free_force),initial=0)),
                    momentError=float(np.max(np.abs(moments),initial=0)), constraintError=float(constraint),
                    netVerticalLoad=float(-external[moving,2].sum()), moment=external_moment)

    def escape_flat_region(self, x, state, payout):
        """Probe physical force directions when slack/fully-wet loads give J=0.

        This changes the starting position only. It never adds a stabilizing
        force and every resulting state must pass the usual balance checks.
        """
        residual = state['residual']
        size = np.max(np.abs(residual),initial=0)
        if size == 0:
            return None
        direction = residual/size
        best = np.linalg.norm(residual)
        for distance in (0.001,0.01,0.05,0.1,0.2,0.5,1.0,2.0,5.0,10.0):
            candidate = x+distance*direction
            trial = self.evaluate(candidate,payout)
            value = np.linalg.norm(trial['residual'])
            if value < best*(1-1e-6):
                return candidate
            # A ray crossed a force reversal while skipping a narrow waterline
            # or cable activation region. Locate it with bounded bisection.
            if np.dot(trial['residual'],direction) < 0:
                lo,hi = 0.0,distance
                for _ in range(24):
                    mid = (lo+hi)/2
                    probe = x+mid*direction
                    pstate = self.evaluate(probe,payout)
                    if np.linalg.norm(pstate['residual']) < best*(1-1e-6):
                        return probe
                    if np.dot(pstate['residual'],direction) > 0:
                        lo = mid
                    else:
                        hi = mid
                break
        return None

    def solve(self, payout, previous=None):
        seed = self.x0.copy() if previous is None else previous.copy()
        if self.size == 0:
            state = self.evaluate(seed,payout)
            return seed,state,dict(forceResidualN=0.0,momentResidualNm=0.0,displacementM=0.0,
                                   tensionChangeN=0.0,buoyancyChangeN=0.0,constraintErrorM=state['constraintError'],evaluations=0)
        # Force scaling improves conditioning; physical acceptance uses SI residuals.
        scale = max(1.0, sum(abs(e['mass']*GRAVITY_M_S2) for e in self.model.beams)/max(1,len(self.model.bodies)))
        evaluations = 0
        last = self.evaluate(seed,payout)
        for _ in range(4):
            result = least_squares(lambda x: self.evaluate(x,payout)['residual']/scale,
                                   seed, jac='3-point', jac_sparsity=self.sparsity,
                                   x_scale=1.0, max_nfev=self.tol.max_evaluations,
                                   ftol=1e-12,xtol=1e-12,gtol=1e-12,
                                   tr_options={'atol':1e-10,'btol':1e-10,'maxiter':200})
            evaluations += result.nfev
            state = self.evaluate(result.x,payout)
            displacement = np.max(np.linalg.norm(state['positions']-last['positions'],axis=1),initial=0)
            tension = np.max(np.abs(state['tensions']-last['tensions']),initial=0)
            buoyancy = abs(state['buoyancy']-last['buoyancy'])
            metrics = dict(forceResidualN=state['forceError'],momentResidualNm=state['momentError'],
                           displacementM=float(displacement),tensionChangeN=float(tension),
                           buoyancyChangeN=float(buoyancy),constraintErrorM=state['constraintError'],evaluations=evaluations)
            if (state['forceError'] <= self.tol.force_n and state['momentError'] <= self.tol.moment_nm and
                displacement <= self.tol.displacement_m and tension <= self.tol.tension_n and
                buoyancy <= self.tol.buoyancy_n and state['constraintError'] <= self.tol.constraint_m):
                return result.x,state,metrics
            if state['forceError'] > self.tol.force_n or state['momentError'] > self.tol.moment_nm:
                probe = self.escape_flat_region(result.x,state,payout)
                if probe is not None:
                    seed,last = probe,state
                    continue
            seed, last = result.x,state
        raise EquilibriumError(f'Equilibrium failed at payout {np.asarray(payout).tolist()} m: {metrics}')
