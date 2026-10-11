"""Normalize AquaSim inputs into rigid assemblies and axial cable/net edges."""
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from simulation.model_io import _read_model, _number, GRAVITY_M_S2


@dataclass
class Body:
    nodes: np.ndarray
    center: np.ndarray
    local: np.ndarray
    scale: float


@dataclass
class Model:
    metadata: dict
    positions: np.ndarray
    fixed: np.ndarray
    bodies: list
    particles: np.ndarray
    owner: np.ndarray
    edges: list
    beams: list
    forces: np.ndarray
    moments: np.ndarray


def connected_groups(edges):
    adjacency = {}
    for i, edge in enumerate(edges):
        for n in (edge['a'], edge['b']):
            adjacency.setdefault(n, []).append(i)
    unseen, groups = set(range(len(edges))), []
    while unseen:
        stack, group = [unseen.pop()], []
        while stack:
            i = stack.pop()
            group.append(i)
            for n in (edges[i]['a'], edges[i]['b']):
                for j in adjacency[n]:
                    if j in unseen:
                        unseen.remove(j)
                        stack.append(j)
        groups.append(group)
    return groups


def load_model(path: Path, density=1025.0, include_membrane=True):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"Cannot read AquaSim model {path}: {error}") from error
    data = _read_model(path, density, root=root)
    components = root.find('Components')
    index = {n: i for i, n in enumerate(data['nodeIds'])}
    pos = np.asarray(data['nodePositionsM'], dtype=float)
    fixed_ids = set(data['fixedNodeIds'])
    fixed = np.asarray([n in fixed_ids for n in data['nodeIds']])
    beams, edges = [], []
    beam_props = {v['componentId']: v for v in data['beamLoads']}
    for comp in components.findall('beam'):
        props = beam_props[int(comp.get('id'))]
        wizard = comp.find('wizard')
        radius = 0.0
        if wizard is not None and wizard.get('type', '').lower() == 'circular':
            radius = (_number(wizard, 'outerDiameter', positive=True) or 0) / 2000
        for el in comp.findall('.//element'):
            a = index[int(el.get('StartNode_ID') or el.get('startNodeId'))]
            b = index[int(el.get('EndNode_ID') or el.get('endNodeId'))]
            length = np.linalg.norm(pos[b]-pos[a])
            if length <= 0:
                raise ValueError('Zero-length beam element.')
            beams.append(dict(a=a, b=b, length=length, radius=radius,
                              mass=props['massPerMkg']*length,
                              buoyancy=(props['massPerMkg']-props['submergedMassPerMkg'])*length*GRAVITY_M_S2))
    bodies, owner = [], np.full(len(pos), -1, dtype=int)
    for group in connected_groups(beams):
        nodes = np.unique([n for i in group for n in (beams[i]['a'], beams[i]['b'])])
        if fixed[nodes].any():
            fixed[nodes] = True
            continue
        mass = sum(beams[i]['mass'] for i in group)
        center = (sum(beams[i]['mass']*(pos[beams[i]['a']]+pos[beams[i]['b']])/2 for i in group)/mass
                  if mass > 0 else pos[nodes].mean(axis=0))
        local = pos[nodes]-center
        owner[nodes] = len(bodies)
        bodies.append(Body(nodes, center, local, max(1.0, np.linalg.norm(local, axis=1).max())))
    winches = {w['componentId']: i for i, w in enumerate(data['winches'])}
    for comp in components.findall('truss'):
        mat = comp.find('mooring')
        if mat is None:
            raise ValueError(f"Truss {comp.get('name')} has no material.")
        area, young = _number(mat, 'areal', positive=True), _number(mat, 'young', positive=True)
        if area is None or young is None:
            raise ValueError(f"Truss {comp.get('name')} requires positive area and Young modulus.")
        air = _number(mat, 'weightInAir')
        if air is None:
            rho = _number(mat, 'massdensity', positive=True)
            if rho is None:
                raise ValueError(f"Truss {comp.get('name')} requires air line mass or material density.")
            air = rho*area
        if air < 0:
            raise ValueError(f"Truss {comp.get('name')} air line mass must be nonnegative.")
        water = _number(mat, 'weightInWater')
        water = water if water is not None else air-density*area
        cid = int(comp.get('id'))
        for el in comp.findall('.//element'):
            a = index[int(el.get('StartNode_ID') or el.get('startNodeId'))]
            b = index[int(el.get('EndNode_ID') or el.get('endNodeId'))]
            length = np.linalg.norm(pos[b]-pos[a])
            if length <= 1e-9:
                raise ValueError('Zero-length cable element.')
            edges.append(dict(a=a, b=b, length=length, ea=young*area, air=air, water=water,
                              tensionOnly=mat.get('noCompressionForces', 'true').lower() in ('true', '1'),
                              winch=winches.get(cid, -1), fraction=0.0, output=True,
                              componentId=cid, componentName=comp.get('name', ''), elementId=int(el.get('id'))))
    for cid in winches:
        matching = [e for e in edges if e['componentId'] == cid]
        for group in connected_groups(matching):
            degree = {}
            for i in group:
                for n in (matching[i]['a'], matching[i]['b']):
                    degree[n] = degree.get(n, 0)+1
            if max(degree.values()) > 2 or sum(v == 1 for v in degree.values()) != 2:
                raise ValueError('Winch routing must be an unbranched open cable path.')
            total = sum(matching[i]['length'] for i in group)
            for i in group:
                matching[i]['fraction'] = matching[i]['length']/total
    if include_membrane:
        for comp in components.findall('membrane'):
            ea = (_number(comp, 'emodule', positive=True) or 1e9)*(_number(comp, 'areal', positive=True) or 2.5e-6)
            area = _number(comp, 'areal', positive=True) or 2.5e-6
            rho = _number(comp, 'massDensity', positive=True) or 1000
            seen = set()
            for el in comp.findall('.//element'):
                try:
                    ns = [index[int(el.get(k) or el.get(k[0].upper()+k[1:]))] for k in ('nodeA','nodeB','nodeC','nodeD')
                          if el.get(k) is not None or el.get(k[0].upper()+k[1:]) is not None]
                except (KeyError, ValueError) as error:
                    raise ValueError(f"Net element {el.get('id')} has an invalid node reference.") from error
                if len(ns) not in (3, 4):
                    raise ValueError('Net elements must have three or four valid nodes.')
                for i, a in enumerate(ns):
                    for b in ns[i+1:]:
                        key = tuple(sorted((a,b)))
                        if key in seen:
                            continue
                        seen.add(key)
                        length = np.linalg.norm(pos[b]-pos[a])
                        if length <= 1e-9:
                            raise ValueError('Zero-length net edge.')
                        edges.append(dict(a=a,b=b,length=length,ea=ea,air=rho*area,water=(rho-density)*area,
                                          tensionOnly=True,winch=-1,fraction=0.0,output=False))
        data['warnings'].append('Netting uses tension-only axial edges and quad diagonals; mesh homogenization, shell bending and hydrodynamic loads are not represented.')
    active = set(n for e in edges+beams for n in (e['a'],e['b']))
    forces, moments = np.zeros_like(pos), np.zeros_like(pos)
    for load in data['nodeLoads']:
        i = index[load['nodeId']]
        forces[i] += load['forceN']
        moments[i] += load['momentNm']
        active.add(i)
    particles = np.asarray([i for i in sorted(active) if not fixed[i] and owner[i] < 0], dtype=int)
    if np.any(np.linalg.norm(moments[particles], axis=1) > 0):
        raise ValueError('A point moment on a cable/net node requires a rotational structural element.')
    data['fixedNodeIds'] = [data['nodeIds'][i] for i in np.flatnonzero(fixed)]
    data['assumptions'] = [
        'Quasi-static force and moment equilibrium; no inertial, wave, current, drag or added-mass loads.',
        'Z=0 is the free surface; water is below it. Explicit supports and upper winch endpoints are fixed.',
        'Connected beam elements form rigid assemblies with six pose degrees of freedom; beam bending is omitted.',
        'HMPE uses source-model axial EA and tension-only elasticity; creep and hysteresis are omitted.',
        'Cable lengths initialize stress-free; ambiguous source pretension is ignored.',
        'Winch payout is positive for lowering and negative for retrieval, shared proportionally along each open cable path.',
        'Pipe cross-sectional immersion is integrated along the axis; transverse buoyancy centroid offsets are neglected.',
        'Winch attachment connectivity follows model node IDs; no implicit fairleads or synthetic links are added.',
    ]
    return Model(data,pos,fixed,bodies,particles,owner,edges,beams,forces,moments)
