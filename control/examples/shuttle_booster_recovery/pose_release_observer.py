"""Experimental geometry-only angular-motion observation, not a landing certificate.

Uses Transform origins consistently at two times; never mixes origin positions
with COM velocities. Multiple intervals reject sustained rotation and deformation.
Actual postrelease motion must validate the experimental release policy.
"""
from collections import deque
import numpy as np
from scipy.spatial.transform import Rotation as R

class PoseReleaseObserver:
    def __init__(self,hw):
        self.root=hw['guids'][hw['parts']['root']]
        ids=('1','2','3','4','5','6','7','15','16','17','25','29','271','272','279','280')
        self.guids=[hw['guids'][i] for i in ids]
        self.history=deque()

    def update(self,t,d):
        if any(g not in d for g in self.guids):
            self.history.clear();return {'ready':False,'reason':'missing_spine_target'}
        if self.history and (t-self.history[-1][0]>.12 or t<=self.history[-1][0]):self.history.clear()
        points=np.array([d[g]['position'] for g in self.guids]);q=R.from_quat(d[self.root]['rotation'])
        self.history.append((t,points,q))
        while len(self.history)>2 and self.history[1][0]<t-2.12:self.history.popleft()
        raw=float(np.linalg.norm(d[self.root]['angular_velocity']))
        out={'ready':False,'raw_root_rate':raw,'windows':{}}
        if t-self.history[0][0]<2.:return out
        b=points-points.mean(axis=0)
        for seconds in (.2,1.,2.):
            ref=min(self.history,key=lambda x:abs(x[0]-(t-seconds)))
            dt=t-ref[0]
            if abs(dt-seconds)>.061:return out
            a=ref[1]-ref[1].mean(axis=0)
            delta,_=R.align_vectors(b,a)
            residual=float(np.sqrt(np.mean(np.sum((delta.apply(a)-b)**2,axis=1))))
            root_delta=q*ref[2].inv()
            out['windows'][str(seconds)]={'dt':dt,'geometry_rate':float(delta.magnitude()/dt),
                'root_pose_rate':float(root_delta.magnitude()/dt),'fit_residual':residual,
                'root_agreement_angle':float((delta*root_delta.inv()).magnitude())}
        out['ready']=bool(raw<.03 and all(w['geometry_rate']<.003 and w['root_pose_rate']<.003
                             and w['fit_residual']<.001 and w['root_agreement_angle']<.001
                             for w in out['windows'].values()))
        return out
