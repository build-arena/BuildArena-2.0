import os
from typing import Annotated, List, Dict, Literal, Union, Any
import uuid
import json
import tomllib
import numpy as np
import quaternion
import copy
from datetime import datetime
import re

from pathlib import Path

import trimesh
from trimesh.collision import CollisionManager
from trimesh import Trimesh

from .components import Vector, Geometry, describe_spin, Orientation
from .utils import *
from .definitions_loader import load_runtime_blocks
from .block_authoring import load_block_authoring
from .descriptor_loader import bind_block_descriptor
from .paths import get_block_registry_path, resolve_project_path

# Blocks whose custom descriptor string already includes ``describe_spin`` output
# (must not append the default spin line again to avoid duplication).
_BLOCK_IDS_WITH_DESCRIPTOR_INCLUDING_SPIN: frozenset[int] = frozenset({48, 80})

# Blocks that visually spin but whose dump behaviour tags don't expose a
# spinning controller, so the registry generator emits ``spin = false``.
# We still want the caption to read the default rotating-body line, so we
# inject a fallback spin config used *only* by ``Block.caption``.  The
# real ``self.spin`` field is left untouched so save-game payloads that
# don't include a ``flipped`` boolean for these blocks remain accurate.
_BLOCK_IDS_FORCING_DEFAULT_SPIN_CAPTION: dict[int, dict[str, Any]] = {
    17: {"axis": [0.0, 0.0, 1.0], "handedness": "left"},  # Circular Saw
}

BLOCK_TRACKER_MOD_ID = "1d45bae7-50b4-4137-963e-27e45f6ece74"
BLOCK_TRACKER_MOD_VERSION = "1.0.0"
BLOCK_TRACKER_MOD_NAME = "BuildArena Block Tracker"


def besiege_required_mod_entry(*, mod_id: str, version: str, name: str) -> str:
    return f"{mod_id}~L~{version}~{name}"


DEFAULT_REGISTRY_PATH = get_block_registry_path()
DEFAULT_CATEGORY_PATH = DEFAULT_REGISTRY_PATH.parent / "block_categories.toml"
registry_path = DEFAULT_REGISTRY_PATH
all_blocks = load_runtime_blocks(registry_path=registry_path)

AvailableBlocks = [key for key, value in all_blocks.items() if value['type'] in ['basic', 'pointer'] and key != 'Starting Block' and not value['disable']]
AvailableConnectors = [key for key, value in all_blocks.items() if value['type'] == 'connection' and not value['disable']]
AvailableKeys = [
    'UpArrow', 'DownArrow', 'LeftArrow', 'RightArrow', 
    'Alpha0 to Alpha9, for example, Alpha1', 
    'Keypad0 to Keypad9, for example, Keypad1', 
    ]


def _block_category_index() -> dict[str, list[tuple[str, str]]]:
    category_index: dict[str, list[tuple[str, str]]] = {}
    for block_name, block_data in all_blocks.items():
        if bool(block_data.get("disable", False)):
            continue

        summary = str(block_data.get("summary", "")).strip()
        if summary == "":
            raise ValueError(f"Block '{block_name}' is missing summary metadata.")

        raw_categories = block_data.get("category", [])
        if not isinstance(raw_categories, list) or not all(
            isinstance(category, str) and category.strip() != ""
            for category in raw_categories
        ):
            raise ValueError(f"Block '{block_name}' has invalid category metadata.")

        for category in raw_categories:
            category_index.setdefault(category, []).append((block_name, summary))

    return {
        category: sorted(block_summaries)
        for category, block_summaries in sorted(category_index.items())
    }


def _format_available_categories(*, category_index: dict[str, list[tuple[str, str]]]) -> str:
    return ", ".join(category_index.keys())


def _load_block_categories() -> dict[str, dict[str, str | bool]]:
    if not DEFAULT_CATEGORY_PATH.is_file():
        raise FileNotFoundError(f"Block category summary file not found: {DEFAULT_CATEGORY_PATH}")

    with open(DEFAULT_CATEGORY_PATH, "rb") as file:
        loaded = tomllib.load(file)

    categories_raw = loaded.get("categories", {})
    if not isinstance(categories_raw, dict):
        raise ValueError("Block category summary file must define a [categories] table.")

    categories: dict[str, dict[str, str | bool]] = {}
    for category_name, category_entry in categories_raw.items():
        if not isinstance(category_name, str) or category_name.strip() == "":
            raise ValueError("Block category names must be non-empty strings.")
        if category_name != category_name.strip():
            raise ValueError(f"Block category name has edge whitespace: {category_name!r}")
        if not isinstance(category_entry, dict):
            raise ValueError(f"Block category '{category_name}' must be a table.")

        enabled = category_entry.get("enable")
        if not isinstance(enabled, bool):
            raise ValueError(f"Block category '{category_name}' requires boolean enable.")

        summary = str(category_entry.get("summary", "")).strip()
        if summary == "":
            raise ValueError(f"Block category '{category_name}' requires a non-empty summary.")
        categories[category_name] = {
            "enable": enabled,
            "summary": summary,
        }

    runtime_categories = set(_block_category_index().keys())
    authored_categories = set(categories.keys())
    missing_categories = sorted(runtime_categories - authored_categories)
    disabled_runtime_categories = sorted(
        category
        for category in runtime_categories & authored_categories
        if categories[category]["enable"] is not True
    )
    if missing_categories:
        raise ValueError(
            "Block category summary file is missing runtime categories: "
            f"{', '.join(missing_categories)}."
        )
    if disabled_runtime_categories:
        raise ValueError(
            "Runtime categories must be enabled in the block category summary file: "
            f"{', '.join(disabled_runtime_categories)}."
        )
    # Platform-specific DLC availability can remove every runtime block from a
    # category. Keep those categories invisible to MCP instead of forcing the
    # authored category file to change per platform.
    return {
        category: categories[category]
        for category in sorted(runtime_categories)
    }


def _format_category_summary_lines(*, category_summaries: dict[str, dict[str, str | bool]]) -> list[str]:
    return [
        f"- Category: {category_name}; Enabled: {category_entry['enable']}; "
        f"Summary: {category_entry['summary']}"
        for category_name, category_entry in category_summaries.items()
    ]


def _format_category_module_lines(
    *,
    category_name: str,
    block_summaries: list[tuple[str, str]],
) -> list[str]:
    return [
        f"- Category: {category_name}; Block: {block_name}; Summary: {summary}"
        for block_name, summary in block_summaries
    ]


def _block_authoring_by_name() -> dict[str, tuple[int, dict[str, str]]]:
    authoring = load_block_authoring()
    by_name: dict[str, tuple[int, dict[str, str]]] = {}
    for block_id, entry in authoring.items():
        block_name = str(entry.get("block_name", "")).strip()
        if block_name == "":
            raise ValueError(f"Block authoring entry for id={block_id} requires block_name.")
        if block_name in by_name:
            previous_id = by_name[block_name][0]
            raise ValueError(
                f"Duplicate block authoring name '{block_name}' for ids {previous_id} and {block_id}."
            )
        by_name[block_name] = (block_id, entry)
    return by_name


def _available_runtime_block_names() -> set[str]:
    return {
        block_name
        for block_summaries in _block_category_index().values()
        for block_name, _summary in block_summaries
    }

