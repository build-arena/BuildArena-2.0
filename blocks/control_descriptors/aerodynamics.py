"""User-facing control meanings for sails and aerodynamic steering."""

CONTROL_SEMANTICS = {
    78: {
        "descriptions": {
            "RaiseSail": "Raises the sail.",
            "LowerSail": "Lowers the sail.",
        },
        "aliases": {
            "raiseSailKey": "RaiseSail",
            "lowerSailKey": "LowerSail",
        },
    },
    79: {"descriptions": {
        "leftKey": "Deflects the rudder toward its left control limit.",
        "rightKey": "Deflects the rudder toward its right control limit.",
    }},
}
