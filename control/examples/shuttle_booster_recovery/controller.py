"""Current dry shuttle ascent characterization. Never declares mission success."""
import json, os
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation as R
from controller_sdk import ControllerClient, targets_by_guid
from controller_sdk.protocol import ENV_MACHINE_BSG, ENV_RUN_DIR

hw=json.loads((Path(os.environ[ENV_RUN_DIR])/'dry_hardware.json').read_text())
Path(os.environ[ENV_RUN_DIR],'field_guidance_snapshot.py').write_text(Path(__file__).with_name('field_guidance.py').read_text())
Path(os.environ[ENV_RUN_DIR],'hardware_snapshot.json').write_text(json.dumps(hw,indent=2))
Path(os.environ[ENV_RUN_DIR],'ascent_controller_snapshot.py').write_text(Path(__file__).read_text())
calibration_path=(Path(os.environ[ENV_RUN_DIR])/'servo_calibration.json')
if calibration_path.exists():Path(os.environ[ENV_RUN_DIR],'servo_calibration_snapshot.json').write_text(calibration_path.read_text())
bs=ET.parse(os.environ[ENV_MACHINE_BSG]).getroot().findall('Blocks/Block')
by_guid={b.get('guid').lower():b for b in bs}
assert set(by_guid)==set(hw['guids'].values())
guid=lambda name:hw['guids'][hw['parts'][name]]
saved={g:R.from_quat([float(b.find('Transform/Rotation').get(k)) for k in ('x','y','z','w')]) for g,b in by_guid.items()}
pos={g:np.array([float(b.find('Transform/Position').get(k)) for k in ('x','y','z')]) for g,b in by_guid.items()}
all_engine_guids=[hw['guids'][a['id']] for a in hw['order'] if a['type']=='Booster']
retro_engines={guid(name) for name in hw['parts'] if name.startswith(('retro','transverse'))}
engines=[g for g in all_engine_guids if g not in retro_engines]
wheels=[hw['guids'][a['id']] for a in hw['order'] if a['type']=='Reaction Steering Block']
tanks=[hw['guids'][a['id']] for a in hw['order'] if a['type'] in ('Fuel Barrel','Fuel Barrel Big')]
points=np.array([pos[g] for g in engines]);points-=points.mean(0)
A=np.vstack([np.ones(len(engines)), -points[:,2],points[:,0]])
mix=np.linalg.pinv(A)
center=np.array([0.,-600.,0.]);gm=8456800.
root=guid('root');rows=[];c=ControllerClient.from_environment(poll_interval=.005);c.arm()
duration=float(os.environ.get('BAILU_DURATION','40'))
staging=None
if os.environ.get('BAILU_ORBITAL_STAGING')=='1':
    if os.environ.get('BAILU_RETURN')=='1':
        from return_staging import ReturnStaging as OrbitalStaging
        Path(os.environ[ENV_RUN_DIR],'return_controller_snapshot.py').write_text(Path(__file__).with_name('return_staging.py').read_text())
        Path(os.environ[ENV_RUN_DIR],'momentum_steering_snapshot.py').write_text(Path(__file__).with_name('momentum_steering.py').read_text())
        Path(os.environ[ENV_RUN_DIR],'paired_spin_snapshot.py').write_text(Path(__file__).with_name('paired_spin.py').read_text())
        Path(os.environ[ENV_RUN_DIR],'pose_release_observer_snapshot.py').write_text(Path(__file__).with_name('pose_release_observer.py').read_text())
        if os.environ.get('BAILU_ENTRY_ALIGNED_BURN')=='1':
            Path(os.environ[ENV_RUN_DIR],'entry_aligned_burn_snapshot.py').write_text(Path(__file__).with_name('entry_aligned_burn.py').read_text())
            Path(os.environ[ENV_RUN_DIR],'axial_burn_trim_snapshot.py').write_text(Path(__file__).with_name('axial_burn_trim.py').read_text())
            Path(os.environ[ENV_RUN_DIR],'native_thruster_force_snapshot.py').write_text(Path(__file__).with_name('native_thruster_force.py').read_text())
        Path(os.environ[ENV_RUN_DIR],'rigid_rate_observer_snapshot.py').write_text(Path(__file__).with_name('rigid_rate_observer.py').read_text())
        if os.environ.get('BAILU_TRANSVERSE_PROBE')=='1':
            Path(os.environ[ENV_RUN_DIR],'transverse_probe_snapshot.py').write_text(Path(__file__).with_name('transverse_probe.py').read_text())
        if os.environ.get('BAILU_WHEEL_RESPONSE')=='1':
            Path(os.environ[ENV_RUN_DIR],'wheel_response_snapshot.py').write_text(Path(__file__).with_name('wheel_response.py').read_text())
        if os.environ.get('BAILU_MOMENTUM_HOLD')=='1':
            Path(os.environ[ENV_RUN_DIR],'momentum_steering_snapshot.py').write_text(Path(__file__).with_name('momentum_steering.py').read_text())
        if os.environ.get('BAILU_RTLS')=='1':
            Path(os.environ[ENV_RUN_DIR],'booster_controller_snapshot.py').write_text(Path(__file__).with_name('booster_return.py').read_text())
        if os.environ.get('BAILU_GLIDER')=='1':
            Path(os.environ[ENV_RUN_DIR],'glider_controller_snapshot.py').write_text(Path(__file__).with_name('glider_return.py').read_text())
            Path(os.environ[ENV_RUN_DIR],'blade_servo_control_snapshot.py').write_text(Path(__file__).with_name('blade_servo_control.py').read_text())
    else:
        from orbital_staging import OrbitalStaging
    history=json.loads(Path(__file__).with_name('machine.json').read_text())
    Path(os.environ[ENV_RUN_DIR],'history_snapshot.json').write_text(json.dumps(history))
    staging=OrbitalStaging(hw,history,saved)
    if 'foldR' in hw['parts']:
        Path(os.environ[ENV_RUN_DIR],'deployment_controller_snapshot.py').write_text(Path(__file__).with_name('wing_deployment.py').read_text())
    Path(os.environ[ENV_RUN_DIR],'staging_controller_snapshot.py').write_text(Path(__file__).with_name('orbital_staging.py').read_text())