class Block:
    # Basic Block class for managing the block's geometry, collider, faces, and caption
    def __init__(self, block_dict: Dict, local_id: str, start_point: Face, collider_scale: int = 0.8, note: str = None, registry_path: Path = DEFAULT_REGISTRY_PATH):
        self.id = str(block_dict['id'])
        self.local_id = local_id
        self.name: str = block_dict['name']
        self.type: str = block_dict['type']
        self.mesh_key: str | None = block_dict.get('mesh_key')
        self.registry_path = registry_path
        self.geo = Geometry(block_dict['vec_base'], block_dict['shape'], block_dict['root'], block_dict['scale'] if 'scale' in block_dict.keys() else None)
        self.root = Vector(block_dict['root'])
        self.center_offset: float = block_dict['center_offset']
        self.root_offset: float = float(block_dict.get('root_offset', 0.0))
        self.start_point = start_point
        if self.start_point is not None:
            base_pos = self.start_point.center.real
            if self.root_offset > 0:
                base_pos = base_pos + self.root_offset * self.start_point.normal.vec_abs.real
            self.geo.position = Vector.from_real(base_pos)
            self.geo.rotation = Orientation(rot=self.start_point.normal.quat, vec_base=Vector(block_dict['vec_base']))
        
        self.wiki: str = block_dict['wiki']
        self.summary: str = block_dict.get("summary", "")
        self.description: str = block_dict['description']
        self.note: str = note
        
        # Collider
        self.collider_scale = collider_scale
        self.collider_mode: str = block_dict.get('collider_mode', 'obb')
        self.collider_shrink: float = float(block_dict.get('collider_shrink', 0.85))
        self.init_collider(registry_path=registry_path)
        
        # Attachable Faces
        self.init_faces(block_dict['faces'])
        self._mark_start_mount_faces()
        
        # Spin config for powered rotating blocks:
        # {'axis': [x, y, z], 'handedness': 'left'|'right'}
        self.spin: Dict = block_dict['spin'] if 'spin' in block_dict else None
        self.flipped = False
        
        # Shoot direction
        self.shoot: Dict = block_dict['shoot'] if 'shoot' in block_dict else None
        self.pointer_axis = block_dict.get("pointer_axis", False)
        
        # Locomotion
        self.locomotion: Dict[str, List[str]] = block_dict['locomotion']
        self.data: str = block_dict['data']
        
        # Connector specific
        self.end_point: Face = None
        self.projection: Vector = None
        
        # Customized descriptor (managed by blocks/block_authoring.toml)
        self.descriptor = bind_block_descriptor(
            block=self,
            block_id=int(self.id),
        )
        
        # Cost
        self.cost = block_dict['cost']

        # Simulation
        self.tracking = False
        self.change_mass = False
        self.guid = str(uuid.uuid4())
        ref_name = self.name
        self.ref_key = f"{ref_name.replace(' ', '').replace('_', '')}_{self.guid.split('-')[0]}"
    
    @property
    def center_pos(self) -> Vector:
        if self.start_point is None:
            # Starting block
            return self.geo.position
        # Other blocks
        return Vector.from_real(self.start_point.center.real + self.center_offset * self.start_point.normal.vec_abs.real)

    def _spin_rotation_vector(self, *, spin_config: dict | None = None) -> Vector:
        config = spin_config if spin_config is not None else self.spin
        if not isinstance(config, dict):
            raise ValueError("Spin config must be a dict with axis/handedness.")

        axis_value = config.get("axis")
        handedness_value = str(config.get("handedness", "")).strip().lower()
        if not isinstance(axis_value, list) or len(axis_value) != 3:
            raise ValueError(f"Invalid spin axis for block '{self.name}' (id={self.id}).")
        if handedness_value not in {"left", "right"}:
            raise ValueError(
                f"Invalid spin handedness for block '{self.name}' (id={self.id}): {handedness_value}"
            )

        axis_local = np.asarray(axis_value, dtype=np.float64)
        axis_norm = np.linalg.norm(axis_local)
        if axis_norm <= 1e-9:
            raise ValueError(f"Spin axis cannot be zero for block '{self.name}' (id={self.id}).")
        axis_local = axis_local / axis_norm
        axis_world = self.geo.rotation.rot_mat @ axis_local

        handedness_sign = 1.0 if handedness_value == "left" else -1.0
        flipped_sign = -1.0 if self.flipped else 1.0
        rotation_vector = handedness_sign * flipped_sign * axis_world
        return Vector(vector=rotation_vector)

    def _pointer_direction_vector(self) -> Vector:
        if self.type != "pointer":
            raise ValueError(f"Block '{self.name}' is not a pointer block.")
        if not isinstance(self.pointer_axis, list) or len(self.pointer_axis) != 3:
            raise ValueError(f"Pointer block '{self.name}' must define pointer_axis=[x,y,z].")

        axis_local = np.asarray(self.pointer_axis, dtype=np.float64)
        axis_norm = np.linalg.norm(axis_local)
        if axis_norm <= 1e-9:
            raise ValueError(f"Pointer axis cannot be zero for block '{self.name}' (id={self.id}).")
        axis_local = axis_local / axis_norm
        axis_world = self.geo.rotation.rot_mat @ axis_local
        return Vector(vector=axis_world)

    def caption(self, finished=False, prefix: str = None):
        """Return face captions if not finished"""
        message = []
        if self.note:
            message.append(f'({self.note}) <ID {prefix}_{self.local_id}: {self.name}>' if prefix else f'({self.note}) <ID {self.local_id}: {self.name}>')
        else:
            message.append(f'<ID {prefix}_{self.local_id}: {self.name}>' if prefix else f'<ID {self.local_id}: {self.name}>')
        message.append(f'Position: {self.center_pos.coordinates}')
        
        if self.descriptor:
            description = self.descriptor()
            message.append(description)

        spin_for_caption = self.spin or _BLOCK_IDS_FORCING_DEFAULT_SPIN_CAPTION.get(
            int(self.id)
        )
        if spin_for_caption and int(self.id) not in _BLOCK_IDS_WITH_DESCRIPTOR_INCLUDING_SPIN:
            rotation_vector = self._spin_rotation_vector(spin_config=spin_for_caption)
            message.append(describe_spin(rotation_vector=rotation_vector))
        
        if not finished and len([face for face in self.faces.values() if face.sticky and face.role != "joint"]) > 0:
            message.append('Attachable Faces:')
            for face in self.faces.values():
                if face.sticky and face.role != "joint":
                    message.append(face.caption) 
        
        message = '\n'.join(message)
        return str(message)
    
    def init_faces(self, faces_dict: Dict):
        # Initialize the faces of the block
        self.faces : Dict[str, Face] = {}
        self.joint_faces : Dict[str, Face] = {}
        if faces_dict is None:
            from .collider_loader import infer_addpoint_faces_from_dump, infer_joint_faces_from_dump
            inferred_addpoint_faces = infer_addpoint_faces_from_dump(
                block_id=int(self.id),
                shape=self.geo.shape.virtual,
            )
            inferred_joint_faces = infer_joint_faces_from_dump(
                block_id=int(self.id),
                shape=self.geo.shape.virtual,
            )
            merged = {}
            if inferred_addpoint_faces is not None:
                merged.update(inferred_addpoint_faces)
            if inferred_joint_faces is not None:
                for key, value in inferred_joint_faces.items():
                    merged[f"J_{key}"] = value
            faces_dict = merged if len(merged) > 0 else None
        if faces_dict is not None:
            for name, face in faces_dict.items():
                if isinstance(face, dict):
                    rel_pos = face.get("rel_pos", [0.0, 0.0, 0.0])
                    normal = face.get("normal", [0.0, 0.0, 1.0])
                    role = face.get("role", "unknown")
                else:
                    rel_pos = face[0]
                    normal = face[1]
                    role = "unknown"
                face_obj = Face(
                    name,
                    rel_pos,
                    normal,
                    self.geo,
                    self.local_id,
                    self.name,
                    role=role,
                )
                if role == "joint":
                    face_obj.sticky = False
                    self.joint_faces[name] = face_obj
                    continue
                self.faces[name] = face_obj

    def _mark_start_mount_faces(self):
        """Mark the mounted side of an attached block as occupied."""
        if self.start_point is None:
            return

        start_normal = self.start_point.normal.vec_abs.virtual
        candidates = []
        for face in self.faces.values():
            if face.role == "joint":
                continue
            if not bool(face.sticky):
                continue
            face_normal = face.normal.vec_abs.virtual
            if float(np.dot(face_normal, start_normal)) >= -0.9:
                continue
            projection = float(
                np.dot(face.center.virtual - self.start_point.center.virtual, start_normal)
            )
            candidates.append((projection, face))

        if len(candidates) == 0:
            return

        attached_faces = [
            face
            for _, face in candidates
            if face_is_attached(face_a=self.start_point, face_b=face)
        ]
        if len(attached_faces) > 0:
            for face in attached_faces:
                face.sticky = False
                face.att_to = self.start_point
                if self.start_point.att_to is None:
                    self.start_point.att_to = face
            return

        min_projection = min(item[0] for item in candidates)
        for projection, face in candidates:
            if not np.isclose(a=projection, b=min_projection, atol=1e-4):
                continue
            face.sticky = False
            face.att_to = self.start_point
            if self.start_point.att_to is None:
                self.start_point.att_to = face
    
    def init_collider(self, registry_path: Path = DEFAULT_REGISTRY_PATH):
        from .mesh_loader import load_aligned_game_mesh
        from .collider_loader import build_collider_mesh, get_visual_transform

        shape_extents = np.asarray(self.geo.shape.virtual.tolist(), dtype=np.float64)
        block_id = int(self.id)
        visual_transform = get_visual_transform(block_id=block_id)

        if self.type != 'connection':
            real_collider = build_collider_mesh(block_id=block_id, solid_only=True)
            if real_collider is None:
                raise RuntimeError(
                    f"No solid colliders found in dump for block '{self.name}' "
                    f"(id={block_id}). Dump data is required."
                )
            self.collider = real_collider

            aligned_mesh = load_aligned_game_mesh(
                mesh_key=self.mesh_key,
                visual_transform=visual_transform,
                registry_path=registry_path,
            )
            self.outline = aligned_mesh
        else:
            self.collider = None
            self.outline = trimesh.Trimesh(
                vertices=np.empty(shape=(0, 3)),
                faces=np.empty(shape=(0, 3), dtype=np.int64),
            )

        self.update_collider()
        
    def update_collider(self):
        # Refresh the collider and outline
        T = np.eye(4)
        T[:3, :3] = self.geo.rotation.rot_mat
        T[:3, 3] = self.geo.position.virtual
        if self.collider is not None:
            self.collider.apply_transform(T)
        self.outline.apply_transform(T)
    
    def update_faces(self):
        # Refresh the faces
        for face in self.faces.values():
            face.update_block_geo(self.geo)
        for face in self.joint_faces.values():
            face.update_block_geo(self.geo)
            
    def rotate(self, yaw, pitch, roll):
        """Rotate the block using Z-Y-X (yaw-pitch-roll) angles in degrees"""
        R = rotation_matrix(yaw, pitch, roll)
        self.geo.rotation = self.geo.rotation.rotate(R)
        # Update the collider
        self.init_collider(registry_path=self.registry_path)
        # Update the faces
        self.update_faces()
    
    def twist(self, angle: float):
        """Twist the block clockwise relative to its rooted surface, angles in degrees"""
        # Map the angle to the range of -180 to 180
        angle = np.mod(angle, 360)
        if angle > 180:
            angle -= 360
        # Compute the rotation matrix
        angle = np.deg2rad(angle)
        # Compute the rotation vector
        norm = self.start_point.normal.vec_abs.norm
        axis_angle_quat = quaternion.from_rotation_vector(angle * norm.virtual)
        # Compute the rotation matrix relative to the normal vector
        R = quaternion.as_rotation_matrix(axis_angle_quat)
        # Update the block's rotation
        self.geo.rotation = self.geo.rotation.rotate(R)
        # Update the collider
        self.init_collider(registry_path=self.registry_path)
        # Update the faces
        self.update_faces()
        
    def shift(self, shift_real: List):
        """Shift the block by [x, y, z] (real coordinates)"""
        shift = Vector.from_real(shift_real)
        pos_old = self.geo.position.virtual
        pos_new = pos_old + shift.virtual
        self.geo.position = Vector(pos_new)
        # Update the collider
        self.init_collider(registry_path=self.registry_path)
        # Update the faces
        self.update_faces()
    
    # Machine export
    def to_xml_node(self) -> "XmlNode":
        from .xml_builder import XmlNode, transform_node, block_node
        pos = self.geo.position.virtual
        rot = self.geo.rotation.quat
        scale = self.geo.scale.virtual

        tr = transform_node(
            pos=(pos[0], pos[1], pos[2]),
            rot=(rot.x, rot.y, rot.z, rot.w),
            scale=(scale[0], scale[1], scale[2]),
        )

        data_node = XmlNode(tag="Data")
        data_text = self._render_data_payload()
        if data_text:
            data_node.text = data_text

        return block_node(
            block_id=self.id,
            guid=self.guid,
            transform=tr,
            data=data_node,
        )

    def _render_data_payload(self) -> str:
        if self.type == "connection":
            return self._render_connection_payload()
        if self.spin:
            if isinstance(self.data, str) and self.data.strip() != "":
                return self.data.format(flipped=self.flipped)
            return f"<Boolean key=\"flipped\">{self.flipped}</Boolean>"
        if self.shoot:
            return self.data.format(
                hold_to_fire="".join(f"<String>{key}</String>" for key in set(self.locomotion['hold_to_fire']))
            )
        return self.data or ""

    def _render_connection_payload(self) -> str:
        """Serialize connector endpoint payload using a fixed, shared schema."""
        if self.end_point is None or self.start_point is None or self.projection is None:
            return ""

        projection = self.projection.virtual
        start_euler = self.start_point.normal.euler.virtual
        end_euler = self.end_point.normal.euler.virtual

        return (
            "<Vector3 key=\"start-position\">\n"
            "<X>0</X>\n"
            "<Y>0</Y>\n"
            "<Z>0</Z>\n"
            "</Vector3>\n"
            "<Vector3 key=\"end-position\">\n"
            f"<X>{projection[0]}</X>\n"
            f"<Y>{projection[1]}</Y>\n"
            f"<Z>{projection[2]}</Z>\n"
            "</Vector3>\n"
            "<Vector3 key=\"start-rotation\">\n"
            f"<X>{start_euler[0]}</X>\n"
            f"<Y>{start_euler[1]}</Y>\n"
            f"<Z>{start_euler[2]}</Z>\n"
            "</Vector3>\n"
            "<Vector3 key=\"end-rotation\">\n"
            f"<X>{end_euler[0]}</X>\n"
            f"<Y>{end_euler[1]}</Y>\n"
            f"<Z>{end_euler[2]}</Z>\n"
            "</Vector3>"
        )

    def to_xml(self, indent_level: int = 0) -> list[str]:
        """Legacy list-of-lines interface kept for backward compatibility."""
        raw = self.to_xml_node().render(depth=indent_level)
        return raw.rstrip("\n").splitlines(keepends=True)
    
