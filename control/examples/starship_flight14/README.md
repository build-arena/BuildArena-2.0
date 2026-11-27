# Starship Flight 14 replica: two-stage launch, booster return and ship recovery

## One-command run

First complete the [game and MOD prerequisites](../../../README.md#before-setup-complete-the-prerequisites),
then pass [one-command setup](../../../README.md#one-command-setup)
(`.local/setup-report.json`: `status=passed`). Only then run this from the repository
root in PowerShell, with no other example running:

```powershell
uv run python control/examples/starship_flight14/run.py
```

This command rebuilds the machine from `machine.json`, launches Besiege, and runs the complete automatic controller. Generated machines, logs, and telemetry are saved under `datacache/manual_cases/`.

Append `--edit-before-start` to place cameras before the mission, or `--prepare-only` to rebuild without starting the simulation. See the [root README](../../../README.md#run-four-automatic-machine-examples) for camera and recording instructions.

## Overview

Starship Flight 14 flew on 2026-09-28 from Starbase Pad 2 with Super Heavy **B21** and Ship **S41**, both
Block 3 / V3. It was the first time a Starship reached Earth orbit, deployed operational payload (26 Starlink
V3, one at a time through the "Pez" dispenser), and made a deorbit burn. Both stages lost an engine on ascent;
out of caution the ship left orbit about two hours after insertion instead of flying six orbits, and after a
belly-first entry flipped tail-down and splashed down north of Hawaii about 3 h 8 min after launch. The
booster boosted back, relit its landing engines, touched down softly in the Gulf and was then destroyed by its
flight termination system as planned.

This example flies the same sequence on **LONE ORB** with a 176-block stack, and both stages are flown by one
controller at once after hot staging. Time is compressed: MECO comes at T+17 s instead of T+2:20, and one orbit
takes about 100 s instead of 90 min. The whole mission takes about 220 s of simulation. Each run writes
`flight_events.json` (the mission timeline, each entry tagged with the real flight's T+ where there is one) and
`steps.jsonl` (per control step: phase, engine power and a state dictionary for each vehicle).

Distances are in game units (one unit is about 3 m at the 1:3 scale of the stack), and the tuned numbers
belong to this simulation, not to the real vehicle.

## Runtime and source map

`run.py` delegates to `control/run_example.py`, which rebuilds `machine.json` (228 successful MCP operations),
applies `mission.py` (`STARSHIP14_PROFILE=full`), runs full telemetry at **25 Hz** on LONE ORB, and allows the
controller up to 2400 s. The controller maps the parts named in `parts.py` (by build ID) to the rebuilt GUIDs
through the run's `rebuild_manifest.json`, and reads each block's spawn rotation from the prepared BSG.

| Source | Main responsibility |
| --- | --- |
| `controller.py` | Runtime loop: bind hardware, read telemetry, call `Flight.step`, send sliders and keys, log. |
| `flight.py` | Stack ascent (TWR schedule, max-Q bucket, pitch program, attitude), hot staging, then both stages in parallel. |
| `booster.py` | B21: flip, boostback, coast, landing burn, touchdown, flight termination event. |
| `ship.py` | S41: ascent, SECO, insertion, deploy, early return, deorbit targeting, belly-first entry, landing flip, landing burn, splashdown. |
| `guidance.py` | Drag Block model, entry and landing-point predictor, landing speed profile, ground-track angle. |
| `actuators.py` | Engine allocation, Steering Thruster allocation, flywheels, separation bolts, PD torque law. |
| `attitude.py` | Attitude error (pointing plus roll) and the PD law used in coast phases. |
| `vehicle.py` | Hardware binding, live mass and center of mass, rigid-body state of each stage. |
| `parts.py` | Every part by build ID, with each block's center and dry mass in the machine frame. |
| `physics.py` | LONE ORB gravity and atmosphere, engine force model, two-body elements, point-mass propagation. |
| `config.py` | Every tuned number. |

## 1. Mission timeline

Events from the validation run of this controller (`flight_events.json`):

| Replica T+ (s) | Event | Real flight |
| ---: | --- | --- |
| 1.0 | Liftoff, all nine B21 engine blocks (33 Raptors) | T+00:00:00 |
| 6.6 | Max Q throttle bucket | T+00:00:58 |
| 13.0 | B21 center Raptor out: the center block drops to 2/3 thrust | ascent anomaly |
| 16.7 | MECO at h = 321 | T+00:02:20 |
| 17.0 | Hot staging: S41 lights while still bolted to the vented ring | T+00:02:22 |
| 17.6 / 18.4 | Separation bolts fire; separation confirmed by distance | |
| 18.8 | B21 flips | T+00:02:25 |
| 19.3 | One S41 Raptor Vacuum shuts down; the other five burn longer | ascent anomaly |
| 22.8 | Boostback, 31 of 33 Raptors (one block off) | T+00:02:27 |
| 24.1 | Boostback shutdown on the predicted impact point | T+00:03:07 |
| 25.3 | SECO into a suborbital trajectory (352 × 787) | T+00:08:11 |
| 42.2 | B21 landing burn, "13 commanded, 11 light" | T+00:06:36 |
| 51.6 / 62.0 | Orbit insertion with one sea-level Raptor; S41 is orbital (600 × 792) | T+00:25:17 / 00:25:36 |
| 57.5 | B21 steps down 13 → 5 → 3 | |
| 59.2 | B21 touchdown at 2.3 u/s, 13.6 from its offshore landing zone | T+00:07:01 |
| 61.2 | B21 flight termination (event only, see limitations) | after T+00:07:01 |
| 87.1 – 120.6 | 26 Starlink V3 released one by one, 1.25 s apart | T+00:34:07 – 01:04:39 |
| 123.6 | Health check after the RVac shutdown: early return to the northern-Pacific zone | "abundance of caution" |
| 176.0 | Deorbit burn, one sea-level Raptor, about 1.1 orbits after insertion | T+02:12:00 |
| 187.8 | Entry interface, belly first at 83 u/s | T+02:28:52 |
| 196.0 | Peak deceleration 31 u/s² at h = 305 | |
| 198.5 | Landing flip | T+02:50:11 |
| 201.1 | Landing burn on three sea-level Raptors | T+02:50:13 |
| 210.9 / 211.5 | Steps down 3 → 2 → 1 and cuts off at contact (3.7 u/s) | T+03:08:20 – 03:08:26 |
| 211.5 | S41 "splashdown", 76 from the target zone | T+03:08:30 |
| 214.6 | S41 tips over | after splashdown |

The target zone is a fixed point on the ground track 300° past the pad, reached on the second revolution; it
plays the contingency zone north of Hawaii, roughly 60° of longitude short of Starbase. All 176 blocks survive
the whole flight.

## 2. The vehicle

The stack is built with the operations the BuildArena MCP server exposes (`start`, `attach_block_to`,
`connect_blocks`, `twist_block`, `shift_block`); `machine.json` holds the 228 successful operations. Scale is
about 1:3: the 3-unit Fuel Barrel Big stands in for the 9 m barrel, and the stack is about 43 units tall. The
build frame is [East, North, Up]; the ship's belly faces South and the Pez door faces North.

**Super Heavy B21.** Six big tanks over a fuel-linked 3×3 "thrust puck" of small barrels carrying nine Booster
blocks. The blocks stand for the 33 Raptors by thrust share: the center block is the three center engines, the
four edge blocks the inner ten, the four corners the outer twenty; engine counts in the story become thrust
caps on blocks. Three grid fins, two flywheels, four Steering Thrusters near the top (the cold-gas thrusters)
and the vented hot-stage ring, whose segments ride on three Decoupler separation bolts.

**Starship S41.** Three big tanks over a 3×3 aft manifold. The three sea-level Raptors sit on a triangle around
the axis (so three engines alone can steer in pitch and yaw), the Raptor Vacuums on the diagonal, and the
three hot-stage clamp struts on the remaining triangle. The payload bay holds 26 Decouplers in 13 columns:
each one is a Starlink V3, ejected North by its own bolt. The belly carries Armor Plate heat-shield tiles, six
**Drag Blocks** (belly "bowls", see section 6) and four flaps (armor plates on hinged roots); three Steering
Thrusters at the nose and two flywheels complete the attitude hardware.

Joints between very light and very heavy blocks are soft in Besiege's physics, so the interstage load path uses
braces and ballast raised to mass 1.0, and staging uses Decoupler bolts (explosive, rigid until fired) rather
than grabbers.

## 3. State, environment and engine allocation

**Environment model** (`physics.py`). Planet center (0, −600, 0), radius 600, g = 22 below r = 620 and
GM/r² above; air density ρ = (e^(−h/500) − e^(−1)) / (1 − e^(−1)) up to h = 500, zero above; every block
feels a linear drag of about 0.123·ρ·v. The controller models the Booster block's force as
F = p·T0·0.35 − 0.45·min(v, F) with T0 = 600 − min((v/2p)², 450) for motion along the thrust direction, and
the full 210·p when braking; the `fthrust` slider tops out at 2. An in-game calibration flight matched these
models (thrust, fuel use and the linear-drag fit) to 0.6 u/s² RMS in acceleration.

**State.** Each body's pose comes from one reference block; its center of mass, velocity and angular velocity
are mass-weighted over all its live blocks (a single block vibrates enough to put ±40 of noise on periapsis).
Masses follow the live fuel readings, and released satellites are dropped from the ship.

**Engine allocation.** Forces are a bounded least-squares fit of [total thrust, pitch torque, yaw torque]
about the live center of mass, torque rows weighted so attitude wins at saturation, then turned into
`fthrust` with the engine model. This also absorbs asymmetric engine sets: with one Raptor Vacuum out the
remaining five still hold any requested torque. Whatever torque the engines cannot give goes to the Steering
Thrusters (their own least-squares fit) and then to the flywheels.

## 4. Booster return

The flip uses pure differential thrust (zero collective, torque priority) and takes about 4 s. The boostback
steers the predicted impact point (drag-aware propagation) onto the landing zone in both axes and pitches up
as the miss closes, so the booster ends near the landing attitude; a short differential engine puff helps the
cold-gas thrusters if the coast attitude error exceeds 45°. The landing burn tracks
v_ref(h) = √(v_td² + 2·a·(h − h_gate)) with feed-forward of its deceleration and gravity at the current
height, steps from five blocks to three near the ground, and ends on a sink-rate collapse (terrain height at
the zone is unknown).

## 5. Ship ascent, orbit, deploy and early return

Radial-rate guidance: a climb rate that fades toward the target height sets the radial acceleration, the rest
of the thrust builds horizontal speed; SECO comes when periapsis reaches 350, so the ship coasts on a
suborbital path to apoapsis, where one sea-level Raptor's thrust (shared by the three SL blocks so they can
still steer) raises periapsis to 600. The Pez dispenser then releases the 26 satellites with the door turned
away from the planet. Coast attitude uses the nose thrusters with a dead band, because every firing also
nudges the orbit.

After a health check the ship waits for the deorbit window: while holding retrograde it repeatedly predicts
where a deorbit burn made now would land (the entry predictor of section 6) and burns when that point reaches
the target zone, cutting off once the prediction with the actual velocity lands on it.

## 6. Belly-first entry, landing flip and burn

The controller models each Drag Block as pushing back against motion along its own local +Y with a force of
ρ²·v_y·(0.6 + min(v_y²/2, 100)) per unit of its `magnitude` slider, with v_y a quarter of the flow speed, and
rescales that model during entry from the measured deceleration. Six of them sit on the side walls with +Y
turned toward the belly, in three rows (aft, near the center of mass, forward), so falling belly-first they
brake hard and edge-on they do nothing. The entry controller:

- holds a belly-first attitude whose nose trails the flight path (the branch nearest the retrograde deorbit
  attitude), ending belly-down with the nose level as the path steepens;
- splits the bowl magnitudes with a bounded least-squares fit: pitch and roll torque first (fore/aft and
  east/west differential, like the real flaps), total drag second;
- uses the total as range control: a predictor flies the belly-first descent to the ground and a bisection
  picks the magnitude that puts touchdown on the zone;
- below h = 350 (or 65 u/s) opens the bowls to the largest magnitude that still trims, for the lowest terminal
  speed, and predicts the last moment of the belly flop that leaves height for the flip plus the landing burn.

For the landing flip the forward bowls open wide and the aft ones close, so the air swings the tail down while
the sea-level engines light; the PD law takes over for the last 40°. The landing burn follows a profile that
uses 65 % of the deceleration the three engines can give at each height; gravity is 13–18 at h = 100–300 but
22 near the ground, where the three engines only manage 1.3 g, so a fixed-deceleration profile either wastes
height or cannot stop. The burn steps 3 → 2 → 1 in the last moments and cuts off when the sink rate collapses
on contact.

## 7. What the blocks showed in flight

- **Drag Block**: it only brakes motion along its own local +Y (Unity `transform.up`), not "into the bowl"
  along the mounting normal. Mounted on a South face its +Y points along the vehicle axis, so it brakes a
  tail-first fall and ignores a belly-first one; mounted on a side wall and twisted until +Y points at the
  belly, it does what a belly flap should.
- **Steering Thruster**: measured force is about 3.5 per unit `fthrust` (slider 0–3), from B21's roll response.
  Its push direction (local +Y) depends on the base block, the face and the twist, so each thruster in the
  history is twisted until its exported rotation pushes the intended way.
- **Reaction Steering Block**: momentum-limited (its rotor is capped at 5 rad/s), fine for trimming, useless
  for turning a 3 000-inertia booster.
- **Wing Panel**: its native lift and drag are large and not modelled here; with Wing Panel flaps the ship
  weathervaned nose-first or tail-first depending on which pair faced the flow, so the flaps are armor plates.

## Evidence and limitations

`flight_events.json` and `steps.jsonl` record every event and control step. Tested on macOS 15.5 with
Besiege 1.91-25490. Landing and zone distances vary from run to run by some tens of units (the physics is not
perfectly repeatable): in the development runs S41 came down 50–170 from the target zone and B21 within about 25
of its own.

- LONE ORB has no ocean. Both "splashdowns" are touchdowns on the ground at the zone's (unknown) height; the
  booster's landing zone sits 150 units downrange of the pad to play the Gulf.
- The ship's tip-over after splashdown is scripted: a two-engine kick on one side levers it over its base
  edge. The post-splashdown explosion and B21's flight termination are events only; no explosives are
  carried.
- Engine counts are thrust fractions of block groups (33 Raptors on nine blocks, "13 commanded, 11 light" as a
  cap on one inner block, one Raptor on three blocks at a third each).
- Time is compressed and the orbit is scaled (≈ 600 × 760 above a 600-radius planet, vs 262 × 277 km).
- No heating or plasma; the heat-shield tiles are cosmetic.
- S41's three sea-level engines give only about 1.3 g near the ground, so its last metres are flown with little
  margin and touch down at 3–6 u/s.
- Besiege's default `Config.xml` enables automatic time scaling, which in this long, 176-block flight dropped
  the real-time factor to 0.15–0.4. With the game closed, set `AutoTimeScale`, `ShadowsEnabled`,
  `ScreenSpaceAmbientOcclusion` and `Bloom` to `false` in the game's `Config.xml` (the game rewrites the file
  on exit, so edit it only while it is closed) to run at about real time.
