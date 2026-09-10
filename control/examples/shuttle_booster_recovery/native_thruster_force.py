"""Flight-control estimator for the unmodified Booster prefab, not game physics.

Read-only source: level0 Booster/ThrusterBlockBehaviour in thruster_prefabs.json;
GetForce in ThrusterBlockBehaviour.decompiled.cs. Ignores ground/jet-contact
bonuses, so predictions need runtime checks. Native slider range is0..2.
"""

FULL_FORCE_PER_SETTING=600.*.35

def force_for_throttle(power,axial_velocity):
    power=float(power);v=float(axial_velocity)
    if power<=0:return 0.
    thrust=600.
    if v>0:thrust-=min((v*.5/power)**2,600.*.75)
    force=power*thrust*.35
    return force-min(max(v,0.),force)*.45

def throttle_for_force(required_force,axial_velocity,limit=2.):
    if required_force<=0:return 0.
    if force_for_throttle(limit,axial_velocity)<=required_force:return float(limit)
    low=0.;high=float(limit)
    for _ in range(36):
        middle=(low+high)/2
        if force_for_throttle(middle,axial_velocity)<required_force:low=middle
        else:high=middle
    return (low+high)/2