class Connector(Block):
    # Connector class for managing the connector's geometry and caption
    def __init__(self, block_dict: Dict, local_id, start_point: Face = None, end_point: Face = None, note: str = None, registry_path: Path = DEFAULT_REGISTRY_PATH):
        super().__init__(block_dict, local_id, start_point, note=note, registry_path=registry_path)
        self.end_point = end_point
        # Compute the projection vector
        projection_global = self.end_point.center.virtual - self.start_point.center.virtual
        # Compute the projection vector in the local coordinate system of starting face
        R = self.start_point.normal.rot_mat
        self.projection = Vector(R.T @ projection_global)
        self.outline = create_connector_mesh(
            point_a=self.start_point.center.virtual,
            point_b=self.end_point.center.virtual)
    
    def caption(self, finished, prefix: str = None):
        message = []
        if self.note:
            message.append(f'({self.note}) <ID {prefix}_{self.local_id}: {self.name}>' if prefix else f'({self.note}) <ID {self.local_id}: {self.name}>')
        else:
            message.append(f'<ID {prefix}_{self.local_id}: {self.name}>' if prefix else f'<ID {self.local_id}: {self.name}>')
        message.append(
            f'Connecting <ID {self.start_point.local_id}: {self.start_point.name}> at {self.start_point.center.real} and <ID {self.end_point.local_id}: {self.end_point.name}> at {self.end_point.center.real}.\t' 
            )
        
        if self.descriptor:
            description = self.descriptor()
            message.append(description)
            
        message = '\n'.join(message)
        return str(message)
    
