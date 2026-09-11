# Shuttle and booster recovery: staged guidance and independent return control

## One-command run

After [one-command setup](../../../README.md#one-command-setup), run this from the repository root in PowerShell:

```powershell
uv run python control/examples/shuttle_booster_recovery/run.py
```

This command rebuilds the machine from `machine.json`, launches Besiege, and runs the complete automatic controller. Generated machines, logs, and telemetry are saved under `datacache/manual_cases/`.

Append `--edit-before-start` to place cameras before the mission, or `--prepare-only` to rebuild without starting the simulation. See the [root README](../../../README.md#run-three-automatic-machine-examples) for camera and recording instructions.

## Overview

This example keeps a shuttle and two powered boosters attached through ascent, a full orbital lap, and a return-orbit maneuver. It then releases the boosters, returns them toward separate launch-site pads, deploys the shuttle's wings, and commands an aerodynamic glide. It combines orbital mechanics, geometric attitude feedback, actuator models, state observation, and coordinated landing schedules.

This tutorial explains the control algorithms and their phase transitions. The `mission_complete=False` field written by `controller.py` is a constant diagnostic field, not a computed verdict on the flight outcome. It should not be interpreted as a failed recovery or used to characterize the project's acceptance workflow.

## Runtime and source map

The shared runner rebuilds `machine.json`, rebinds hardware roles and servo calibration to the rebuilt GUIDs, and runs full telemetry at **25 Hz** on **LONE ORB**. `mission.py` enables orbital staging, return guidance, booster RTLS (return to launch site), and glider control. It sets ascent altitude to 1400, return apoapsis altitude to 3000, booster cruise/fine-control height to 600, lateral speed cap to 35, and controller duration to 800 s.

| Source | Main responsibility |
| --- | --- |
| `controller.py` | Attached-stack ascent, insertion latch, hardware commands and logging. |
| `return_staging.py` | Active return state machine, orbit reshaping, entry alignment and release. Inherits hardware grouping from `orbital_staging.py`. |
| `field_guidance.py` | Burn-window forecast and spherical landing-field guidance. |
| `axial_burn_trim.py`, `native_thruster_force.py` | Differential engine allocation and velocity-dependent thrust compensation. |
| `momentum_steering.py`, `paired_spin.py` | Attached-stack fine wheel control and opposed-spin reduction. |
| `pose_release_observer.py` | Experimental multi-window geometric release observation. |
| `booster_return.py` | Independent booster guidance, coupled descent schedule and contact cutoff. |
| `wing_deployment.py`, `glider_return.py`, `blade_servo_control.py` | Measured deployment, aerodynamic guidance and native servo-target tracking. |

## 1. Mission phases and parallel return activities

The final preset selects `ReturnStaging`, rather than the simpler base `OrbitalStaging.update` sequence. Altitudes below are measured above the spherical surface of radius 600; radii explicitly include that 600.

| Phase | Objective and actual transition rule |
| --- | --- |
| `ascent` | Attached-stack velocity guidance builds the orbit. The `coast` latch requires periapsis altitude above 1300, current altitude above 1310, apoapsis altitude below 2600, and absolute radial speed below 15 for the final preset. |
| `orbit_coast` | Engines are off and wheels damp rotation. Accumulate radial angular travel. After at least 360 degrees and radius above 1400, wait for a predicted landing-field phase error below 0.018 rad. |
| `retrograde_align` | Align the stack with the required velocity change. With the configured outbound return ellipse, this direction need not be retrograde. Enter the burn below attitude error 0.14 rad and angular speed 0.12 rad/s. |
| `deorbit_burn` | Reshape the orbit toward periapsis altitude 300 and apoapsis altitude 3000. Exit when periapsis is between 280 and 310, apoapsis is within 80 of 3000, and outward radial speed exceeds 3. |
| `entry_align` | Aim at the future atmospheric-entry attitude and reduce residual rotation while booster wheels remain attached. Release after the orbital, attitude, and spin predicates remain satisfied for more than 0.5 s. |
| `return` | Pulse four couplers for 0.2 s, then run independent booster recovery, wing deployment, and aerodynamic return activities. |

After separation, booster attitude damping operates during the initial clearance interval. Booster RTLS begins after 5 s. Wing deployment becomes eligible after 5 s **and** minimum booster-to-orbiter group-center distance exceeds 15. Glider control waits for measured deployment and valid servo calibration; its atmospheric guidance becomes active below altitude 500 at speed above 5. The orbiter receives no engine or reaction-wheel commands after separation.

The final preset enables counterspin, quiet-spin transition, paired spindown, observed wheel feedback, and the **experimental pose-release trial**. Standalone wheel-response, momentum-hold, dry-release, transverse-probe, and entry-aligned-burn experiments are disabled. Some utilities from `entry_aligned_burn.py` are still reused by the main-axis burn path; importing a utility does not activate its experimental allocation mode.

## 2. State, reference frames, and orbital model

The attached-stack position and velocity are arithmetic means over blocks. These reduce root lever-arm motion but are **not mass-weighted center-of-mass estimates**. Return control uses separate group means for diagnostics and booster root states for each booster loop. Orientation is the live core quaternion multiplied by the inverse saved core orientation, recovering the authored machine frame. In this frame, `+Y` is nose/main thrust and `+Z` is the dorsal reference used to construct attitude.

The planet model uses center `(0,-600,0)`, surface radius 600, and gravitational parameter `mu=8,456,800`. Guidance gravity magnitude is `min(22, mu/r^2)`. Define outward unit vector **n**, radial speed `v_r=v dot n`, and tangential velocity **v_t** = **v** - `v_r`**n**. The radial dynamics motivate

$$
\ddot r=a_r-g+\frac{\|\mathbf v_t\|^2}{r}.
$$

Consequently, radial thrust demand includes `g - |v_t|^2/r`: a vehicle near circular speed needs much less outward support than a hovering vehicle.

Orbital estimates use specific energy `epsilon=|v|^2/2-mu/r` and angular momentum `ell=|r cross v|`:

$$
e=\sqrt{\max(0,1+2\epsilon\ell^2/\mu^2)},\qquad
r_p=\frac{\ell^2}{\mu(1+e)},\qquad
r_a=\frac{\ell^2}{\mu(1-e)}.
$$

These are two-body approximations. They are most useful for the vacuum coast; they omit atmospheric forces, thrust, structural motion, and the near-surface gravity clamp.

## 3. Attached-stack ascent and engine mixing

The outer loop schedules radial and eastward velocity:

$$
v_r^*=\operatorname{clip}(0.18(h_t-h),-12,30),\qquad
v_E^*=\sqrt{\frac{\mu}{600+h_t}}\operatorname{clip}\left(\frac{h-400}{600},0,1\right).
$$

The gradual east-speed ramp spreads the pitch-over across 600 altitude units. Radial error feeds a bounded integral plus proportional feedback. East-speed error uses gain 4 and a low-altitude empirical drag term. Crossrange velocity is damped with gain 2 and acceleration norm capped at 20. In compact form:

$$
\mathbf a^*=\mathbf n\left[g-\frac{\|\mathbf v_t\|^2}{r}
 +\operatorname{clip}(2.5(v_r^*-v_r)+I_r,-25,25)\right]
 +\mathbf e_E a_E+\mathbf a_{cross}.
$$

The desired thrust direction is the unit acceleration demand. A pointing cross product and angular-rate damping command wheels. Differential thrust supplies additional attitude authority, including a bounded integral trim to counter the folded vehicle's asymmetry.

For main engines centered at body positions `(x_i,y_i,z_i)`, the mixer is

$$
A=\begin{bmatrix}1&\cdots&1\\-z_1&\cdots&-z_N\\x_1&\cdots&x_N\end{bmatrix},\qquad
\Delta\mathbf u=A^+\begin{bmatrix}0\\m_x\\m_z\end{bmatrix}.
$$

`A+` is the Moore-Penrose pseudoinverse. A `+Y` force gives moments `(-z_i,0,x_i)` by `r cross F`, explaining the matrix. The zero first component requests no change in total setting. These moments are command-times-distance quantities, not calibrated torques in newton-metres. Ascent clips each differential correction inside available collective authority and `[0,2]`; clipping can alter the ideal pseudoinverse result. Collective power divides acceleration projected onto actual body-up by `3.5*N`.

## 4. Planning and executing the return orbit

`outbound_velocity` uses energy and angular-momentum conservation to calculate the outward velocity on an ellipse with selected periapsis radius `r_p` and apoapsis radius `r_a`:

$$
\ell=\sqrt{\frac{2\mu r_pr_a}{r_p+r_a}},\quad v_t^*=\ell/r,\quad
v_r^*=\sqrt{\max(0,2\mu(1/r-1/(r_p+r_a))-(v_t^*)^2)}.
$$

The final target uses radii 900 and 3600. The positive square root deliberately creates an **outbound** coast, giving boosters time to return while the dry shuttle follows its longer arc. The stack points along `v_target-v`, and burns only when actual thrust-axis alignment exceeds 0.96. Nominal equivalent collective power is proportional to velocity deficit and capped at 0.4.

`burn_window` forecasts 22 s ahead using eight RK4 steps of two-body motion. It computes the descending crossing at radius 1100, then rotates that entry direction by the empirical glide arc **4.175 rad** to predict landing direction. The signed angular error to the launch direction schedules the maneuver. This is a short physics forecast combined with a measured glide-distance heuristic, not an optimization over a simulated atmospheric trajectory.

During the burn, `axial_burn_trim.distribute` scales a zero-sum pseudoinverse correction uniformly until all equivalent engine settings fit `[0,0.6]`, retaining only the ascent pitch trim as feedforward. Unlike individual clipping, uniform scaling preserves the requested zero-sum property. Actual engine force still changes with axial velocity, so `native_thruster_force` converts equivalent force to native slider settings.

For positive setting `u` and axial velocity `v`, its estimator is

$$
T_0=\begin{cases}600-\min((0.5v/u)^2,450),&v>0\\600,&v\le0,\end{cases}
\quad F_0=0.35uT_0,\quad
F=F_0-0.45\min(\max(v,0),F_0).
$$

Zero setting gives zero force. A 36-iteration bisection inverts this relation, capped at native setting 2. The estimator omits ground/jet-contact bonuses; saturation and predicted force are logged. Conserving the sum of settings in the geometric mixer does not by itself conserve actual velocity-dependent force.

## 5. Quiet entry alignment and release observation

`predicted_entry_attitude` solves the Keplerian descending crossing at radius 1090. It points the nose slightly above predicted entry velocity and uses the entry radial direction to orient the dorsal axis. The reference is smoothed in rotation-vector space and uses zero desired angular velocity during entry alignment. This avoids carrying a rotating orbital-frame attitude into a long unpowered coast.

The active counterspin controller first runs each orthogonal wheel pair in opposing directions, using base speed 0.28 and bounded differential correction. After attitude error remains below 1 degree and root angular speed below 0.03 for 0.5 s, it switches to quiet observed feedback. For each wheel, relative rotor rate provides a baseline native speed command:

$$
u_{base}=-\frac{\boldsymbol\omega_{rotor,relative}\cdot\mathbf a_i}{1200\pi/180},\qquad
u_i=\operatorname{clip}(u_{base}+(K_p\mathbf e_R-K_d\boldsymbol\omega_{err}+\mathbf b)\cdot\mathbf a_i).
$$

The quiet switch explicitly changes gains to `(Kp,Kd,Ki)=(0.2,1,0)`, baseline cap to 0.225, and output cap to 0.235, overriding the initial preset values. Rotor and control-rate filters reduce rapid command variation. Wheel magnitude goes to the `speed` slider and sign to a key; fractional key values are not analog motor commands.

`reduce_opposed_spin` writes each axis pair as a mean plus an opposed component, then reduces the opposed component while preserving the signed sum. It is an equal-inertia **command-space construction**. Neither its net-spin proxy nor the historical name `MomentumSteering` implies a fully identified physical angular-momentum model.

For the configured outbound return, release requires radius between 1160 and 3700, periapsis altitude between 0 and 330, negative orbital energy, and attitude error below 0.14 rad. The pose trial replaces the nominal raw-rate gate of 0.003 rad/s with multi-window evidence:

1. Center the same spine block origins at two times and fit the best rigid rotation using `Rotation.align_vectors`.
2. Repeat for intervals of 0.2, 1, and 2 s, comparing geometric rotation with the root quaternion change.
3. Require geometric and root-pose rates below 0.003 rad/s, fit residual below 0.001, root/geometry rotation disagreement below 0.001 rad, and raw root rate below 0.03 rad/s.

These conditions must accompany the attitude/orbit conditions for more than 0.5 s. Multiple windows help reject deformation and sustained drift. `rigid_rate_observer.py` retains a different experimental fit based on `v_i-v_mean = omega cross r_i`; mixing Transform origins with Rigidbody COM velocities can bias that fit. It is not the default release gate. Even the geometry-only gate needs post-release evidence to establish that separation actually remained quiet.

## 6. Booster recovery: independent feedback with a shared schedule

`BoosterReturn` assigns pads about 20 surface units to either side of the launch direction, preserving observed booster ordering. Distance is the spherical arc `600*acos(n dot n_pad)`. A shared height schedule based on the farther booster coordinates descent; each booster retains its own velocity feedback, integral, thrust estimate, wheel commands, and cutoff state.

The return progresses through descent arrest (`capturing`), pad approach, a synchronized terminal region, and final descent. Capture completes only when both boosters are sufficiently high, nearly upright, and no longer rapidly sinking. The terminal region requires both within 12 of their pads, both below height 60 relative to launch contact, and maximum tangential speed below 3. Additional alignment and low-speed conditions must persist for 0.5 s before final descent begins.

The approach-speed limit reserves distance for slow attitude response before braking. Solving

$$
d_{flight}=v\tau+\frac{v^2}{2a}
$$

gives `v_limit=sqrt((a*tau)^2+2*a*d_flight)-a*tau`, with `d_flight=d_surface*r/600`. The active call uses braking acceleration 1.5 and delay 6 s. Vertical-speed references likewise use square-root stopping envelopes to avoid abrupt descent commands near the terminal region and atmosphere.

Radial PI feedback adds gravity and centrifugal compensation; tangential velocity feedback steers toward the pad. Thrust is the desired acceleration projected onto actual thrust direction, divided by estimated acceleration per engine setting, and gated off when directional alignment falls below 0.7. A filtered online estimate uses gravity-corrected velocity increments during suitable powered samples to adapt thrust effectiveness as conditions change. Near the pads, observed rotor baselines and bounded integral trim replace coarse wheel pulses.

Cutoff requires at least two landing-pad blocks near the surface with low radial speed, low vehicle radial/tangential speed, upright attitude, and limited angular rate for more than 0.25 s. Distance below 8 from the assigned target is logged separately as `at_target`. This separates the local engine-cutoff condition from landing-position accuracy; read both fields when studying the return controller.

## 7. Aerodynamic shuttle return and servo control

Wing deployment commands the configured native 90-degree stops and measures actual spar rotation. Both sides must be within 2 degrees of 90 for more than 1 s before deployment is reported. Elevon angles are then measured relative to the moving wing bases, preventing wing deployment from being mistaken for control-surface deflection. The runner rebinds the saved servo-sign calibration; if it is unavailable, the glider can attempt a short sign-identification pulse while sufficiently high.

Below altitude 500, the glider forms a desired sink speed and lift demand:

$$
v_r^*=-\operatorname{clip}(0.08\max(0,h-1),0.6,3),\qquad
L^*=\max(0,g-v_t^2/r+0.4(v_r^*-v_r)).
$$

Its empirical blade model uses normalized density

$$
\rho=\operatorname{clip}\left(\frac{e^{-h/500}-e^{-1}}{1-e^{-1}},0,1\right),\qquad
L_{scale}=0.0011\,A_{factor}\rho^2 V\min(V^2,900).
$$

This approximates the game's native blade response, including a speed cap of 30 inside the squared-speed factor; it is not the usual aircraft `0.5*rho*V^2*S*CL` model. Inverting `L_scale*sin(alpha)` estimates angle of attack, bounded to 8–40 degrees after an altitude blend from the initial 10-degree reference. Pitch combines this angle with flight-path angle and is itself bounded.

`field_bank` supplies spherical route guidance with a fixed great-circle normal to avoid heading reversal near the antipode. For heading error `eta` and lookahead `L=max(100,8*v_t)`, it requests lateral acceleration `2*v_t^2*sin(eta)/L`, then bank `atan2(a_lateral,22)`, limited to 25 degrees, faded near the ground, and rate-limited to 0.12 rad/s. This resembles lookahead path-following guidance; the long-arc route and empirical entry-to-rest distance remain important assumptions.

Rotation-vector attitude errors drive pitch PI plus rate damping, and roll/yaw PD laws. Pitch integration is conditional to reduce windup under saturation. The final machine's left/right elevons receive mixed pitch/roll goals; a yaw output is applied only if rudder hardware exists.

`BladeServoControl` accounts for the native servo's **integrated target angle**. It propagates an internal target using the previous commanded rate, applies a slow bounded correction from measured angle, then drives target error with a rate cap of 40 degrees/s and a 0.15-degree deadband. Slider speed divides rate by the native 80-degrees/s scale. This avoids repeatedly driving the internal target past the desired angle while the physical joint is still catching up.

## Evidence and model boundaries

`ascent.json` contains full frames, phase/deployment/release/contact events, per-group states, and detailed return diagnostics. The controller also saves source and hardware snapshots in the run directory. Read the per-group records after separation: whole-machine means no longer describe any single vehicle's trajectory.

Useful checks are burn velocity error versus actual compensated engine power; pose-window residuals before release versus observed motion afterward; booster pad distance versus contact cutoff; and glider lift demand versus estimated lift scale and servo saturation. These connect the mathematics to actuator limits and actual behavior.

The gravity/orbit, thrust, lift, and rotor models are approximations used to generate control commands. Their assumptions explain the feedback corrections, saturation handling, and release observations in the implementation; they are not a verdict on the outcome of a recorded mission.
