"""Settled baseline followed by a short side pulse; engineering only."""
import numpy as np
import os
from collections import deque
from scipy.spatial.transform import Rotation as R

class TransverseProbe:
    def __init__(self,staging):
        self.s=staging;self.start=None;self.started=False;self.finished=False
        self.recentered=False;self.stable_since=None;self.baseline_at=None;self.pulse_at=None
        self.held_sliders=[];self.held_commands=[];self.aborted=False
        self.poses=deque()
        self.group=os.environ.get('BAILU_SIDE_PULSE_GROUP','upper')
        self.upper_fraction=float(os.environ.get('BAILU_SIDE_UPPER_FRACTION','.229'))
        self.weights={}
        for name in staging.hw['parts']:
            if not name.startswith('transverse'):continue
            lower=name.endswith('Lower')
            weight=(1-self.upper_fraction if lower else self.upper_fraction) if self.group=='balanced' else float(lower==(self.group=='lower'))
            self.weights[staging.guid(name)]=weight
        self.selected=[g for g,w in self.weights.items() if w>0]
    def update(self,t,d,sliders,commands):
        if self.start is None:self.start=t
        elapsed=t-self.start
        root=self.s.cores[0];target=d[root]
        actual=R.from_quat(target['rotation'])*self.s.saved[root].inv()
        if self.poses and t-self.poses[-1][0]>.2:
            self.poses.clear();self.stable_since=None
            if self.baseline_at is not None and not self.finished:
                self.aborted=True
                self.s.events.append(dict(t=t,event='transverse_telemetry_gap_abort',engineering_probe=True))
        self.poses.append((t,actual))
        while len(self.poses)>2 and self.poses[1][0]<=t-1.:self.poses.popleft()
        span=t-self.poses[0][0]
        pose_rate=float((actual*self.poses[0][1].inv()).magnitude()/span) if span>=.95 else None
        rate=float(np.linalg.norm(target['angular_velocity']))
        height=float(np.linalg.norm(np.array(target['position'])-[0,-600,0])-600)
        hold=self.s.momentum_steering
        if not self.recentered and elapsed>5 and rate<.02:
            hold.desired=actual;self.recentered=True
            self.s.events.append(dict(t=t,event='transverse_hold_recentered',rate=rate))
        error=float((hold.desired*actual.inv()).magnitude()) if hold.desired is not None else float('inf')
        ready=self.recentered and pose_rate is not None and pose_rate<.01 and rate<.03 and error<.14 and height>600
        if self.baseline_at is None and not self.aborted:
            if ready:
                if self.stable_since is None:self.stable_since=t
                if t-self.stable_since>=2.:
                    self.baseline_at=t
                    self.held_sliders=[x for x in sliders if x[0] in self.s.all_wheels]
                    self.held_commands=[x for x in commands if x[0] in self.s.all_wheels]
                    self.s.events.append(dict(t=t,event='transverse_frozen_baseline_start',rate=rate,error=error,pose_rate=pose_rate))
            else:self.stable_since=None
        if self.baseline_at is not None and self.pulse_at is None and not self.aborted:
            if rate>.03:
                self.aborted=True
                self.s.events.append(dict(t=t,event='transverse_baseline_rejected',rate=rate,engineering_probe=True))
            elif t-self.baseline_at>=1.:
                self.pulse_at=t;self.started=True
                self.s.events.append(dict(t=t,event='transverse_impulse_start',power=.25,rate=rate,group=self.group,weights=self.weights,guids=self.selected,engineering_probe=True))
        power=.25 if self.pulse_at is not None and t-self.pulse_at<1.2 else 0.
        if self.started and not self.finished and (t-self.pulse_at>=1.2 or rate>.25):
            self.finished=True;self.s.events.append(dict(t=t,event='transverse_impulse_end',engineering_probe=True))
        if self.finished or self.aborted:power=0.
        if self.baseline_at is not None and not self.finished and not self.aborted:
            # Freeze the same wheel commands over baseline and pulse so a new
            # feedback response does not masquerade as engine pitch torque.
            sliders[:]=[x for x in sliders if x[0] not in self.s.all_wheels]
            commands[:]=[x for x in commands if x[0] not in self.s.all_wheels]
            sliders.extend(self.held_sliders);commands.extend(self.held_commands)
        for g in self.s.transverse_engines:
            value=power*self.weights.get(g,0.)
            sliders.append((g,'fthrust',value))
            if value:commands.append((g,'ThrustKey'))
        return dict(engineering_probe=True,elapsed=elapsed,power=power,guids=self.s.transverse_engines,
                    rate=rate,pose_rate=pose_rate,error_deg=float(np.degrees(error)),recentered=self.recentered,
                    baseline_at=self.baseline_at,pulse_at=self.pulse_at,finished=self.finished,aborted=self.aborted,group=self.group,weights=self.weights)