class Pointer(Block):
    # Pointer class for managing the pointer's geometry and caption (different from the basic block)
    def __init__(self, block_dict: Dict, local_id, start_point: Face = None, note: str = None, registry_path: Path = DEFAULT_REGISTRY_PATH):
        super().__init__(block_dict, local_id, start_point, note=note, registry_path=registry_path)
        
    def caption(self, finished, prefix: str = None):
        message = []
        if self.note:
            message.append(f'({self.note}) <ID {prefix}_{self.local_id}: {self.name}>' if prefix else f'({self.note}) <ID {self.local_id}: {self.name}>')
        else:
            message.append(f'<ID {prefix}_{self.local_id}: {self.name}>' if prefix else f'<ID {self.local_id}: {self.name}>')
        message.append(f'Position: {self.center_pos.coordinates}')
        pointer_direction = self._pointer_direction_vector()
        message.append(f'Pointing at {pointer_direction.caption}')
        
        if self.descriptor:
            description = self.descriptor()
            message.append(description)

        message = '\n'.join(message)
        return str(message)
    
class Blocks:
    # Initialize all blocks and for system prompt
    def __init__(self, registry_path: Path = DEFAULT_REGISTRY_PATH):
        self.blocks: Dict[str, Dict] = load_runtime_blocks(registry_path=registry_path)

        self.available_blocks = [key for key, value in self.blocks.items() if (value['type'] == 'basic' or value['type'] == 'pointer') and key != 'Starting Block']
        self.available_connectors = [key for key, value in self.blocks.items() if value['type'] == 'connection']
        self.registry_path = registry_path

    def __call__(self):
        # Return the caption of all available blocks and connectors
        message = []
        message.append(f"\n{len(self.available_blocks)} kinds of available blocks: ")
        for key in self.available_blocks:
            if self.blocks[key]["disable"]:
                continue
            message.append(f'<{key}> shape: {self.blocks[key]["shape"]}, mass: {self.blocks[key]["weight"]}')
            message.append(f'Description: {self.blocks[key]["description"]}')
        message.append(f"\n{len(self.available_connectors)} kinds of available connectors: ")
        message.append(f'Important: Connectors can not be attached to other blocks, it can only be used to connect two blocks. Connectors does not have physical volume nor collider.')
        for key in self.available_connectors:
            if self.blocks[key]["disable"]:
                continue
            message.append(f'<{key}> mass: {self.blocks[key]["weight"]}')
            message.append(f'Description: {self.blocks[key]["description"]}')
        
        message = '\n'.join(message)
        return str(message)

    def available_connector_names(self) -> list[str]:
        return [
            name
            for name in self.available_connectors
            if not self.blocks[name]["disable"]
        ]

    def has_available_connector(self, *, connector_name: str) -> bool:
        block_data = self.blocks.get(connector_name)
        if block_data is None:
            return False
        return block_data["type"] == "connection" and not block_data["disable"]
    
    def get(self, block_name: str, local_id: str, start_point: Face = None, end_point: Face = None, note: str = None):
        # Get an instance of the specific block
        block_data = self.blocks.get(block_name)
        if block_data['type'] == 'connection':
            block = Connector(block_data, local_id, start_point=start_point, end_point=end_point, note=note, registry_path=self.registry_path)
        elif block_data['type'] == 'basic':
            block = Block(block_data, local_id, start_point=start_point, note=note, registry_path=self.registry_path)
        elif block_data['type'] == 'pointer':
            block = Pointer(block_data, local_id, start_point=start_point, note=note, registry_path=self.registry_path)
        return block

