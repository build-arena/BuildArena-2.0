"""Hybrid closed-loop spiral and landing mission with experimental limits."""
import json,os,time
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation as R
from controller_sdk import ControllerClient,targets_by_guid
from controller_sdk.protocol import ENV_MACHINE_BSG,ENV_RUN_DIR
from telemetry_wait import next_frame
from hybrid_control import allocate,attitude,Helix,Join,AttitudeGovernor
from clearance import Clearance
from runtime_config import MECHANICAL_MODEL, COLLIDERS


def main(scenario='bank'):
    tactical=scenario=='tactical'
    mission=scenario in ('mission','tactical')
    cruise_height=130 if tactical else 50
    bs=ET.parse(os.environ[ENV_MACHINE_BSG]).getroot().findall('Blocks/Block')
    ids=lambda i:[b.get('guid').lower() for b in bs if b.get('id')==str(i)]
    pos={b.get('guid').lower():np.array([float(b.find('Transform/Position').get(k)) for k in ('x','y','z')]) for b in bs}
    root=ids(0)[0];fans=ids(14);rockets=ids(90);wheels=ids(46);pistons=ids(18);susp=ids(16);yaw=ids(101)[0]
    axes={b.get('guid').lower():R.from_quat([float(b.find('Transform/Rotation').get(k)) for k in ('x','y','z','w')]).apply([0,0,-1]) for b in bs if b.get('id')=='90'}
    lift=[g for g in rockets if axes[g][1]>.9];axial=[g for g in rockets if g not in lift]
    actuators=fans+lift+axial;kinds=['fan']*len(fans)+['lift']*len(lift)+['axial']*len(axial)
    blades=ids(26);hinges=ids(28);folds=[g for g in hinges if abs(pos[g][2])<.1]
    steering=[g for g in hinges if g not in folds]
    front={h:next(w for w in wheels if pos[w][2]>0 and pos[w][0]*pos[h][0]>0) for h in steering}
    steering_origins={};steering_signs={};steering_since=None
    assets=Path(__file__).resolve().parent
    model=MECHANICAL_MODEL
    c=ControllerClient.from_environment(poll_interval=.005);c.arm();rows=[];events=[];failure='';completed=False
    governor=AttitudeGovernor();governed_previous=False
    smooth=os.environ.get('NIGHTBLADE_SMOOTH','1')=='1'
    phase='settle';since=0.;last=0.;report=-3;integral=np.zeros(3);qprevious=None;wprevious=np.zeros(3);failure_frame=None
    join=None;helix=None;path_origin=None;path_base=R.from_euler('y',90,degrees=True);roll_integral=0.;velocity_integral=np.zeros(3)
    turn_heading=0.;turn_origin=None;landing_heading=0.;hold_point=None;landing_origin=None;flare_pitch_start=0.;level_rotation=None;flare_rotation=None;level_target=None
    clearance_model=Clearance(os.environ[ENV_MACHINE_BSG],COLLIDERS) if mission else None
    flare_height=float(os.environ.get('NIGHTBLADE_FLARE_HEIGHT','7.0'));contact_duration=0.
    try:
        f=c.wait_until_running(timeout=60);c.load_block_table();t0=f.simulation_time;d=targets_by_guid(f,block_guids=c._block_guids);ground=d[root]['position'][1]
        keys={(g,i):next(x.name for x in c.channels if x.block_guid==g and x.keylist_index==i) for g in folds for i in (0,1)}
        steer_keys={(g,i):next(x.name for x in c.channels if x.block_guid==g and x.keylist_index==i) for g in steering for i in (0,1)}
        channel_indexes={(x.block_guid,x.name):x.index for x in c.channels}
        slider_indexes={(x.block_guid,x.name):x.index for x in c.sliders}
        def change(name,t):
            nonlocal phase,since
            phase=name;since=t;events.append(dict(t=t,phase=name));print('PHASE',round(t,2),name,flush=True)
        while True:
            iteration_start=time.perf_counter()
            t=f.simulation_time-t0;elapsed=max(0.,t-last);dt=np.clip(elapsed,.001,.16);last=t;e=t-since
            d=targets_by_guid(f,block_guids=c._block_guids);p=np.array(d[root]['position']);v=np.array(d[root]['velocity']);rot=R.from_quat(d[root]['rotation']);omega=np.array(d[root]['angular_velocity']);up=rot.apply([0,1,0])
            clearance=clearance_model.minimum(d) if clearance_model else None
            wheel_clearance=clearance_model.minimum(d,('46',)) if clearance_model else None
            body_velocity=rot.inv().apply(v)
            steering_angles={}
            for h,w in front.items():
                axle=rot.inv().apply(R.from_quat(d[w]['rotation']).apply([0,0,1]))*np.sign(pos[w][0])
                steering_angles[h]=float(np.degrees(np.arctan2(-axle[2],axle[0])))
            if phase=='settle' and e>.8 and steering_since is None:
                steering_origins=steering_angles.copy();steering_since=t
            if steering_since is not None and not steering_signs and t-steering_since>.28:
                for h in steering:
                    delta=steering_angles[h]-steering_origins[h]
                    if abs(delta)<1:raise RuntimeError('Front-wheel steering calibration failed')
                    steering_signs[h]=float(np.sign(delta))
            if clearance and phase in ('flare','level_after_flare') and clearance['world_y']<-.08:
                failure_frame=dict(t=t,pos=p.tolist(),vel=v.tolist(),clearance=clearance,targets=d)
                raise RuntimeError('Premature ground contact during flare')
            if phase=='touchdown':
                near=clearance['world_y']<.015 and abs(v[1])<.4 and np.linalg.norm(v[[0,2]])<.5
                contact_duration=contact_duration+dt if near else 0.
            if phase=='landing_commit':
                near=wheel_clearance['world_y']<.12 and -1.2<v[1]<.3 and 4<body_velocity[2]<16 and abs(body_velocity[0])<2.5 and up[1]>.94
                contact_duration=contact_duration+elapsed if near else 0.
            rel={g:rot.inv().apply(np.array(d[g]['position'])-p) for g in actuators}
            connection=max(np.linalg.norm((rel[g]-(pos[g]-pos[root]))[[1,2]]) for g in fans)
            if f.machine.alive_block_count!=len(bs) or connection>.45:
                failure_frame=dict(t=t,pos=p.tolist(),vel=v.tolist(),connection=connection,targets=d)
                raise RuntimeError(f'Structure guard {connection:.3f}')
            if t>160 or p[1]<ground-1 or (up[1]<.35 and phase not in ('spiral_entry','spiral','spiral_recover','flare','level_after_flare')):raise RuntimeError('Flight envelope guard')
            if phase=='settle' and e>4:change('full_wheel_run',t)
            elif phase=='full_wheel_run' and e>3:change('rolling_launch',t)
            elif phase=='rolling_launch' and p[1]-ground>8:change('climb',t)
            elif phase=='climb' and abs(p[1]-ground-cruise_height)<1 and abs(v[1])<1:change('cruise',t)
            elif phase=='cruise' and (scenario=='spiral' or mission) and e>.5 and (not tactical or abs(v[2])<1):
                path_origin=p.copy();helix=Helix([0,0,0],radius=30,forward=20,gravity=model['gravity'],load=1.8,ramp=2.5,turns=2 if tactical else 1)
                change('spiral',t)
            elif phase=='spiral_entry' and e>join.duration:change('spiral',t)
            elif phase=='spiral' and e>helix.duration:
                if tactical:
                    landing_heading=float(np.arctan2(v[0],v[2]));velocity_integral*=0.;change('dive_align',t)
                elif mission:
                    turn_heading=float(np.arctan2(v[0],v[2]));turn_origin=p.copy();velocity_integral*=0.;change('large_turn',t)
                else:change('spiral_recover',t)
            elif phase=='large_turn' and e>np.pi/(25/70):
                landing_heading=turn_heading+np.pi;change('glide',t)
            elif phase=='dive_align' and e>1.5 and abs(np.arctan2(np.sin(np.arctan2(rot.apply([0,0,1])[0],rot.apply([0,0,1])[2])-landing_heading),np.cos(np.arctan2(rot.apply([0,0,1])[0],rot.apply([0,0,1])[2])-landing_heading)))<.12 and abs(omega[1])<.3:
                velocity_integral*=0.;change('tactical_dive',t)
            elif phase in ('glide','tactical_dive') and p[1]-ground<(12+.25*max(0,-v[1])+max(0,-v[1])**2/(2*55) if tactical else flare_height):
                if tactical:landing_heading=float(np.arctan2(v[0],v[2]))
                flare_pitch_start=float((R.from_euler('y',landing_heading).inv()*rot).as_euler('xyz')[0]);flare_rotation=rot;velocity_integral*=0.;change('flare',t)
            elif phase=='flare' and e>.7 and np.dot(v,[np.sin(landing_heading),0,np.cos(landing_heading)])<12:
                hold_point=p.copy();level_rotation=rot;level_target=None;velocity_integral*=0.;change('level_after_flare',t)
            elif tactical and phase=='level_after_flare' and e>1 and np.linalg.norm(v[[0,2]])<12 and up[1]>.85:
                hold_point=p.copy();velocity_integral[[0,2]]=0.;contact_duration=0.;change('landing_commit',t)
            elif phase=='landing_commit' and contact_duration>=.04:
                landing_origin=p.copy();change('drive_after_landing',t)
            elif not tactical and phase=='level_after_flare' and e>3 and np.linalg.norm(v[[0,2]])<.35 and abs(v[1])<.4:
                hold_point=p.copy();velocity_integral*=0.;change('vertical_land',t)
            elif phase=='vertical_land' and p[1]-ground<1.5:change('stow',t)
            elif phase=='stow' and e>(7 if smooth else 5):change('touchdown',t)
            elif phase=='touchdown' and contact_duration>.24:
                landing_origin=p.copy();change('landed_settle',t)
            elif phase=='landed_settle' and e>2:change('drive_after_landing',t)
            elif phase=='drive_after_landing' and e>6:change('final_brake',t)
            elif phase=='final_brake' and e>2 and np.linalg.norm(v)<.5:
                if np.linalg.norm(p-landing_origin)<8:raise RuntimeError('Post-landing wheel travel insufficient')
                completed=True
            elif phase=='spiral_recover' and e>8:completed=True
            elif phase=='cruise' and e>4:change('bank_sweep',t)
            elif phase=='bank_sweep' and e>6:change('recover',t)
            elif phase=='recover' and e>4:completed=True
            e=t-since;flying=phase not in ('settle','full_wheel_run','landed_settle','drive_after_landing','final_brake')
            wheel=2. if phase in ('full_wheel_run','rolling_launch','drive_after_landing') else 0.
            commands=[(g,'ForwardKey') for g in wheels] if wheel else []
            steering_goals={}
            if steering_since is not None and not steering_signs:
                commands += [(h,steer_keys[h,0]) for h in steering]
            elif steering_signs:
                steer_goal=4*np.sin(np.pi*min(e/4,1)) if phase=='drive_after_landing' else 0.
                # Bound planned lateral acceleration while keeping wheel
                # power at its maximum; larger speed needs a wider curve.
                steer_limit=float(np.degrees(np.arctan2(6*4,max(25,body_velocity[2]**2))))
                steer_goal=float(np.clip(steer_goal,-steer_limit,steer_limit))
                for h in steering:
                    goal=0.
                    if abs(steer_goal)>.01:
                        radius=6/np.tan(np.radians(abs(steer_goal)));inner=pos[h][0]*steer_goal>0
                        goal=float(np.sign(steer_goal)*np.degrees(np.arctan2(6,radius+(-3 if inner else 3))))
                    steering_goals[h]=goal
                    err=goal-(steering_angles[h]-steering_origins[h])
                    if abs(err)>(.5 if phase=='drive_after_landing' else 1.0):commands.append((h,steer_keys[h,0 if err*steering_signs[h]>0 else 1]))
            retract=phase in ('stow','touchdown','landing_commit') or tactical and phase=='level_after_flare' and e>1
            piston_commands={g:flying and not retract for g in pistons}
            if phase=='large_turn' and 2.5<e<6.5:
                for g in pistons:
                    if pos[g][0]>0:piston_commands[g]=False
            commands += [(g,'ExtendKey') for g,on in piston_commands.items() if on]
            # Stowed blades present a broad normal to helix crossflow. Deploy
            # before entry so their plane, rather than their face, meets it.
            fold_angles={}
            fold_goal=0. if (scenario=='spiral' or mission) and flying and phase not in ('stow','touchdown') and (p[1]-ground>25 or mission and phase in ('large_turn','glide','tactical_dive','flare','level_after_flare','vertical_land')) else 78.
            if retract:fold_goal=78.
            for h in folds:
                b=min(blades,key=lambda g:np.linalg.norm(pos[g]-pos[h]));axis=rot.inv().apply(R.from_quat(d[b]['rotation']).apply([0,0,1]))*np.sign(pos[h][0])
                angle=float(np.degrees(np.arctan2(axis[1]*np.sign(pos[h][0]),axis[0])));fold_angles[h]=angle
                if abs(angle-fold_goal)>2:commands.append((h,keys[h,1 if angle<fold_goal else 0]))
            force=np.zeros(3);alpha=np.zeros(3);yaw_effort=0.;q=rot;residual=np.zeros(4);u=np.zeros(len(actuators));wdes=np.zeros(3);target=p.copy();target_v=np.zeros(3)
            if flying:
                heading=np.pi/2*float(np.clip((t-9)/6,0,1));forward=np.array([np.sin(heading),0,np.cos(heading)]);base=R.from_euler('y',heading)
                desired_v=20*forward;desired_v[1]=np.clip((ground+cruise_height-p[1])*.7,-5,8)
                # Offset the flight corridor south of the observed terrain island.
                # Keep the acrobatic reference relative to its actual entry pose.
                if tactical and phase=='climb':desired_v[2]-=12*float(np.clip((e-2)/3,0,1))*float(np.clip((cruise_height-(p[1]-ground))/40,0,1))
                acc_ff=np.zeros(3)
                if phase=='large_turn':
                    heading=turn_heading+(25/70)*e;forward=np.array([np.sin(heading),0,np.cos(heading)]);desired_v=25*forward
                    desired_v[1]=np.clip((ground+65-p[1])*.7,-5,8);acc_ff=(25**2/70)*np.array([np.cos(heading),0,-np.sin(heading)])
                    blend=min(1.,e/2);heading=np.pi/2+(heading-np.pi/2)*blend;forward=np.array([np.sin(heading),0,np.cos(heading)])
                elif phase in ('glide','dive_align','tactical_dive','flare','level_after_flare','vertical_land','stow','touchdown','landing_commit'):
                    heading=landing_heading;forward=np.array([np.sin(heading),0,np.cos(heading)])
                    if phase=='glide':desired_v=25*forward+np.array([0,-7,0])
                    elif phase=='dive_align':desired_v=35*forward+np.array([0,0,0])
                    elif phase=='tactical_dive':desired_v=25*forward+np.array([0,-36,0])
                    elif phase=='flare':desired_v=np.array([0,-3,0])
                    else:
                        desired_v=np.clip((hold_point-p)*(.35 if smooth and phase in ('vertical_land','stow','touchdown') else .8),-3,3)
                        if phase=='level_after_flare':
                            desired_v[[0,2]]=0.
                            if tactical:desired_v[[0,2]]=10*forward[[0,2]]
                            desired_v[1]=np.clip((1.2-clearance['world_y'])*.8,-2,4)
                            if np.linalg.norm(v[[0,2]])>.6:desired_v[1]=max(0.,desired_v[1])
                            if tactical and e>1 and up[1]>.85 and np.linalg.norm(v[[0,2]])<10:
                                desired_v[1]=-np.clip(wheel_clearance['world_y']*.9,.5,2.5)
                        if phase=='landing_commit':
                            desired_v=10*forward
                            desired_v[1]=-np.clip(wheel_clearance['world_y']*1.1,.6,3.5)
                            if (body_velocity[2]<4 or abs(body_velocity[0])>2.5) and wheel_clearance['world_y']<2:desired_v[1]=-.2
                        if phase=='vertical_land':desired_v[1]=max(-1.2,-.7*(p[1]-ground-.8))
                        elif phase=='stow':desired_v[1]=np.clip(ground+1.2-p[1],-.4,.4)
                        elif phase=='touchdown':desired_v[1]=-.25
                base=R.from_euler('y',heading)
                if phase=='spiral_recover':desired_v=np.array([0,np.clip((ground+55-p[1])*.7,-5,8),0.])
                if phase not in ('spiral','spiral_entry','rolling_launch','flare'):velocity_integral=np.clip(velocity_integral+.5*(desired_v-v)*dt,[-8,-24 if tactical else -8,-8],[8,8,8])
                acc=np.clip((desired_v-v)*1.3+velocity_integral+acc_ff,[-14,-18,-15],[14,22,15]);force=acc+[0,model['gravity'],0]+.3*v
                # Tilt renewable fans in upright flight; reserve rocket fuel for
                # the full-roll portions that cannot use this thrust direction.
                q=attitude(force,forward)
                if phase=='tactical_dive':q=base*R.from_euler('x',55,degrees=True)
                if phase=='level_after_flare':
                    s=min(1.,e/1.0);ease=10*s**3-15*s**4+6*s**5
                    if level_target is None:level_target=q
                    total=((level_target if smooth else q)*level_rotation.inv()).as_rotvec()
                    q=R.from_rotvec(total*ease)*level_rotation
                    level_omega=total*(30*s**2-60*s**3+30*s**4)/1.0
                    level_alpha=total*(60*s-180*s**2+120*s**3)/1.0**2
                    if e>1:q=attitude(force,forward)
                if phase=='flare':
                    duration=1.2;s=min(1.,e/duration);ease=10*s**3-15*s**4+6*s**5
                    total=(base*R.from_euler('x',-40,degrees=True)*flare_rotation.inv()).as_rotvec()
                    q=R.from_rotvec(total*ease)*flare_rotation
                    flare_omega=total*(30*s**2-60*s**3+30*s**4)/duration
                    flare_alpha=total*(60*s-180*s**2+120*s**3)/duration**2
                    actual_pitch=float((base.inv()*rot).as_euler('xyz')[0])
                    thrust_max=2*(len(fans)*model['thrust_acceleration_per_fan_power']+len(lift)*model['lift_rocket_acceleration_per_engine_power'])
                    amount=thrust_max if actual_pitch<np.radians(-35) else (model['gravity']+2*(-3-v[1]))/max(.4,up[1])
                    if tactical and v[1]>-8:
                        amount=min(amount,(model['gravity']+2*(-1-v[1]))/max(.4,up[1]))
                    force=rot.apply([0,max(0,amount),0])
                if phase=='bank_sweep':q=base*R.from_euler('x',10,degrees=True)*R.from_euler('z',30*np.sin(2*np.pi*e/6),degrees=True)
                if phase in ('spiral_entry','spiral'):
                    lp,lv,la=(join if phase=='spiral_entry' else helix).sample(e)
                    target=path_origin+path_base.apply(lp);target_v=path_base.apply(lv);target_a=path_base.apply(la)
                    correction=np.clip((.8 if tactical else .5)*(target-p)+(1.5 if tactical else 1.2)*(target_v-v),-18 if tactical else -14,18 if tactical else 14)
                    force=target_a+[0,model['gravity'],0]+.3*v+correction
                    lf=path_base.inv().apply(force)
                    q=path_base*attitude([lf[0],lf[1],0] if phase=='spiral' else lf)
                    if phase=='spiral':
                        def nominal(s):
                            _,vv,aa=helix.sample(max(0.,s));ff=aa+[0,model['gravity'],0]+.3*vv
                            return path_base*attitude([ff[0],ff[1],0])
                        h=.008;qt=nominal(e+.10);qm=nominal(e+.10-h);qp=nominal(e+.10+h)
                        wb=(qt*qm.inv()).as_rotvec()/h;wa=(qp*qt.inv()).as_rotvec()/h
                        nominal_omega=(wb+wa)/2;nominal_alpha=(wa-wb)/h
                        correction_axis=np.cross(qt.apply([0,1,0]),force/max(np.linalg.norm(force),1e-8))
                        correction_axis*=min(1.,(.25 if tactical else .10)/max(np.linalg.norm(correction_axis),1e-8))
                        q=R.from_rotvec(correction_axis)*qt
                    if np.linalg.norm(target-p)>25 or (q*rot.inv()).magnitude()>np.radians(75) or p[1]-ground<12:
                        failure_frame=dict(t=t,pos=p.tolist(),vel=v.tolist(),target=target.tolist(),desired_rotation=q.as_quat().tolist(),targets=d)
                        raise RuntimeError('Acrobatic tracking or clearance guard')
                    if phase=='spiral':roll_integral+=rot.inv().apply(omega)[2]*elapsed
                if qprevious is not None:wdes=np.clip((q*qprevious.inv()).as_rotvec()/max(elapsed,.001),-5,5)
                ades=np.clip((wdes-wprevious)/dt,-8,8);qprevious=q;wprevious=wdes
                if phase=='spiral':wdes=nominal_omega;ades=np.clip(nominal_alpha,-12,12)
                if phase=='flare':wdes=flare_omega;ades=flare_alpha
                if phase=='level_after_flare' and smooth and e<=1:wdes=level_omega;ades=np.clip(level_alpha,-8,8)
                if phase in ('vertical_land','stow','touchdown','landing_commit') or phase=='level_after_flare' and e>1:
                    wdes=np.zeros(3);ades=np.zeros(3)
                governed=smooth and phase not in ('spiral','spiral_entry','flare','level_after_flare')
                if governed:
                    q,wdes,governor_acc=governor.step(q,rot,omega,elapsed,reset=not governed_previous)
                    # Raw second differences of feedback targets amplify
                    # telemetry noise; use the bounded reference derivative.
                    ades=.4*governor_acc
                governed_previous=governed
                error=(q*rot.inv()).as_rotvec();integral=np.clip(integral+.6*error*dt,-2,2)
                kp,kd=(20,7) if phase=='spiral' else (12,7) if phase in ('flare','level_after_flare') else (12,5)
                alpha=rot.inv().apply(kp*error+kd*(wdes-omega)+integral+ades)
                conserving=phase in ('large_turn','glide','dive_align','tactical_dive','level_after_flare','vertical_land','stow','touchdown','landing_commit') or tactical and phase in ('climb','cruise','spiral')
                upper=[2. if kind=='fan' or d[g].get('fuel',[0])[0]>.001 else 1e-9 for g,kind in zip(actuators,kinds)]
                body_force=rot.inv().apply(force)
                if phase=='level_after_flare' and up[1]>.25:
                    # Limited forward thrust cannot exactly cancel horizontal
                    # lift while nose-up. Preserve vertical support first.
                    max_axial=sum(upper[len(fans)+len(lift):])*model['axial_rocket_acceleration_per_engine_power']
                    body_force[2]=np.clip(body_force[2],0,max_axial)
                    body_force[1]=max(0,(force[1]-rot.apply([0,0,1])[1]*body_force[2])/up[1])
                u,residual=allocate(body_force,alpha,[rel[g] for g in actuators],kinds,model,attitude_weight=8 if phase in ('flare','level_after_flare') else 3,rocket_penalty=1.2 if conserving else .4,upper=upper)
                commands += [(g,'FlyKey' if kind=='fan' else 'ThrustKey') for g,kind,power in zip(actuators,kinds,u) if power>.001]
                yaw_effort=rot.inv().apply(4*error+3*(wdes-omega))[1] if smooth else alpha[1]
                commands += [(yaw,'LeftKey' if yaw_effort>0 else 'RightKey')] if abs(yaw_effort)>.008 else []
            sliders=[(g,'speed',wheel) for g in wheels]+[(g,k,val) for g in susp for k,val in [('spring',2.),('damper',2.)]]
            sliders += [(g,'rotation-speed',(.12 if smooth and retract else .16) if g in folds else .12 if phase in ('drive_after_landing','final_brake') else .5) for g in hinges]+[(g,'tension',2.) for g in hinges]
            sliders += [(g,'speed',.35 if smooth and retract else .65) for g in pistons]+[(g,'push-power',1.) for g in pistons]
            sliders += [(g,'speed' if kind=='fan' else 'fthrust',float(power)) for g,kind,power in zip(actuators,kinds,u)]
            sliders += [(yaw,'speed',float(min(2,abs(yaw_effort)*(.8 if smooth else 4)))),(yaw,'damper',1.)]
            fuel=sum(s['fuel'][0] for s in d.values() if s.get('fuel_valid'))
            rows.append(dict(t=t,phase=phase,pos=p.tolist(),vel=v.tolist(),body_velocity=body_velocity.tolist(),rotation=rot.as_quat().tolist(),omega=omega.tolist(),desired_rotation=q.as_quat().tolist(),target=target.tolist(),target_velocity=target_v.tolist(),roll_degrees=float(np.degrees(roll_integral)),powers=u.tolist(),allocation_residual=residual.tolist(),fold_angles=fold_angles,steering_angles=steering_angles,steering_goals=steering_goals,piston_commands=piston_commands,connection=connection,fuel=fuel,wheel=wheel,alive=f.machine.alive_block_count,clearance=clearance,wheel_clearance=wheel_clearance,targets=d))
            if t-report>2:print(round(t,2),phase,'h',round(p[1]-ground,1),'v',np.round(v,1),'fuel',round(fuel,1),'tilt',round(np.degrees(np.arccos(up[1])),1),flush=True);report=t
            if completed:break
            publish_start=time.perf_counter()
            records=[(slider_indexes[(g,name)],value) for g,name,value in sliders]
            records += [(channel_indexes[(g,name)],1.) for g,name in commands]
            action_sequence=c.send_indexes(records)
            wait_start=time.perf_counter()
            f=next_frame(c)
            rows[-1]['timing']=dict(compute_seconds=publish_start-iteration_start,
                publish_seconds=wait_start-publish_start,wait_seconds=time.perf_counter()-wait_start,
                action_sequence=action_sequence,next_sample_sequence=f.sequence,
                next_applied_sequence=f.sequence_applied)
    except Exception as exc:failure=str(exc);raise
    finally:
        Path(os.environ[ENV_RUN_DIR],'hybrid_envelope.json').write_text(json.dumps(dict(completed=completed,failure=failure,failure_frame=failure_frame,root=root,actuators=actuators,kinds=kinds,events=events,model=model,frames=rows)));c.close()



if __name__ == "__main__":
    main("tactical")
