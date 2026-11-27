"""Tunable flight parameters (game units, seconds). Values were fitted in LONE ORB test flights."""
CONFIG = dict(
    max_duration=900.0,
    t_ignition=1.0,
    # --- stack ascent ---
    twr_liftoff=1.22, twr_maxq=1.15, twr_climb=1.30, maxq_h=(60.0, 140.0),
    t_booster_engine_out=12.0,
    pitch_start_h=20.0, pitch_max_deg=50.0, pitch_exp=0.75, meco_h=300.0, stack_wn=0.5,
    # --- hot staging ---
    t_hot_ignite=0.3, t_hot_release=0.8, bolt_power=1.0, sep_confirm_gap=4.0,
    # --- booster return ---
    booster_lz_downrange=150.0, flip_delay_s=1.0, booster_wn=0.9,
    flip_collective=0.0, flip_pmax=2.0, boostback_collective=1.4,
    boostback_elev_deg=20.0, boostback_align_deg=25.0, boostback_max_s=6.0, boostback_lead=5.0,
    boostback_blend_miss=60.0, boostback_blend_max=0.8, coast_assist_deg=45.0, coast_assist_pmax=0.5,
    landing_decel=5.0, touchdown_speed=2.5, landing_target_h=10.0, landing_ignite_frac=0.97, landing_vertical_h=12.0,
    booster_lz_gain=0.1, booster_vh_max=10.0, booster_vh_gain=0.8,
    booster_ah_max=8.0, booster_vz_gain=1.2, landing_step1_h=4.0,
    fts_delay_s=2.0,
    # --- ship: ascent and orbit ---
    t_rvac_out=1.5, ship_wn=0.8, ship_slew_deg_s=15.0, target_apo=800.0, seco_peri=350.0,
    ship_vr_gain=0.08, ship_vr_max=15.0, ship_vr_kp=1.0,
    ship_min_throttle=0.15, ship_ascent_max_s=40.0, insertion_vr=0.5, orbit_min_peri=600.0, insertion_max_s=45.0,
    rcs_max_fthrust=3.0, coast_wn=0.6, coast_err_max=0.5, coast_quiet_deg=3.0,
    deploy_settle_s=4.0, deploy_interval_s=1.25, sat_epower=0.1,
    # --- ship: early return ---
    health_check_s=3.0, landing_zone_deg=660.0, deorbit_dv_plan=12.0, deorbit_align_deg=25.0, deorbit_power=0.5, deorbit_max_s=10.0,
    entry_aoa_deg=90.0, entry_mag_nominal=1.0, entry_mag_max=3.0, entry_wn=1.0, entry_err_max=0.5,
    entry_ki=0.8, entry_i_max=2.0,
    ship_landing_margin=0.65, ship_touchdown_speed=2.5, ship_landing_target_h=10.0, flip_lead_s=2.6, flip_dv=8.0, terminal_h=350.0, terminal_v=65.0, flip_settled_decel=2.0, flip_floor_h=80.0,
    ship_flip_collective=0.2, flip_wn=1.5, flip_align_deg=30.0, flip_max_s=6.0, flip_open_loop_deg=40.0,
    ship_vz_gain=1.2, ship_lz_gain=0.1, ship_vh_max=8.0, ship_vh_gain=0.8, ship_ah_max=6.0, ship_vertical_h=12.0,
    ship_step2_h=1.5, tip_kick_start=1.5, tip_kick_s=2.0, tip_kick_power=2.0,
    tip_kick_cells=((-1, 0), (-1, -1)), end_s=9.0,
)
