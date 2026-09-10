"""Conservative collider support heights from per-block live poses.

Rigid primitive colliders are exact. Articulated internal child colliders use
the dump's rest transforms and are flagged: they cannot certify a close flare.
"""
import json,sys,tomllib,itertools
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation as R


class Clearance:
    def __init__(self,bsg,dump):
        registry=dump
        self.parts=[];self.unresolved=[]
        for b in ET.parse(bsg).getroot().findall('Blocks/Block'):
            guid=b.get('guid').lower();bid=b.get('id')
            scale=np.array([float(b.find('Transform/Scale').get(k,'1')) for k in ('x','y','z')])
            for name,c in registry[bid].get('colliders',{}).items():
                if not c['enabled'] or c['is_trigger']:continue
                typ=c['type'];s=np.array(c['local_scale']);q=R.from_quat(np.roll(c['local_rotation'],-1))
                centre=np.array(c.get('center',[0,0,0]));offset=np.array(c['local_position'])
                radius=0.
                if typ=='box':
                    points=np.array(list(itertools.product([-1,1],repeat=3)))*np.array(c['size'])/2+centre
                elif typ=='sphere':
                    points=np.array([centre]);radius=c['radius']*max(abs(s))*max(abs(scale))
                elif typ=='capsule':
                    direction=c.get('direction',1);axis=np.eye(3)[direction]
                    radius=c['radius']*max(abs(s[np.arange(3)!=direction]))*max(abs(scale))
                    half=max(0,c['height']*abs(s[direction])/2-radius/max(abs(scale)))
                    points=np.array([centre*s-axis*half,centre*s+axis*half]);s=np.ones(3)
                else:
                    self.unresolved.append(dict(guid=guid,block_id=bid,collider=name,reason='mesh vertices unavailable'));continue
                points=(q.apply(points*s)+offset)*scale
                self.parts.append((guid,bid,name,points,radius))
                if bid in ('16','18','28') and '/' in c['transform_path']:
                    self.unresolved.append(dict(guid=guid,block_id=bid,collider=name,reason='internal moving child uses rest transform'))

    def minimum(self,targets,block_ids=None):
        best=(float('inf'),None,None)
        for guid,bid,name,points,radius in self.parts:
            if block_ids is not None and bid not in block_ids:continue
            state=targets[guid];rot=R.from_quat(state['rotation'])
            y=float(np.min(rot.apply(points)[:,1])+state['position'][1]-radius)
            if y<best[0]:best=(y,guid,name)
        return dict(world_y=best[0],guid=best[1],collider=best[2])


