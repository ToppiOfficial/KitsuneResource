from libs import datamodel
import re
from math import cos, sin, atan2, asin, degrees, radians
from typing import NamedTuple


class BoneTransform(NamedTuple):
    bone_name: str
    parent_name: str | None
    location: tuple[float, float, float]
    rotation: tuple  # (x, y, z, w) quaternion  or  (x, y, z) euler


BoneFrameData = list[list[BoneTransform]]


def _euler_to_quat(rx: float, ry: float, rz: float) -> tuple[float, float, float, float]:
    cx, cy, cz = cos(rx/2), cos(ry/2), cos(rz/2)
    sx, sy, sz = sin(rx/2), sin(ry/2), sin(rz/2)
    return (
        sx*cy*cz - cx*sy*sz,
        cx*sy*cz + sx*cy*sz,
        cx*cy*sz - sx*sy*cz,
        cx*cy*cz + sx*sy*sz,
    )


def _quat_to_euler(x: float, y: float, z: float, w: float) -> tuple[float, float, float]:
    rx = atan2(2*(w*x + y*z), 1 - 2*(x*x + y*y))
    ry = asin(max(-1.0, min(1.0, 2*(w*y - z*x))))
    rz = atan2(2*(w*z + x*y), 1 - 2*(y*y + z*z))
    return (rx, ry, rz)


def _quat_multiply(a: tuple, b: tuple) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
        aw*bw - ax*bx - ay*by - az*bz,
    )


def _quat_inverse(q: tuple) -> tuple[float, float, float, float]:
    x, y, z, w = q
    return (-x, -y, -z, w)


# Conversion Utilities

def frames_quat_to_euler(frames: BoneFrameData) -> BoneFrameData:
    return [
        [BoneTransform(bt.bone_name, bt.parent_name, bt.location, _quat_to_euler(*bt.rotation))
         for bt in frame]
        for frame in frames
    ]


def frames_euler_to_quat(frames: BoneFrameData) -> BoneFrameData:
    return [
        [BoneTransform(bt.bone_name, bt.parent_name, bt.location, _euler_to_quat(*bt.rotation))
         for bt in frame]
        for frame in frames
    ]


def frames_rotation_to_degrees(frames: BoneFrameData) -> BoneFrameData:
    return [
        [BoneTransform(bt.bone_name, bt.parent_name, bt.location, tuple(degrees(v) for v in bt.rotation))
         for bt in frame]
        for frame in frames
    ]


def frames_rotation_to_radians(frames: BoneFrameData) -> BoneFrameData:
    return [
        [BoneTransform(bt.bone_name, bt.parent_name, bt.location, tuple(radians(v) for v in bt.rotation))
         for bt in frame]
        for frame in frames
    ]


def apply_world_scale(frames: BoneFrameData, scale: float) -> BoneFrameData:
    return [
        [BoneTransform(bt.bone_name, bt.parent_name, (bt.location[0] * scale, bt.location[1] * scale, bt.location[2] * scale), bt.rotation)
         for bt in frame]
        for frame in frames
    ]


def to_offset_frames(frames: BoneFrameData, rest_frame: int = 0) -> BoneFrameData:
    """
    Returns frames where every frame's location and rotation is expressed as an
    offset relative to rest_frame. The rest frame itself will contain zero offsets.
    Quaternion rotations use q_inverse(rest) * current. Euler rotations use simple subtraction.
    """
    if not frames or rest_frame >= len(frames):
        return frames

    rest_by_bone = {bt.bone_name: bt for bt in frames[rest_frame]}
    result: BoneFrameData = []

    for frame_index, frame in enumerate(frames):
        if frame_index == rest_frame:
            result.append(list(frame))
            continue

        offset_frame: list[BoneTransform] = []
        for bt in frame:
            rest = rest_by_bone.get(bt.bone_name)
            if rest is None:
                offset_frame.append(bt)
                continue

            loc_offset = (
                bt.location[0] - rest.location[0],
                bt.location[1] - rest.location[1],
                bt.location[2] - rest.location[2],
            )

            if len(bt.rotation) == 3:
                rot_offset = (
                    bt.rotation[0] - rest.rotation[0],
                    bt.rotation[1] - rest.rotation[1],
                    bt.rotation[2] - rest.rotation[2],
                )
            else:
                rot_offset = _quat_multiply(_quat_inverse(rest.rotation), bt.rotation)

            offset_frame.append(BoneTransform(bt.bone_name, bt.parent_name, loc_offset, rot_offset))

        result.append(offset_frame)

    return result


def read_smd_bone_animation(filepath: str, target_bone: str | None = None) -> BoneFrameData:
    bones: dict[int, tuple[str, int]] = {}

    with open(filepath, 'r') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if line == 'nodes':
            while i < len(lines):
                line = lines[i].strip()
                i += 1
                if line == 'end':
                    break
                parts = re.split(r'\s+', line, maxsplit=2)
                bone_id = int(parts[0])
                bone_name = parts[1].strip('"')
                parent_id = int(parts[2])
                bones[bone_id] = (bone_name, parent_id)

        elif line == 'skeleton':
            frames: BoneFrameData = []
            current_frame: list[BoneTransform] = []

            while i < len(lines):
                line = lines[i].strip()
                i += 1
                if line == 'end':
                    if current_frame:
                        frames.append(current_frame)
                    break
                if line.startswith('time'):
                    if current_frame:
                        frames.append(current_frame)
                    current_frame = []
                    continue

                parts = line.split()
                bone_id = int(parts[0])
                if bone_id not in bones:
                    continue

                bone_name, parent_id = bones[bone_id]
                if target_bone is not None and bone_name != target_bone:
                    continue

                parent_name = bones[parent_id][0] if parent_id != -1 and parent_id in bones else None
                loc = (float(parts[1]), float(parts[2]), float(parts[3]))
                rotation = (float(parts[4]), float(parts[5]), float(parts[6]))  # raw euler radians
                current_frame.append(BoneTransform(bone_name, parent_name, loc, rotation))

            return frames

    return []


