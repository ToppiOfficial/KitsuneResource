from pathlib import Path
from libs import bone_animations

import shlex


def _parse_driverbone_block(lines: list[str], start: int) -> tuple[dict | None, int]:
    result = {"pose": None, "triggers": [], "target_bones": []}
    i = start - 1

    while i < len(lines) and "{" not in lines[i]:
        i += 1
    if i >= len(lines):
        return None, i

    i += 1  # skip '{' line

    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if line == "}":
            break
        if not line or line.startswith("//"):
            continue

        tokens = shlex.split(line)
        if not tokens:
            continue

        if tokens[0].lower() == "pose":
            result["pose"] = tokens[1]
            continue

        # Process tokens left-to-right; 'trigger' keyword is just a separator
        j = 0
        while j < len(tokens):
            tok = tokens[j]
            if tok.lower() == "trigger":
                j += 1
                continue
            # Try to parse as a trigger pair: float int
            if j + 1 < len(tokens):
                try:
                    angle = float(tok)
                    frame = int(tokens[j + 1])
                    result["triggers"].append((angle, frame))
                    j += 2
                    continue
                except ValueError:
                    pass
            # Otherwise treat as a bone name
            result["target_bones"].append(tok.strip('"'))
            j += 1

    return result, i

def generate_vrd(driver_bone: str, pose_path: str, triggers: list[tuple[float, int]],
                 target_bones: list[str], pose_dir: Path, vrd_dir: Path, vrd_name: str,
                 scale: float = 1.0, logger=None) -> Path:

    pose_file = (pose_dir / pose_path).resolve()

    ext = pose_file.suffix.lower()
    if not ext:
        for candidate_ext in (".dmx", ".smd"):
            candidate = pose_file.with_suffix(candidate_ext)
            if candidate.exists():
                pose_file = candidate
                ext = candidate_ext
                break

    if ext == ".smd":
        euler_frames = bone_animations.frames_rotation_to_degrees(
            bone_animations.read_smd_bone_animation(str(pose_file))
        )
    elif ext == ".dmx":
        euler_frames = bone_animations.frames_rotation_to_degrees(
            bone_animations.frames_quat_to_euler(
                bone_animations.read_dmx_bone_animation(str(pose_file))
            )
        )
    else:
        raise ValueError(f"Unsupported format: {ext}")

    if scale != 1.0:
        euler_frames = bone_animations.apply_world_scale(euler_frames, scale)

    hierarchy = {bt.bone_name.lower(): bt for bt in euler_frames[0]}
    d_key = driver_bone.lower()
    driver_ref = hierarchy.get(d_key)

    if not driver_ref:
        if logger: logger.error(f"Driver '{driver_bone}' not found in {pose_path}")

    def strip_prefix(bone_name):
        return bone_name.split('.')[-1]

    vrd_lines = []

    for helper_bone in target_bones:
        h_key = helper_bone.lower()
        helper_ref = hierarchy.get(h_key)

        if not helper_ref:
            if logger: logger.warning(f"Helper '{helper_bone}' not found, skipping")
            continue

        # hp: Helper Parent | dp: Driver Parent
        # We MUST pull dp from the driver_ref to ensure it matches the side
        hp = helper_ref.parent_name if helper_ref.parent_name else helper_bone
        dp = (driver_ref.parent_name if driver_ref else None) or driver_bone

        vrd_lines.append(
            f"<helper> {strip_prefix(helper_bone)} {strip_prefix(hp)} "
            f"{strip_prefix(dp)} {strip_prefix(driver_bone)}"
        )
        vrd_lines.append("<basepos> 0 0 0")

        for angle_of_influence, frame_index in triggers:
            if frame_index >= len(euler_frames):
                frame_index = len(euler_frames) - 1

            frame_map = {bt.bone_name.lower(): bt for bt in euler_frames[frame_index]}
            d_bt = frame_map.get(d_key)
            h_bt = frame_map.get(h_key)

            d_rot = d_bt.rotation if d_bt else (0.0, 0.0, 0.0)
            h_rot = h_bt.rotation if h_bt else (0.0, 0.0, 0.0)
            h_loc = h_bt.location if h_bt else (0.0, 0.0, 0.0)

            dr = " ".join(f"{v:.6g}" for v in d_rot)
            hr = " ".join(f"{v:.6g}" for v in h_rot)
            hl = " ".join(f"{v:.6g}" for v in h_loc)
            vrd_lines.append(f"<trigger> {angle_of_influence} {dr} \t{hr} \t{hl}")

        vrd_lines.append("")

    out_dir = vrd_dir / "vrds"
    out_dir.mkdir(exist_ok=True)
    vrd_path = out_dir / f"{vrd_name}.vrd"
    vrd_path.write_text("\n".join(vrd_lines), encoding="utf-8")

    if logger: logger.info(f"(VRD generated): {vrd_path.name}")
    return vrd_path