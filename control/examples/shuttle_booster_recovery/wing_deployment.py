"""Feedback on actual spar rotation; a key ACK is not deployment evidence."""
import numpy as np
from scipy.spatial.transform import Rotation as R

class WingDeployment:
    def __init__(self,staging):
        self.s=staging
        self.started=None
        self.stable_since=None
        self.reported=False

    def update(self,t,d,allowed,sliders,commands):
        root=self.s.cores[0]
        body=R.from_quat(d[root]['rotation'])*self.s.saved[root].inv()
        angles={}
        if allowed and self.started is None:
            self.started=t
            self.s.events.append(dict(t=t,event='wing_deployment_started'))
        deploying=self.started is not None
        for side,sign in [('R',-1.),('L',1.)]:
            spine=self.s.guid('ventral'+side)
            rel=body.inv()*R.from_quat(d[spine]['rotation'])*self.s.saved[spine].inv()
            angle=float(rel.as_rotvec()[1])*sign
            angles[side]=float(np.degrees(angle))
            goal=np.pi/2 if deploying else 0.
            for name in ('fold'+side,'foldFront'+side):
                hinge=self.s.guid(name)
                sliders.extend([(hinge,'rotation-speed',.5),(hinge,'tension',2.)])
                if deploying:
                    # Configured vanilla limit is90. Hold the outward target
                    # there; reversing on inertial overshoot backed the native
                    # target off and produced repeated87-91deg oscillation.
                    commands.append((hinge,'leftKey'))
                    continue
                # Before release the native joint target starts at zero.
                # Keep it there: chasing elastic deflection with key pulses
                # changes the target and excites the otherwise fixed joint.
        near=deploying and all(abs(a-90)<2 for a in angles.values())
        if near:
            if self.stable_since is None:self.stable_since=t
        else:self.stable_since=None
        deployed=self.stable_since is not None and t-self.stable_since>1
        if deployed and not self.reported:
            self.s.events.append(dict(t=t,event='wing_deployment_measured',angles_deg=angles))
            self.reported=True
        return dict(angles_deg=angles,started=self.started,deployed=bool(deployed),clearance_allowed=bool(allowed))
