"""Free surface clipping and circular-section hydrostatics (SI, Z upwards)."""
import numpy as np

_GAUSS_X, _GAUSS_W = np.polynomial.legendre.leggauss(12)


def wet_interval(z0, z1, surface=0.0):
    """Parametric interval of a straight line below the surface."""
    z0, z1 = z0 - surface, z1 - surface
    if z0 <= 0 and z1 <= 0:
        return 0.0, 1.0
    if z0 > 0 and z1 > 0:
        return 0.0, 0.0
    crossing = z0 / (z0 - z1)
    return (0.0, crossing) if z0 <= 0 else (crossing, 1.0)


def circular_fraction(depth, radius):
    """Area fraction below water for a circular section."""
    u = np.clip(np.asarray(depth) / radius, -1.0, 1.0)
    return (np.arccos(-u) + u * np.sqrt(np.maximum(0.0, 1 - u*u))) / np.pi


def segment_buoyancy(a, b, full_force, radius=0.0, surface=0.0):
    """Return vertical force and its axial first moment at the wet centroid.

    Circular pipes use Gauss integration of submerged cross-sectional area;
    slender lines use exact clipping. Circular buoyancy is distributed on the
    centerline, neglecting transverse centroid offsets (slender-body model).
    """
    if radius <= 0:
        lo, hi = wet_interval(a[2], b[2], surface)
        fraction = hi - lo
        return full_force * fraction, a + (b-a) * (lo+hi) / 2
    if max(a[2], b[2]) + radius <= surface:
        return full_force, (a+b)/2
    if min(a[2], b[2]) - radius >= surface:
        return 0.0, (a+b)/2
    delta = b-a
    length = np.linalg.norm(delta)
    projected_radius = radius * np.sqrt(max(0.0, 1 - (delta[2]/length)**2))
    if projected_radius < 1e-10:
        return segment_buoyancy(a, b, full_force, surface=surface)
    # Split at fully wet/dry boundaries so quadrature resolves long tilted pipes.
    cuts = [0.0, 1.0]
    if abs(delta[2]) > 1e-12:
        cuts += [t for z in (surface-projected_radius, surface+projected_radius)
                 if 0 < (t := (z-a[2])/delta[2]) < 1]
    cuts.sort()
    x, w = _GAUSS_X, _GAUSS_W
    volume, moment = 0.0, 0.0
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        t = (x+1)*(hi-lo)/2 + lo
        weights = w*(hi-lo)/2 * circular_fraction(surface-a[2]-t*delta[2], projected_radius)
        volume += weights.sum()
        moment += np.dot(weights, t)
    return full_force * volume, a + delta * (moment/volume if volume > 0 else 0.5)
