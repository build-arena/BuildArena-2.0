"""Small plain-text renderers; no changes to authoring or runtime state."""

from numbers import Real


def display(value):
    """Format display values only; never use this for command serialization."""
    if value is None or (isinstance(value, str) and not value):
        return "unknown"
    if isinstance(value, Real) and not isinstance(value, (bool, int)):
        return f"{value:.4g}"
    if isinstance(value, (list, tuple)) or hasattr(value, "shape"):
        return "[" + ", ".join(display(item) for item in value) + "]"
    return str(value)


def section(title, lines):
    return title + ":\n" + "\n".join("  " + line for item in lines for line in str(item).splitlines())


def issue(subject, reason, next_step):
    return f"Object: {subject}\nReason: {reason}\nNext: {next_step}"


def block_structure(block, *, prefix=None):
    """Render direct authoring fields, without parsing descriptor prose."""
    identity = f"{prefix}_{block.local_id}" if prefix else str(block.local_id)
    lines = [f"Build ID {identity}: {block.name}"]
    connections = []
    for label, face in (("from", block.start_point), ("to", block.end_point)):
        if face is not None:
            connections.append(f"{label} Build ID {face.local_id}, face {face.color} ({face.name}) at {face.center.coordinates}")
    lines.append("  Attachment: " + ("; ".join(connections) or "root / no authored attachment"))
    lines.append(f"  Position (build [East, North, Up]): {block.center_pos.coordinates}")
    lines.append(f"  Facing (build compass): {block.geo.rotation.caption}")
    lines.append(f"  Configuration (build): flipped={block.flipped}; telemetry_target={block.tracking}")
    if block.note:
        lines.append(f"  Note: {block.note}")
    return "\n".join(lines)


def machine_overview(machine):
    return section("Overview", [
        f"Machine: {machine.name}",
        "Structure source: authoring state / build history; not a runtime observation",
        f"Build history: {getattr(machine, 'output_sequence_path', None) or 'in-memory authoring operations'}",
        f"Blocks: {len(machine.blocks)}",
        f"Note: {machine.note}" if machine.note else "Note: none",
    ])