try:
    f=c.wait_until_running(timeout=60);c.load_block_table();t0=f.simulation_time;last=0.;report=-2.;smooth=np.zeros(3);vi=0.;trim=np.zeros(3);coast=False
    # Folded wing mass is aft of the engine plane in body Z. The first
    # dual-hinge ascent measured a persistent negative pitch acceleration.
    trim[0]=2. if 'foldFrontR' in hw['parts'] else 0.
    while True:
        t=f.simulation_time-t0;dt=max(.001,min(.2,t-last));last=t
        d=targets_by_guid(f,block_guids=c._block_guids);a=d[root]
        # Mean block state reduces root lever-arm motion; it is not a mass-weighted COM.
        p=np.mean([x['position'] for x in d.values()],axis=0);v=np.mean([x['velocity'] for x in d.values()],axis=0);omega=np.array(a['angular_velocity'])
        rot=R.from_quat(a['rotation'])*saved[root].inv();up=rot.apply([0,1,0])
        smooth+=(omega-smooth)*min(1.,dt/.3)
        rel=p-center;radius=np.linalg.norm(rel);rad=rel/radius;h=radius-600;vr=v@rad
        east=np.cross([0,0,-1],rad);east/=np.linalg.norm(east)
        vt=v@east;tangential=v-vr*rad;crossrange_velocity=tangential-east*vt
        gravity=min(22.,gm/radius**2)
        target_alt=float(os.environ.get('BAILU_ALTITUDE','850' if 'elevonBaseR' in hw['parts'] else '650'))
        target_vr=np.clip((target_alt-h)*.18,-12,30)
        # return26 lost radial speed28->5 while ramping to orbital horizontal
        # speed across only200 altitude units. Spread the pitch transition
        # over600 so the heavier booster heads do not demand a rapid turn.
        target_vt=np.sqrt(gm/(600+target_alt))*np.clip((h-400)/600,0,1)
        vi=np.clip(vi+(target_vr-vr)*dt*.2,-8,8) if t>.5 else 0.
        drag_ff=10*np.clip((500-h)/500,0,1) if target_vt>0 else 0
        # Folded blades induce crossrange motion (return21: full speed74
        # versus east speed61 near insertion). Correct both tangential axes;
        # using east speed alone also overestimated radial thrust demand.
        crossrange_accel=-crossrange_velocity*2.
        crossrange_accel*=min(1.,20./max(np.linalg.norm(crossrange_accel),1e-9))
        accel=rad*(gravity-(tangential@tangential)/radius+np.clip((target_vr-vr)*2.5+vi,-25,25))+east*np.clip((target_vt-vt)*4+drag_ff,-30,60)+crossrange_accel
        wanted=accel/max(1e-6,np.linalg.norm(accel));err=np.cross(up,wanted)
        torque=rot.inv().apply(.55*err-2.4*omega)
        collective=np.clip(np.dot(accel,up)/(3.5*len(engines)),0,2)
        if 'foldFrontR' in hw['parts'] and h<100:collective=min(collective,1.2)
        localerr=rot.inv().apply(err)
        if t>.5:trim=np.clip(trim+localerr*dt*2,-4,4)
        etorque=rot.inv().apply(5*err-6*smooth)+trim
        # Keep differential authority inside available collective; avoid
        # generating unintended net thrust while commanding a zero burn.
        authority=min(.8,float(collective),float(2-collective))
        u=np.clip(collective+np.clip(mix@np.array([0,etorque[0]*2,etorque[2]*2]),-authority,authority),0,2)
        if t<.5:u*=0
        sliders=[(g,'fthrust',float(x)) for g,x in zip(engines,u)]
        sliders.extend((g,'fthrust',0.) for g in retro_engines)
        commands=[(g,'ThrustKey') for g,x in zip(engines,u) if x>.01]
        for g in wheels:
            axis=-saved[g].apply([0,0,1]);val=torque@axis
            sliders.extend([(g,'speed',.5),(g,'damper',.5)])
            if abs(val)>.04:commands.append((g,'LeftKey' if val>0 else 'RightKey'))
        hv=np.cross(rel,v);energy=v@v/2-gm/radius;ecc=np.sqrt(max(0,1+2*energy*(hv@hv)/gm**2));peri=(hv@hv)/gm/(1+ecc)-600
        apo=(hv@hv)/gm/(1-ecc)-600 if ecc<1 else float('inf')
        insertion_apo_limit=target_alt+float(os.environ.get('BAILU_INSERTION_APO_MARGIN','350'))
        if peri>max(550,target_alt-100) and h>max(560,target_alt-90) and apo<insertion_apo_limit and abs(vr)<15:coast=True
        if coast:
            u*=0
            sliders=[(g,'fthrust',0.) for g in engines]
            commands=[]
            damping=rot.inv().apply(-2.4*omega)
            for g in wheels:
                val=damping@(-saved[g].apply([0,0,1]))
                sliders.extend([(g,'speed',.5),(g,'damper',.5)])
                if abs(val)>.04:commands.append((g,'LeftKey' if val>0 else 'RightKey'))
        item=dict(t=t,coast=coast,state_reference='mean block positions and velocities',altitude=h,vr=vr,vt=vt,periapsis_altitude=peri,apoapsis_altitude=apo if np.isfinite(apo) else None,fuel=sum(d[g]['fuel'][0] for g in tanks),tilt=float(np.degrees(np.arccos(np.clip(up@rad,-1,1)))),power=u.tolist(),alive=f.machine.alive_block_count,targets=d)
        item.update(crossrange_speed=float(np.linalg.norm(crossrange_velocity)),tangential_speed=float(np.linalg.norm(tangential)))
        item['insertion_apo_limit']=insertion_apo_limit
        if staging:
            if not coast and hasattr(staging,'ascent_engine_trim'):
                staging.ascent_engine_trim=trim.copy()
            sliders,commands,item['staging']=staging.update(t,d,coast,sliders,commands)
            final_power={g:val for g,key,val in sliders if key=='fthrust'}
            item['power']=[final_power.get(g,0.) for g in all_engine_guids]
            item['orbiter_active_channels']=[(command[0],command[1]) for command in commands if command[0] in staging.groups[0]]
        rows.append(item)
        if t-report>(10 if staging else 2):print({k:item[k] for k in ('t','altitude','vr','vt','fuel','tilt')},item.get('staging',''),flush=True);report=t
        if f.machine.alive_block_count!=len(bs):raise RuntimeError('Lost blocks')
        if t>8 and h<8 and item['fuel']>rows[0]['fuel']-.5:raise RuntimeError('No thrust/fuel response to ascent commands')
        if t>duration:break
        c.send_sliders(sliders);c.send_channels(channels=commands);f=c.next_sample(timeout=5)
finally:
    Path(os.environ[ENV_RUN_DIR],'ascent.json').write_text(json.dumps(dict(frames=rows,events=staging.events if staging else [],mission_complete=False)))
    c.close()
