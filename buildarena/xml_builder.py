"""Structured XML assembly DSL for Besiege .bsg files.

Inspired by BesiegeMachineGenerator/pkg/generator/xml_item.py (archived,
https://github.com/drelatgithub/BesiegeMachineGenerator), adapted and
significantly extended to support the full .bsg schema used here.

An XmlNode tree is built in memory and serialised to a string in one pass,
which keeps to_xml() logic free of manual string formatting.
"""

from __future__ import annotations

from typing import Any

_INDENT = "    "
_NL = "\n"


# ---------------------------------------------------------------------------
# Core node
# ---------------------------------------------------------------------------

class XmlNode:
    """A single XML element with optional attributes, children and text body."""

    def __init__(
        self,
        tag: str,
        attrs: dict[str, Any] | None = None,
        children: list["XmlNode"] | None = None,
        text: str | None = None,
    ):
        self.tag = tag
        self.attrs: dict[str, Any] = attrs or {}
        self.children: list[XmlNode] = children or []
        self.text = text

    # -- builder helpers --------------------------------------------------------

    def add(self, child: "XmlNode") -> "XmlNode":
        self.children.append(child)
        return self

    # -- serialisation ----------------------------------------------------------

    def _attr_str(self) -> str:
        if not self.attrs:
            return ""
        return " " + " ".join(f'{k}="{v}"' for k, v in self.attrs.items())

    def render(self, depth: int = 0) -> str:
        pad = _INDENT * depth
        attr = self._attr_str()

        if self.text is not None and not self.children:
            return f"{pad}<{self.tag}{attr}>{self.text}</{self.tag}>{_NL}"

        if not self.children and self.text is None:
            return f"{pad}<{self.tag}{attr} />{_NL}"

        lines = [f"{pad}<{self.tag}{attr}>{_NL}"]
        if self.text:
            for line in self.text.splitlines():
                lines.append(f"{pad}{_INDENT}{line}{_NL}")
        for child in self.children:
            lines.append(child.render(depth=depth + 1))
        lines.append(f"{pad}</{self.tag}>{_NL}")
        return "".join(lines)


# ---------------------------------------------------------------------------
# Convenience factories — mirror the names in BesiegeMachineGenerator
# ---------------------------------------------------------------------------

def position_node(x: float, y: float, z: float) -> XmlNode:
    return XmlNode(tag="Position", attrs={"x": x, "y": y, "z": z})


def rotation_node(x: float, y: float, z: float, w: float) -> XmlNode:
    return XmlNode(tag="Rotation", attrs={"x": x, "y": y, "z": z, "w": w})


def scale_node(x: float, y: float, z: float) -> XmlNode:
    return XmlNode(tag="Scale", attrs={"x": x, "y": y, "z": z})


def transform_node(
    pos: tuple[float, float, float],
    rot: tuple[float, float, float, float],
    scale: tuple[float, float, float],
) -> XmlNode:
    node = XmlNode(tag="Transform")
    node.add(position_node(*pos))
    node.add(rotation_node(*rot))
    node.add(scale_node(*scale))
    return node


def string_node(key: str, value: str) -> XmlNode:
    return XmlNode(tag="String", attrs={"key": key}, text=value)


def string_array_node(key: str, value: str) -> XmlNode:
    return XmlNode(tag="StringArray", attrs={"key": key}, text=value)


def boolean_node(key: str, value: bool) -> XmlNode:
    return XmlNode(tag="Boolean", attrs={"key": key}, text=str(value))


def integer_node(key: str, value: int) -> XmlNode:
    return XmlNode(tag="Integer", attrs={"key": key}, text=str(value))


def single_node(key: str, value: float) -> XmlNode:
    return XmlNode(tag="Single", attrs={"key": key}, text=str(value))


def vec3_node(key: str, x: float, y: float, z: float) -> XmlNode:
    node = XmlNode(tag="Vector3", attrs={"key": key})
    node.add(XmlNode(tag="X", text=str(x)))
    node.add(XmlNode(tag="Y", text=str(y)))
    node.add(XmlNode(tag="Z", text=str(z)))
    return node


def block_node(
    block_id: str,
    guid: str,
    transform: XmlNode,
    data: XmlNode,
) -> XmlNode:
    node = XmlNode(tag="Block", attrs={"id": block_id, "guid": guid})
    node.add(transform)
    node.add(data)
    return node


def machine_node(
    name: str,
    version: int = 1,
    bsg_version: str = "1.4",
) -> XmlNode:
    return XmlNode(tag="Machine", attrs={"version": version, "bsgVersion": bsg_version, "name": name})


# ---------------------------------------------------------------------------
# Document root — produces the full <?xml ...?> header + tree
# ---------------------------------------------------------------------------

class BsgDocument:
    def __init__(self, root: XmlNode):
        self.root = root

    def render(self) -> str:
        header = '<?xml version="1.0" encoding="utf-8"?>' + _NL
        return header + self.root.render(depth=0)
