"""EXPERIMENTAL spine fit, not a production release gate.

Native BAT4 uses Transform.position and Rigidbody.velocity (COM velocity).
The uncorrected mixed reference points can bias this fit. Pose drift is an
independent observation; a future gate must correct COM offsets or use pose
geometry alone and be validated in an actual dry release.
"""
from collections import deque
import numpy as np
from scipy.spatial.transform import Rotation as R

class RigidRateObserver:
    def __init__(self,hw):
        self.root=hw['guids'][hw['parts']['root']]
        self.spine=[hw['guids'][i] for i in ('1','2','3','4','5','6','7','15','16','17','25','29','271','272','279','280')]
        self.poses=deque()

    def update(self,t,d):
        if self.poses and t-self.poses[-1][0]>.2:self.poses.clear()
        q=R.from_quat(d[self.root]['rotation']);self.poses.append((t,q))
        while len(self.poses)>2 and self.poses[1][0]<=t-1:self.poses.popleft()
        points=np.array([d[g]['position'] for g in self.spine]);velocities=np.array([d[g]['velocity'] for g in self.spine])
        r=points-points.mean(0);v=velocities-velocities.mean(0)
        inertia=np.sum(r*r)*np.eye(3)-r.T@r
        omega=np.linalg.solve(inertia,np.cross(r,v).sum(0))
        residual=float(np.sqrt(np.mean(np.sum((v-np.cross(omega,r))**2,axis=1))))
        dt=t-self.poses[0][0]
        pose_rate=float(np.linalg.norm((q*self.poses[0][1].inv()).as_rotvec())/dt) if dt>=.95 else None
        rate=float(np.linalg.norm(omega));root_rate=float(np.linalg.norm(d[self.root]['angular_velocity']))
        ready=pose_rate is not None and rate<.003 and pose_rate<.003 and residual<.025 and root_rate<.05
        return dict(fit_rate=rate,fit_rate_world=omega.tolist(),pose_rate_over1s=pose_rate,
                    fit_residual_speed=residual,root_rate=root_rate,low_spin_candidate=bool(ready))
