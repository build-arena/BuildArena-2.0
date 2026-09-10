"""Orbit-lap and clean-coupler separation trial for the dry hardware."""
import re,os
import numpy as np
from scipy.spatial.transform import Rotation as R

class OrbitalStaging:
    def __init__(self, hw, history, saved):
        self.hw=hw;self.saved=saved;self.sweep=0.;self.previous=None
        self.separated_at=None;self.events=[];self.guid=lambda n:hw['guids'][hw['parts'][n]]
        parents={}
        for ev in history:
            m=re.search(r'Added Build ID (\d+):',ev.get('result',''))
            if m and ev['op']=='attach_block_to':parents[m[1]]=str(ev['params']['base_block'])
        def owner(i):
            while i in parents:
                if i==hw['parts']['baseR']:return 1
                if i==hw['parts']['baseL']:return -1
                i=parents[i]
            return 0
        physical_ids=list(hw['guids'])
        self.groups={s:[hw['guids'][i] for i in physical_ids if owner(i)==s] for s in [-1,0,1]}
        self.wheels={s:[hw['guids'][a['id']] for a in hw['order'] if a['type']=='Reaction Steering Block' and owner(a['id'])==s] for s in [-1,0,1]}
        self.cores={0:self.guid('root'),1:self.guid('lowerR'),-1:self.guid('lowerL')}
        self.deployment=None
        if 'foldR' in hw['parts']:
            from wing_deployment import WingDeployment
            self.deployment=WingDeployment(self)
    def update(self,t,d,coast,sliders,commands):
        # BAT4 omits six orbiter armor targets on this machine. Use observed
        # group members for centers; core states below remain mandatory.
        positions={s:np.mean([d[g]['position'] for g in gs if g in d],axis=0) for s,gs in self.groups.items()}
        reference=np.mean(list(positions.values()),axis=0)-[0,-600,0]
        radial=reference/np.linalg.norm(reference)
        if coast and self.separated_at is None:
            if self.previous is not None:
                self.sweep+=np.arctan2(np.linalg.norm(np.cross(self.previous,radial)),np.dot(self.previous,radial))
            self.previous=radial
            omega=np.linalg.norm(d[self.cores[0]]['angular_velocity'])
            if self.sweep>=np.deg2rad(float(os.environ.get('BAILU_STAGE_SWEEP_DEG','360'))) and omega<.08:
                self.separated_at=t
                self.events.append(dict(t=t,event='simultaneous_coupler_release',sweep_degrees=float(np.degrees(self.sweep))))
        if self.separated_at is not None:
            # All engines are already shut down by the coast controller.
            commands=[]
            for s in [-1,1]:
                g=self.cores[s];rot=R.from_quat(d[g]['rotation'])*self.saved[g].inv()
                damping=rot.inv().apply(-2.4*np.array(d[g]['angular_velocity']))
                for w in self.wheels[s]:
                    val=damping@(-self.saved[w].apply([0,0,1]))
                    if abs(val)>.04:commands.append((w,'LeftKey' if val>0 else 'RightKey'))
            if t-self.separated_at<.2:
                for name in ['sepR','sepL','upperSepR','upperSepL']:
                    g=self.guid(name);sliders.append((g,'epower',2.));commands.append((g,'DetachKey'))
        distances={f'{a}:{b}':float(np.linalg.norm(positions[a]-positions[b])) for a,b in [(-1,0),(1,0),(-1,1)]}
        extra={}
        if self.deployment:
            allowed=self.separated_at is not None and t-self.separated_at>5 and min(distances['-1:0'],distances['1:0'])>15
            extra['deployment']=self.deployment.update(t,d,allowed,sliders,commands)
        return sliders,commands,dict(sweep_degrees=float(np.degrees(self.sweep)),separated_at=self.separated_at,center_distances=distances,**extra)
