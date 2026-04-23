from pathlib import Path
from core import bone_animations
import shlex


def _strip_prefix(bone_name: str) -> str:
    return bone_name.split('.')[-1]


def generate_lookat_vrd(target_bone: str, attachment_name: str, frame_index: int, aimvector: tuple,
                        upvector: tuple, helper_bones: list[str], pose_path: str,
                        pose_dir: Path, vrd_dir: Path, vrd_name: str,
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

    if frame_index >= len(euler_frames):
        frame_index = len(euler_frames) - 1

    hierarchy = {bt.bone_name.lower(): bt for bt in euler_frames[0]}
    frame_map = {bt.bone_name.lower(): bt for bt in euler_frames[frame_index]}
    av = " ".join(f"{v:.6g}" for v in aimvector)
    uv = " ".join(f"{v:.6g}" for v in upvector)

    vrd_lines = []

    for helper_bone in helper_bones:
        h_key      = helper_bone.lower()
        helper_ref = hierarchy.get(h_key)

        if not helper_ref:
            if logger: logger.error(f"Helper '{helper_bone}' not found, skipping")
            continue

        hp    = helper_ref.parent_name if helper_ref.parent_name else helper_bone
        h_bt  = frame_map.get(h_key)
        h_loc = h_bt.location if h_bt else (0.0, 0.0, 0.0)
        hl    = " ".join(f"{v:.9f}" for v in h_loc)

        vrd_lines.append(f"<aimconstraint>\t{_strip_prefix(helper_bone)}\t\t{_strip_prefix(hp)}\t{attachment_name}")
        vrd_lines.append(f"<basepos>       {hl}")
        vrd_lines.append(f"<aimvector>\t\t{av}")
        vrd_lines.append(f"<upvector>\t\t{uv}")
        vrd_lines.append("")

    out_dir  = vrd_dir / "vrds"
    out_dir.mkdir(exist_ok=True)
    vrd_path = out_dir / f"{vrd_name}.vrd"
    vrd_path.write_text("\n".join(vrd_lines), encoding="utf-8")

    if logger: logger.info(f"(VRD generated): {vrd_path.name}")
    return vrd_path


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
    d_key     = driver_bone.lower()
    driver_ref = hierarchy.get(d_key)

    if not driver_ref:
        if logger: logger.error(f"Driver '{driver_bone}' not found in {pose_path}")

    vrd_lines = []

    for helper_bone in target_bones:
        h_key      = helper_bone.lower()
        helper_ref = hierarchy.get(h_key)

        if not helper_ref:
            if logger: logger.error(f"Helper '{helper_bone}' not found, skipping")
            continue

        hp = helper_ref.parent_name if helper_ref.parent_name else helper_bone
        dp = (driver_ref.parent_name if driver_ref else None) or driver_bone

        vrd_lines.append(
            f"<helper> {_strip_prefix(helper_bone)} {_strip_prefix(hp)} "
            f"{_strip_prefix(dp)} {_strip_prefix(driver_bone)}"
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

    out_dir  = vrd_dir / "vrds"
    out_dir.mkdir(exist_ok=True)
    vrd_path = out_dir / f"{vrd_name}.vrd"
    vrd_path.write_text("\n".join(vrd_lines), encoding="utf-8")

    if logger: logger.info(f"(VRD generated): {vrd_path.name}")
    return vrd_path