def read_dmx_bone_animation(filepath: str, target_bone: str | None = None) -> BoneFrameData:
    dm = datamodel.load(filepath)

    if not dm.root.get("animationList"):
        parent_map: dict[str, str | None] = {}
        base_transforms: dict[str, tuple] = {}

        def _walk_joints(joint, parent_name):
            for child in joint.get("children", []):
                if child.type in ("DmeJoint", "DmeDag") or "Joint" in child.type:
                    parent_map[child.name] = parent_name
                    trfm = child.get("transform")
                    if trfm is not None:
                        pos = trfm.get("position", (0.0, 0.0, 0.0))
                        ori = trfm.get("orientation", (0.0, 0.0, 0.0, 1.0))
                        base_transforms[child.name] = (
                            (pos[0], pos[1], pos[2]),
                            (ori[0], ori[1], ori[2], ori[3]),
                        )
                    _walk_joints(child, child.name)

        DmeModel = dm.root.get("model") or dm.root.get("skeleton")
        if not DmeModel:
            return []

        # Check for baseStates first, fall back to live transforms
        base_states = DmeModel.get("baseStates")
        if base_states and len(base_states) > 0:
            for trfm in base_states[0].get("transforms", []):
                base_transforms[trfm.name] = (
                    tuple(trfm.get("position", (0.0, 0.0, 0.0)))[:3],
                    tuple(trfm.get("orientation", (0.0, 0.0, 0.0, 1.0)))[:4],
                )
        else:
            for root_joint in DmeModel.get("children", []):
                parent_map[root_joint.name] = None
                _walk_joints(root_joint, root_joint.name)

        frame: list[BoneTransform] = []
        for bone_name, (pos, ori) in base_transforms.items():
            if target_bone is not None and bone_name != target_bone:
                continue
            parent_name = parent_map.get(bone_name)
            frame.append(BoneTransform(bone_name, parent_name, pos, ori))

        return [frame]

    animation = dm.root["animationList"]["animations"][0]
    frame_rate = animation.get("frameRate", 30)
    time_frame = animation["timeFrame"]
    start = time_frame.get("start", 0)
    duration = time_frame.get("duration") or time_frame.get("durationTime", 0.0)

    if isinstance(duration, int):
        duration = datamodel.Time.from_int(duration)

    total_frames = max(1, round(float(duration) * frame_rate) + 1)

    parent_map: dict[str, str | None] = {}           # bone_name -> parent_name
    transform_id_map: dict[any, str] = {}             # DmeTransform.id -> bone_name

    def _walk_joints(joint, parent_name):
        for child in joint.get("children", []):
            if child.type in ("DmeJoint", "DmeDag") or "Joint" in child.type:
                parent_map[child.name] = parent_name
                trfm = child.get("transform")
                if trfm is not None:
                    transform_id_map[trfm.id] = child.name
                _walk_joints(child, child.name)

    DmeModel = dm.root.get("model") or dm.root.get("skeleton")
    if DmeModel:
        for root_joint in DmeModel.get("children", []):
            parent_map[root_joint.name] = None
            trfm = root_joint.get("transform")
            if trfm is not None:
                transform_id_map[trfm.id] = root_joint.name
            _walk_joints(root_joint, root_joint.name)

    bone_channels: dict[str, dict[str, dict[int, any]]] = {}

    for channel in animation["channels"]:
        to_element = channel.get("toElement")
        if not to_element:
            continue

        # resolve bone name: try transform id map first, fall back to element name
        bone_name = transform_id_map.get(to_element.id) or to_element.name
        if not bone_name:
            continue

        attr = channel.get("toAttribute")
        if attr not in ("position", "orientation"):
            continue
        if target_bone is not None and bone_name != target_bone:
            continue

        frame_log = channel["log"]["layers"][0]
        times = frame_log["times"]
        values = frame_log["values"]

        if bone_name not in bone_channels:
            bone_channels[bone_name] = {"position": {}, "orientation": {}, "parent": parent_map.get(bone_name)}

        for idx in range(len(times)):
            t = times[idx]
            if isinstance(t, int):
                t = datamodel.Time.from_int(t)
            frame_index = int(round(float(t) * frame_rate))
            bone_channels[bone_name][attr][frame_index] = values[idx]

    frames: BoneFrameData = [[] for _ in range(total_frames)]

    for bone_name, data in bone_channels.items():
        parent_name = data["parent"]
        positions = data["position"]
        orientations = data["orientation"]

        last_pos = (0.0, 0.0, 0.0)
        last_quat = (0.0, 0.0, 0.0, 1.0)

        for frame_index in range(total_frames):
            if frame_index in positions:
                p = positions[frame_index]
                last_pos = (p[0], p[1], p[2])
            if frame_index in orientations:
                q = orientations[frame_index]
                last_quat = (q[0], q[1], q[2], q[3])

            frames[frame_index].append(BoneTransform(bone_name, parent_name, last_pos, last_quat))

    return frames

