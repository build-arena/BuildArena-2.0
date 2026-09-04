"""User-facing control meanings for camera and automation blocks."""

_ACTIVATE_EMULATE = {
    "ActivateKey": "Activates or toggles this automation block.",
    "EmulateKey": "Simulates its condition being satisfied and emits its configured signal.",
}

CONTROL_SEMANTICS = {
    58: {"descriptions": {
        "ActivateKey": "Switches the player view to this camera."
    }},
    62: {
        "descriptions": {"VacuumKey": "Turns the vacuum suction on."},
        "aliases": {"On": "VacuumKey"},
    },
    65: {"descriptions": dict(_ACTIVATE_EMULATE)},
    66: {"descriptions": dict(_ACTIVATE_EMULATE)},
    67: {"descriptions": dict(_ACTIVATE_EMULATE)},
    68: {"descriptions": {
        "AKey": "Supplies or toggles logic input A.",
        "BKey": "Supplies or toggles logic input B.",
        "EmulateKey": "Simulates the gate condition and emits its configured signal.",
    }},
    69: {"descriptions": dict(_ACTIVATE_EMULATE)},
    70: {"descriptions": dict(_ACTIVATE_EMULATE)},
    75: {"descriptions": dict(_ACTIVATE_EMULATE)},
    93: {"descriptions": dict(_ACTIVATE_EMULATE)},
}