class Machine:
    # Machine class for managing the machine's geometry, collider, blocks, and caption
    def __init__(self, 
                 name: str | None = None, 
                 save_dir: str = './.local/default/machine', 
                 note: str | None = None, 
                 do_collision: bool = True, 
                 collision_tolerance: float = 0.01,
                 tmp_dir: str = None,
                 registry_path: Path = DEFAULT_REGISTRY_PATH,
                 write_full_history: bool = True):
        if name is None or str(name).strip() == "":
            raise ValueError("Machine name must be a non-empty string.")
        self.name = name
        self.save_dir = save_dir
        self.note = note
        self.write_full_history = write_full_history
        self.blocks_storage = Blocks(registry_path=registry_path)
        
        # NOTE: Collision detection switch
        self.do_collision = do_collision
        self.collision_tolerance = float(collision_tolerance)
        self.collision_manager = CollisionManager()

        # Control sequence
        self.tracker_config: dict[str, Any] | None = None
        
        # Record Operations
        self.blocks: Dict[str, Block] = {}
        self.operation_history = []
        self.full_op_history_path = os.path.join(self.save_dir, f"{self.name}_full.json")
        self.init_full_op_history()
        self.uid = 1
        self.machines: Dict[str, Machine] = {}
        
        # Register operations
        self.record_op = False
        self.operations = {}
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if callable(attr) and getattr(attr, "_is_operation", False):
                self.operations[attr_name] = attr
        self.tools = {}
        self.started = False

        for operation in self.operations.values():
            if operation._group not in self.tools.keys():
                self.tools[operation._group] = []
            self.tools[operation._group].append(operation)
            
        self.local_index = None
            
    def log_failed_operation(self, func_name: str, message: str):
        self.operation_history_full.append({
            "op": "failed",
            "params": {"func_name": func_name, "message": message}
        })
        self.save_full_op_history()
    
    @property
    def cost(self):
        return sum([block.cost for block in self.blocks.values()])
    
    @property
    def num_blocks(self):
        return len(self.blocks)

    @property
    def has_spinful(self):
        return any([block.spin for block in self.blocks.values()])

    def add_collider(self, block: Block):
        if not isinstance(block, Connector):
            self.collision_manager.add_object(block.local_id, block.collider)
        else:
            pass
    
    def clean_colliders(self):
        obj_names = [name for name in self.collision_manager._objs.keys()]
        for obj in obj_names:
            self.collision_manager.remove_object(name=obj)
        
    def refresh_colliders(self):
        """Clean all colliders and add all blocks back to the collision manager"""
        self.clean_colliders()
        for block in self.blocks.values():
            self.add_collider(block)
            
    def refresh_collider(self, block: Block):
        """Remove the original block and add the new block after twisting or rotating"""
        # Check if the block in the collision manager
        if block.local_id in self.collision_manager._objs.keys():
            # Remove the collider from the collision manager    
            self.collision_manager.remove_object(block.local_id)
        # Add the collider back to the collision manager
        self.add_collider(block)

    @operation(group="build")
    def start(self, init_shift: List[float] = [0, 0, 0], init_rotation: List[float] = [0, 0, 0], note: str = None):
        """
        Start to build the machine by creating and positioning the starting block.
        
        Args:
            init_shift (List[float]): Initial position offset [x, y, z] in real coordinates
            init_rotation (List[float]): Initial rotation [yaw, pitch, roll] in degrees
            note (str): Description about the machine you want to build
            
        Returns:
            str: Status message about the starting block
        """
        if self.started:
            error_message = "Machine already exists"
            self.update_prompt(pre_msg=error_message, complete=True, return_summary=True)
            self.log_failed_operation("start", error_message)
            return self.prompt
        starting_block = self.blocks_storage.get('Starting Block', '1', start_point=None, note="The starting block")
        starting_block.shift(init_shift)
        starting_block.rotate(init_rotation[0], init_rotation[1], init_rotation[2])
        collision_msg = self._add_block(starting_block)
        self.record_op = True
        self.started = True
        self.note = note
        
        return self.prompt

    @operation(group="build")
    def reset(self):
        """
        Reset the machine to its initial state without any blocks.
        
        Args:
            None
            
        Returns:
            None
        """
        self.save_full_op_history()
        self.__init__(name=self.name, 
                      save_dir=self.save_dir, 
                      note=self.note, 
                      do_collision=self.do_collision,
                      collision_tolerance=self.collision_tolerance,
                      write_full_history=self.write_full_history)
        return "The machine has been reset, please start again."

    def update_prompt(self, pre_msg = None, complete = False, return_summary = False, locomotion = False, prefix: str = None):
        """
        Update the machine prompt message.
        
        Args:
        - pre_msg: str, the message to be displayed before the summary.
        - complete: bool, whether to display the complete summary of the machine. If True, all blocks will be displayed but without face captions. If False, only the last block will be displayed with face captions.
        - return_summary: bool, whether to return the machine state message. If False, only the pre_msg will be displayed.
        """
        message = []
        if pre_msg:
            message.append(pre_msg)
            
        if locomotion:
            message.append(self.review_powered_blocks())
        
        if return_summary:
            message.append(f'Existing Blocks: {len(self.blocks)}')
            if complete:
                message.append(f'\nMachine Summary: {self.note}')
                for block in self.blocks.values():
                    message.append(block.caption(finished = complete, prefix = prefix))
            else:
                # Show the last block if not complete
                max_uid = [key for key in self.blocks.keys()][-1]
                message.append(self.blocks[str(max_uid)].caption(finished = complete, prefix = prefix))
        self.prompt = '\n'.join(message)
    
    @operation(log=False)
    def get_machine_summary(self):
        """
        Get the latest state of the machine without face captions, provide the overview of the machine.
        If the block and face details are needed for further operations, use get_block_details.
        Important: It is mandatory to use this tool for a final check before the termination of the current process. Always remind the collaborator.
        
        Args:
            None
            
        Returns:
            str: The latest state of the machine
        """
        self.update_prompt(complete=True, return_summary=True)
        return self.prompt
    
    @operation(log=False)
    def get_block_details(self, block_id: Union[str, int]):
        """
        Get the complete details of a specific block, including its position, rotation, and face details.
        
        Args:
            block_id (Union[str, int]): ID of the block to get details for

        Returns:
            str: The details of the block
        """
        if isinstance(block_id, int):
            block_id = str(block_id)
        if block_id in self.blocks.keys():
            return self.blocks[block_id].caption(finished=False)
        else:
            return f"Block {block_id} not found"

    @operation(group="query")
    def list_block_categories(self) -> str:
        """
        List available block categories with their high-level summaries.

        Args:
            None

        Returns:
            str: Text entries containing category names and summary descriptions.
        """
        category_summaries = _load_block_categories()
        if len(category_summaries) == 0:
            raise RuntimeError("No block category summaries were found.")

        lines = [f"Available block categories ({len(category_summaries)}):"]
        lines.extend(_format_category_summary_lines(category_summaries=category_summaries))
        return "\n".join(lines)

    @operation(group="query")
    def list_blocks_by_category(self, category: str) -> str:
        """
        List available blocks in one exact registry category.

        Args:
            category (str): Exact category name returned by list_block_categories.

        Returns:
            str: Text entries containing category, block name, and summary.
        """
        category_index = _block_category_index()
        if len(category_index) == 0:
            raise RuntimeError("No enabled block modules were found in the block registry.")

        if not isinstance(category, str):
            raise TypeError("category must be a string.")
        if category != category.strip() or category == "":
            raise ValueError("category must be a non-empty exact category name without edge whitespace.")
        if category not in category_index:
            category_summaries = _load_block_categories()
            if category in category_summaries:
                raise ValueError(
                    f"Block category '{category}' is authored but has no enabled blocks in the runtime registry."
                )
            available_categories = ", ".join(category_summaries.keys())
            raise ValueError(
                f"Unknown block category '{category}'. Available categories: {available_categories}."
            )

        block_summaries = category_index[category]
        lines = [f"Category '{category}' blocks ({len(block_summaries)}):"]
        lines.extend(
            _format_category_module_lines(
                category_name=category,
                block_summaries=block_summaries,
            )
        )
        return "\n".join(lines)

    @operation(group="query")
    def get_block_description(self, module_name: str) -> str:
        """
        Get the authored description text for a block module.

        Args:
            module_name (str): Exact block module name returned by list_blocks_by_category.

        Returns:
            str: Text containing the block name and authored description.
        """
        if not isinstance(module_name, str):
            raise TypeError("module_name must be a string.")
        if module_name != module_name.strip() or module_name == "":
            raise ValueError("module_name must be a non-empty exact module name without edge whitespace.")

        authoring_by_name = _block_authoring_by_name()
        available_block_names = _available_runtime_block_names()
        if module_name not in available_block_names:
            raise ValueError(
                f"Unknown module_name '{module_name}'. Use list_blocks_by_category first "
                "and pass one of the returned module names exactly."
            )
        if module_name not in authoring_by_name:
            raise ValueError(f"Block authoring entry for '{module_name}' is missing.")

        _block_id, entry = authoring_by_name[module_name]
        description = str(entry.get("description", "")).strip()
        if description == "":
            raise ValueError(f"Block authoring entry for '{module_name}' is missing description.")

        return "\n".join(
            [
                f"Block Name: {module_name}",
                f"Description: {description}",
            ]
        )

    # Machine reuse
    def shift(self, shift_real: List):
        """
        Shift the entire machine by a specified offset.
        
        Args:
            shift_real (List[float]): Offset vector [x, y, z]
            
        Returns:
            None
        """
        shift = Vector.from_real(shift_real)
        for i, op in enumerate(self.operation_history):
            if op['params'].get('init_shift') is not None:
                shift_old: List[float] = self.operation_history[i]['params']['init_shift']
                shift_new = shift_old + shift.virtual
                self.operation_history[i]['params']['init_shift'] = shift_new.tolist()
        
        self.rebuild_from_history(self.operation_history)
        self.record_op = False
        
    def rotate(self, yaw, pitch, roll):
        """
        Rotate the entire machine using Z-Y-X (yaw-pitch-roll) angles.
        
        Args:
            yaw (float): Rotation around Z axis in degrees
            pitch (float): Rotation around Y axis in degrees
            roll (float): Rotation around X axis in degrees
            
        Returns:
            None
        """
        R = rotation_matrix(yaw, pitch, roll)
        for i, op in enumerate(self.operation_history):
            if op['params'].get('init_rotation') is not None:
                rot_old: List[float] = self.operation_history[i]['params']['init_rotation']
                R_old = rotation_matrix(rot_old[0], rot_old[1], rot_old[2])
                R_new = R @ R_old
                rot_new = [
                    np.degrees(np.arctan2(R_new[1,0], R_new[0,0])),  # yaw
                    np.degrees(np.arcsin(-R_new[2,0])),  # pitch 
                    np.degrees(np.arctan2(R_new[2,1], R_new[2,2]))   # roll
                ]
                self.operation_history[i]['params']['init_rotation'] = [rot_new[1], rot_new[2], -rot_new[0]]
        
        self.rebuild_from_history(self.operation_history)
        self.record_op = False

    # Machine construction
    def _add_block(self, block: Block, return_summary=True, replacing=False):
        """Add a block to the machine and the collision manager"""
        self.blocks[str(self.uid)] = block
        
        self.add_collider(block)

        # Check for collision if more than one block
        collision_msg = self.collision_detect() if self.do_collision else None

        if collision_msg:
            # Failed to add new block
            self._remove_block(block.local_id)
            self.update_prompt(pre_msg=collision_msg)
        else:
            self.record_op = True
            if not replacing:
                # In case of in-place replacing (twist and shift), the uid should not be updated
                # Update counter if adding successes
                self.update_prompt(pre_msg=f"You have successfully added <ID {block.local_id}: {block.name}>.", 
                                return_summary=return_summary, 
                                complete=False)
                self.uid += 1
        
        return collision_msg
        
    @operation(placeholder=AvailableConnectors, group="build")
    def connect_blocks(self, block_a: Union[str, int], face_a: str, block_b: Union[str, int], face_b: str, connector: str, note: str = None):
        """
        Connect two blocks using a connector. 
        The connection will not be successful if the two faces are too close to each other.
        The face is labeled with capitalized letters, check the attachable face details using get_block_details if needed.
        
        Args:
            block_a (Union[str, int]): ID of the first block
            face_a (str): Face of the first block to connect from
            block_b (Union[str, int]): ID of the second block
            face_b (str): Face of the second block to connect to
            connector (str): Type of connector block to use, available types: {placeholder}
            note (str): Conceptual note or description about the connection
            
        Returns:
            str: Status message about the connection operation
        """
        if connector in AvailableBlocks:
            self.update_prompt(pre_msg="Basic blocks can not be used as connectors, please try again.")
            return self.prompt
        if not self.blocks_storage.has_available_connector(connector_name=connector):
            available_connectors = ", ".join(self.blocks_storage.available_connector_names())
            error_message = (
                f"Connector {connector} does not exist or is not available, "
                f"please choose one of: {available_connectors}."
            )
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("connect_blocks", error_message)
            return self.prompt
        if isinstance(block_a, int):
            block_a = str(block_a)
        if isinstance(block_b, int):
            block_b = str(block_b)
        if block_a in self.blocks.keys() and block_b in self.blocks.keys():
            block_a: Block = self.blocks.get(block_a)
            block_b: Block = self.blocks.get(block_b)
            
            # Check if the specified faces exist and attachable
            if face_a in block_a.faces.keys() and face_b in block_b.faces.keys():
                face_a: Face = block_a.faces.get(face_a)
                face_b: Face = block_b.faces.get(face_b)
                if np.linalg.norm(face_a.center.virtual - face_b.center.virtual) < 0.01:
                    error_message = "The two faces are too close to each other, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("connect_blocks", error_message)
                    return self.prompt
                else:
                    # Get the specified new block
                    local_id = str(self.uid)
                    connector: Connector = self.blocks_storage.get(block_name=connector, local_id=local_id, start_point=face_a, end_point=face_b, note=note)
                    collision_msg = self._add_block(block=connector)
                    if collision_msg:
                        error_message = collision_msg
                        self.update_prompt(pre_msg=error_message)
                        self.log_failed_operation("connect_blocks", error_message)
                        return self.prompt
            elif face_a not in block_a.faces.keys():
                if len(block_a.faces.keys()) == 0:
                    error_message = f"Block {block_a.local_id} {block_a.name} does not have any faces, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("connect_blocks", error_message)
                    return self.prompt
                else:
                    error_message = f"Block {block_a.local_id} {block_a.name} does not have face {face_a}, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("connect_blocks", error_message)
                    return self.prompt
            elif face_b not in block_b.faces.keys():
                if len(block_b.faces.keys()) == 0:
                    error_message = f"Block {block_b.local_id} {block_b.name} does not have any faces, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("connect_blocks", error_message)
                    return self.prompt
                else:
                    error_message = f"Block {block_b.local_id} {block_b.name} does not have face {face_b}, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("connect_blocks", error_message)
                    return self.prompt
        elif block_a not in self.blocks.keys():
            error_message = f"Block {block_a} not found, please try again."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("connect_blocks", error_message)
            return self.prompt
        elif block_b not in self.blocks.keys():
            error_message = f"Block {block_b} not found, please try again."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("connect_blocks", error_message)
            return self.prompt
        
        return self.prompt
    
    @operation(group="build")
    def remove_block(self, block_id: Union[str, int]):
        """
        Remove a block from the machine and the collision manager.
        
        Args:
            block_id (Union[str, int]): ID of the block to remove
            
        Returns:
            str: Status message about the removal operation
        """
        if isinstance(block_id, int):
            block_id = str(block_id)
        if block_id == '0':
            error_message = 'The Starting Block can not be removed'
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("remove_block", error_message)
            return self.prompt
        else:
            if block_id in self.blocks.keys():
                # Update machine prompt
                remove_msg = self._remove_block(block_id)
                self.update_prompt(pre_msg=remove_msg, return_summary=False)
                self.record_op = True
                return self.prompt
            else:
                error_message = f"Specified block {block_id} not found, please try again."
                self.update_prompt(pre_msg=error_message)
                self.log_failed_operation("remove_block", error_message)
                return self.prompt
            
    def _remove_block(self, block_id: int):
        """
        Remove a block from the machine and the collision manager.
        """
        # Remove block from blocks dict
        block = self.blocks.pop(block_id)
        # Remove collider from collision manager
        self.collision_manager.remove_object(block.local_id)
        # Set exposed face back to attachable
        if block.start_point is not None:
            block.start_point.sticky = True
            block.start_point.att_to = None
        for face in (face for face in block.faces.values() if face.att_to):
            face.att_to.sticky = True
            face.att_to.att_to = None
        # Update machine prompt
        remove_msg = f"You have successfully removed <ID {block.local_id}: {block.name}>."
        
        return remove_msg
    
    @operation(placeholder=AvailableBlocks, group="build")
    def attach_block_to(self, base_block: Union[str, int], face: str, new_block: str, note: str = None):
        """
        Attach a new block to a face of an existing block.
        The face is labeled with capitalized letters, check the attachable face details using get_block_details if needed.
        
        Args:
            base_block (Union[str, int]): ID of the existing block to attach to
            face (str): Face of the base block to attach to
            new_block (str): Type of block to attach, available types: {placeholder}
            note (str): Conceptual note or description about the new block
            
        Returns:
            str: Status message about the attachment operation
        """
        if not self.started:
            error_message = "The machine has not been started, please start the machine first."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("attach_block_to", error_message)
            return self.prompt  
        if new_block in AvailableConnectors:
            error_message = "Connectors can not be attached to a single face. Use 'connect_blocks' to connect two faces instead."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("attach_block_to", error_message)
            return self.prompt
        if new_block not in AvailableBlocks:
            error_message = f"Block {new_block} not available, please try again."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("attach_block_to", error_message)
            return self.prompt
        else:
            if isinstance(base_block, int):
                base_block = str(base_block)
            if base_block in self.blocks.keys():
                base_block: Block = self.blocks.get(base_block)
                
                # Check if the specified face exists and attachable
                if face in base_block.faces.keys() and base_block.faces.get(face).role == "joint":
                    error_message = f"Face {face} of base block {base_block.local_id} {base_block.name} is a joint face and can not attach a block, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("attach_block_to", error_message)
                elif face in base_block.faces.keys() and base_block.faces.get(face).sticky:
                    # Get the center position of the specified face
                    # bb: base block, nb: new block
                    bb_face = base_block.faces.get(face)

                    # Get the specified new block
                    local_id = str(self.uid)
                    new_block: Block = self.blocks_storage.get(new_block, local_id, start_point=bb_face, note=note)

                    # Delete occupied face
                    bb_face.sticky = False
                    
                    for nb_face in new_block.faces.values():
                        if face_is_attached(bb_face, nb_face):
                            nb_face.sticky = False
                            nb_face.att_to = bb_face
                            bb_face.att_to = nb_face
                    
                    collision_msg = self._add_block(new_block)
                    if collision_msg:
                        error_message = collision_msg
                        self.log_failed_operation("attach_block_to", error_message)
                elif face not in base_block.faces.keys():
                    error_message = f"Base block {base_block.local_id} {base_block.name} does not have face {face}, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("attach_block_to", error_message)
                else:
                    error_message = f"Face {face} of base block {base_block.local_id} {base_block.name} is already occupied, please try again."
                    self.update_prompt(pre_msg=error_message)
                    self.log_failed_operation("attach_block_to", error_message)
            else:
                error_message = f"Base block {base_block} not found, please try again."
                self.update_prompt(pre_msg=error_message)
                self.log_failed_operation("attach_block_to", error_message)
        
        return self.prompt
    
    def refresh_block(self, block: Block):
        """Remove the original block and add the new block after twisting or rotating"""
        # Save the original block in case the operation does not pass the collision detection
        original_block = self.blocks.pop(block.local_id)
        self.blocks[block.local_id] = block
        self.refresh_colliders()
        collision_msg = self.collision_detect(target_block_id=block.local_id) if self.do_collision else None
        if collision_msg:
            _ = self.blocks.pop(block.local_id)
            self.blocks[original_block.local_id] = original_block
            self.refresh_colliders()
            return collision_msg
        else:
            return None

    @operation(group="refine")
    def twist_block(self, block_id: Union[str, int], angle: float):
        """
        Twist a block clockwise relative to its rooted surface, angles in degrees.
        Especially useful for changing the direction of the pointer block.
        For example, if the pointer block is attached to a vertical face and points upwards, twisting it 180 degrees will make it point downwards.
        For example, if the pointer block is attached to a horizontal top face and points towards the north, twisting it 90 degrees will make it point towards the east.
        Try with multiple twists to get the desired direction.
        
        Args:
            block_id (Union[str, int]): ID of the block to twist
            angle (float): Angle in degrees to twist the block by
            
        Returns:
            str: Status message about the twist operation
        """
        if isinstance(block_id, int):
            block_id = str(block_id)
        if block_id in self.blocks.keys():
            block = self.blocks.get(block_id)
            block.twist(angle)
            collision_msg = self.refresh_block(block)
            if collision_msg:
                error_message = collision_msg
                self.update_prompt(pre_msg=error_message)
                self.log_failed_operation("twist_block", error_message)
                block.twist(angle * -1)
                self.refresh_colliders()
                return self.prompt
            self.update_prompt(pre_msg=f'The block {block_id} <{block.name}> is twisted by {angle} degrees. \n {block.caption(finished=False)}')
            self.record_op = True
        else:
            error_message = f"Specified block {block_id} not found, please try again."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("twist_block", error_message)
        return self.prompt

    @operation(group="refine")
    def shift_block(self, block_id: Union[str, int], shift_real: List):
        """
        Shift a block by a specified offset. 
        Particularly useful for adjusting the position of a block after it is attached, when another attachment attempt is failed due to overlap.
        
        Args:
            shift_real (List[float]): Offset vector [x, y, z] in the 3D space,each offset should be in the range of [-0.5, 0.5], too much offset will cause the block to be detached from its base block.
            
        Returns:
            None
        """
        if isinstance(block_id, int):
            block_id = str(block_id)
        if block_id in self.blocks.keys():
            block = self.blocks.get(block_id)
            block.shift(shift_real)
            collision_msg = self.refresh_block(block)
            if collision_msg:
                error_message = collision_msg
                self.update_prompt(pre_msg=error_message)
                self.log_failed_operation("shift_block", error_message)
                block.shift([shift * -1 for shift in shift_real])
                self.refresh_colliders()
                return self.prompt
            self.update_prompt(pre_msg=f'The block {block_id} <{block.name}> is shifted by {shift_real}. \n {block.caption(finished=False)}')
            self.record_op = True
        else:
            error_message = f"Specified block {block_id} not found, please try again."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("shift_block", error_message)
        return self.prompt
    
    @operation(group="refine")
    def flip_spin(self, block_id: Union[str, int]):
        """
        Flip the spin direction of a block. The flip operation will not be successful if the block does not spin.
        
        Args:
            block_id (Union[str, int]): ID of the block to flip
            
        Returns:
            str: Status message about the flip operation
        """
        if isinstance(block_id, int):
            block_id = str(block_id)
        if block_id in self.blocks.keys():
            block = self.blocks.get(block_id)
            if block.spin:
                block.flipped = not block.flipped 
                self.update_prompt(pre_msg=f'The block {block_id} <{block.name}> is flipped. \n {block.caption(finished=False)}')
                self.record_op = True
            else: 
                error_message = f'The block {block_id} <{block.name}> does not spin, please try again.'
                self.update_prompt(pre_msg=error_message)
                self.log_failed_operation("flip_spin", error_message)
        else: 
            error_message = f"Specified block {block_id} not found, please try again."
            self.update_prompt(pre_msg=error_message)
            self.log_failed_operation("flip_spin", error_message)
            
        return self.prompt
    
    # Collision detection
    def in_collision(self):
        is_collision, collision_pairs = self.collision_manager.in_collision_internal(return_names=True)
        return is_collision, collision_pairs

    def _collision_pair_signed_distance(self, *, block_id_a: str, block_id_b: str) -> float:
        block_a = self.blocks.get(block_id_a)
        block_b = self.blocks.get(block_id_b)
        if block_a is None:
            raise ValueError(f"Block {block_id_a} not found for collision distance check.")
        if block_b is None:
            raise ValueError(f"Block {block_id_b} not found for collision distance check.")

        pair_manager = CollisionManager()
        pair_manager.add_object(block_id_a, block_a.collider)
        pair_manager.add_object(block_id_b, block_b.collider)
        distance, _pair = pair_manager.min_distance_internal(return_names=True)
        return float(distance)

    def _blocks_are_directly_attached(self, *, block_id_a: str, block_id_b: str) -> bool:
        block_a = self.blocks.get(block_id_a)
        block_b = self.blocks.get(block_id_b)
        if block_a is None:
            raise ValueError(f"Block {block_id_a} not found for attachment collision check.")
        if block_b is None:
            raise ValueError(f"Block {block_id_b} not found for attachment collision check.")

        if block_a.start_point is not None and str(block_a.start_point.local_id) == str(block_b.local_id):
            return True
        if block_b.start_point is not None and str(block_b.start_point.local_id) == str(block_a.local_id):
            return True
        return False

    def _significant_collision_pairs(
        self,
        *,
        collision_pairs: set[tuple[str, str]],
    ) -> list[tuple[str, str, float]]:
        significant_pairs = []
        for block_id_a, block_id_b in collision_pairs:
            if self._blocks_are_directly_attached(
                block_id_a=block_id_a,
                block_id_b=block_id_b,
            ):
                continue
            distance = self._collision_pair_signed_distance(
                block_id_a=block_id_a,
                block_id_b=block_id_b,
            )
            significant_pairs.append((block_id_a, block_id_b, distance))
        return significant_pairs
    
    def collision_detect(self, target_block_id = False):
        is_collision, collision_pairs = self.in_collision()
        
        if is_collision:
            significant_pairs = self._significant_collision_pairs(
                collision_pairs=collision_pairs,
            )
            if len(significant_pairs) == 0:
                return None

            block = self.blocks.get(target_block_id) if target_block_id else self.blocks.get(str(self.uid))
            if target_block_id:
                block_label = f"block {block.local_id} <{block.name}>"
            else:
                block_label = f"new block <{self.blocks[str(self.uid)].name}>"
            collision_msg = [f"Operation failed! The {block_label} overlaps with existing blocks and it's been restored to previous state already, please try again"]
            for i, j, distance in significant_pairs:
                collision_msg.append(f"Overlapping detected between <ID {self.blocks[i].local_id}: {self.blocks[i].name}> and <ID {self.blocks[j].local_id}: {self.blocks[j].name}> (signed distance {distance:.4f})")
            
            return '\n'.join(collision_msg)
        else:
            return None
    
    # Machine visualization
    def outline_mesh(self):
        return trimesh.util.concatenate([block.outline for block in self.blocks.values()])

    def _configure_tracker(self, *, sample_rate_hz: float=10.0, target_block_id: str | list[str]):
        """
        Configure the BlockTracker mod through machine-level .bsg data.

        Args:
            sample_rate_hz (float): Runtime trajectory sample rate in Hz.
            target_block_id (str | list[str]): Local block id or ids in this Machine; their BSG GUIDs are written to tracker.target_guids.
        """
        if sample_rate_hz <= 0:
            error_message = "Tracker sample_rate_hz must be greater than zero."
            raise ValueError(error_message)

        if isinstance(target_block_id, str):
            target_block_ids = [target_block_id]
        elif isinstance(target_block_id, list) and all(isinstance(item, str) for item in target_block_id):
            target_block_ids = target_block_id
        else:
            error_message = "Tracker target_block_id must be a string or a list of strings."
            raise ValueError(error_message)

        if not target_block_ids:
            error_message = "Tracker target_block_id list cannot be empty."
            raise ValueError(error_message)

        missing_ids = [block_id for block_id in target_block_ids if block_id not in self.blocks]
        if missing_ids:
            error_message = f"Tracker target block ids are not in the machine: {', '.join(missing_ids)}."
            raise ValueError(error_message)

        target_blocks = [self.blocks[block_id] for block_id in target_block_ids]
        self.tracker_config = {
            "sample_rate_hz": float(sample_rate_hz),
            "target_block_ids": target_block_ids,
            "target_guids": [block.guid for block in target_blocks],
            "output_basename": self.name,
        }

    def _configure_starting_block_tracker(self) -> None:
        starting_block_ids = [
            block_id
            for block_id, block in self.blocks.items()
            if block.name == "Starting Block"
        ]
        if len(starting_block_ids) == 0:
            raise ValueError("Expected at least one Starting Block before export, found 0.")
        target_block_id = starting_block_ids[0] if len(starting_block_ids) == 1 else starting_block_ids
        self._configure_tracker(
            sample_rate_hz=10.0,
            target_block_id=target_block_id,
        )

    # Machine export
    def to_xml(self, shift_virtual: List[float] = [0, 0, 0], rotation: List[float] = [0, 0, 0, 1]):
        from .xml_builder import BsgDocument, XmlNode, machine_node, position_node, rotation_node

        self._configure_starting_block_tracker()

        root = machine_node(name=self.name)

        # Global
        global_node = XmlNode(tag="Global")
        global_node.add(position_node(shift_virtual[0], 5 - shift_virtual[1], shift_virtual[2]))
        global_node.add(rotation_node(rotation[0], rotation[1], rotation[2], rotation[3]))
        root.add(global_node)

        # Machine Data (Tracker Data)
        machine_data = XmlNode(tag="Data")
        required_mods = ""
        if self.tracker_config is not None:
            required_mods = besiege_required_mod_entry(
                mod_id=BLOCK_TRACKER_MOD_ID,
                version=BLOCK_TRACKER_MOD_VERSION,
                name=BLOCK_TRACKER_MOD_NAME,
            )
        machine_data.add(XmlNode(tag="StringArray", attrs={"key": "requiredMods"}, text=required_mods))
        if self.tracker_config is not None:
            machine_data.add(XmlNode(tag="Boolean", attrs={"key": "tracker.enabled"}, text="True"))
            machine_data.add(XmlNode(tag="Single", attrs={"key": "tracker.sample_rate_hz"}, text=str(self.tracker_config["sample_rate_hz"])))
            machine_data.add(XmlNode(tag="String", attrs={"key": "tracker.target_guids"}, text=";".join(self.tracker_config["target_guids"])))
            machine_data.add(XmlNode(tag="String", attrs={"key": "tracker.output_basename"}, text=self.tracker_config["output_basename"]))
        root.add(machine_data)

        # Blocks
        blocks_node = XmlNode(tag="Blocks")
        first_key = next(k for k, b in self.blocks.items() if b.name == "Starting Block")
        first_block = self.blocks.pop(first_key)
        blocks_node.add(first_block.to_xml_node())
        for block in self.blocks.values():
            blocks_node.add(block.to_xml_node())
        root.add(blocks_node)
        self.blocks[first_key] = first_block

        return BsgDocument(root=root).render()
    
    # Save and load machine
    def _write_machine_files(self, *, output_dir: Path, bsg_data: str) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        bsg_file_path = output_dir / f"{self.name}.bsg"
        output_sequence_path = output_dir / f"{self.name}.json"
        with open(bsg_file_path, 'w', encoding='utf-8') as file:
            file.write(bsg_data)
        self.save_operation_history(file_path=str(output_sequence_path))
        return bsg_file_path, output_sequence_path

    def _local_machine_mirror_dir(self) -> Path:
        return resolve_project_path(path=Path(".local") / "Machine" / self.name)

    def to_file(self, output_dir, shift_virtual: List[float] = [0, 0, 0], rotation: List[float] = [0, 0, 0, 1]):
        """
        Save the machine .bsg and operation history JSON files into output_dir.
        Named with the machine name.
        
        Args:
            output_dir (str): Directory to save the machine files to.
        """
        bsg_data = self.to_xml(shift_virtual=shift_virtual, rotation=rotation)
        primary_output_dir = Path(output_dir)
        bsg_file_path, output_sequence_path = self._write_machine_files(
            output_dir=primary_output_dir,
            bsg_data=bsg_data,
        )
        self.output_sequence_path = str(output_sequence_path)

        local_mirror_dir = self._local_machine_mirror_dir()
        mirror_written = False
        if local_mirror_dir.resolve() != primary_output_dir.resolve():
            self._write_machine_files(output_dir=local_mirror_dir, bsg_data=bsg_data)
            mirror_written = True

        if mirror_written:
            print(f'machine saved as {bsg_file_path}, mirrored to {local_mirror_dir / f"{self.name}.bsg"}')
        else:
            print(f'machine saved as {bsg_file_path}')
    
    def from_file(self, file_path):
        self.output_sequence_path = Path(file_path)
        # 4 levels up
        operation_history = self.load_operation_history(self.output_sequence_path)
        self.rebuild_from_history(operation_history)
        return self
        
    # Save and load operation sequence
    def save_operation_history(self, file_path):
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.operation_history, f, indent=2)

    def load_operation_history(self, file_path) -> List[Dict[str, Any]]:
        with open(file_path, 'r', encoding='utf-8') as f:
            operation_history = json.load(f)
        return operation_history
    
    # Rebuild the machine according to the history
    def rebuild_from_history(self, operation_history=None):
        # Use current history if new one provided
        if operation_history is None:
            operation_history = self.operation_history
        
        # Reset the machine
        self.reset()
        # Execute each operation in sequence
        for op in operation_history:
            if op["op"] in self.operations:
                self.operations[op["op"]](**op["params"])
    
    # Save and load full operation history
    def init_full_op_history(self):
        if not os.path.exists(self.full_op_history_path):
            self.operation_history_full = []
        else:
            self.operation_history_full = json.load(open(self.full_op_history_path, 'r', encoding='utf-8'))
    
    def save_full_op_history(self):
        if not self.write_full_history:
            return
        os.makedirs(os.path.dirname(self.full_op_history_path), exist_ok=True)
        with open(self.full_op_history_path, 'w', encoding='utf-8') as f:
            json.dump(self.operation_history_full, f, indent=2)
