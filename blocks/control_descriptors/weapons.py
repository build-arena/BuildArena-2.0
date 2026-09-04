"""User-facing control meanings for weapons and active propulsion."""

CONTROL_SEMANTICS = {
    11: {"descriptions": {
        "ShootKey": "Fires the cannon."
    }},
    21: {"descriptions": {
        "IgniteKey": "Ignites the flamethrower."
    }},
    53: {"descriptions": {
        "ShootKey": "Fires the shrapnel cannon."
    }},
    54: {"descriptions": {
        "DetonateKey": "Detonates the grenade."
    }},
    56: {"descriptions": {
        "ShootKey": "Fires the water cannon."
    }},
    59: {"descriptions": {
        "LaunchKey": "Launches the rocket; it detonates after its built-in delay."
    }},
    61: {"descriptions": {
        "FireKey": "Fires the crossbow."
    }},
    84: {"descriptions": {
        "ShootKey": "Fires the harpoon and its attached tether.",
        "RetractKey": "Retracts the harpoon tether.",
    }},
    90: {
        "descriptions": {
            "ThrustKey": "Turns engine thrust on.",
            "OffKey": "Turns engine thrust off.",
        },
        "sliders": {
            "fthrust": "Engine thrust magnitude while ThrustKey is held. Game default is too weak for LONE ORB; send the published maximum to use full thrust.",
        },
    },
    91: {"descriptions": {
        "LeftKey": "Fires the left control nozzle.",
        "RightKey": "Fires the right control nozzle.",
    }},
    97: {"descriptions": {
        "deployKey": "Deploys the parachute."
    }},
    102: {"descriptions": {
        "FireKey": "Fires the fuel cannon."
    }},
}
