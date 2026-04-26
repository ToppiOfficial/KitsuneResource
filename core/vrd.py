from pathlib import Path
from core import bone_animations
import shlex


def _strip_prefix(bone_name: str) -> str:
    return bone_name.split('.')[-1]


def _load_euler_frames(filepath: Path, scale: float) -> bone_animations.BoneFrameData:
    """Load a DMX or SMD file and return euler-degree frames, with scale applied."""
    ext = filepath.suffix.lower()
    if not ext:
        for candidate_ext in (".dmx", ".smd"):
            candidate = filepath.with_suffix(candidate_ext)
            if candidate.exists():
                filepath = candidate
                ext = candidate_ext
                break

    if ext == ".smd":
        frames = bone_animations.frames_rotation_to_degrees(
            bone_animations.read_smd_bone_animation(str(filepath))
        )
    elif ext == ".dmx":
        frames = bone_animations.frames_rotation_to_degrees(
            bone_animations.frames_quat_to_euler(
                bone_animations.read_dmx_bone_animation(str(filepath))
            )
        )
    else:
        raise ValueError(f"Unsupported format: {ext}")

    if scale != 1.0:
        frames = bone_animations.apply_world_scale(frames, scale)

    return frames


def _resolve_pose_file(pose_dir: Path, pose_path: str) -> Path:
    p = (pose_dir / pose_path).resolve()
    if p.suffix.lower() or p.exists():
        return p
    for ext in (".dmx", ".smd"):
        candidate = p.with_suffix(ext)
        if candidate.exists():
            return candidate
    return p


def generate_lookat_vrd(target_bone: str, attachment_name: str, frame_index: int, aimvector: tuple,
                        upvector: tuple, helper_bones: list[str], pose_path: str,
                        pose_dir: Path, vrd_dir: Path, vrd_name: str,
                        scale: float = 1.0, logger=None) -> Path:

    pose_file    = _resolve_pose_file(pose_dir, pose_path)
    euler_frames = _load_euler_frames(pose_file, scale)

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
                 scale: float = 1.0, logger=None,
                 restpose_path: str | None = None, restpose_frame: int = 0) -> Path:

    pose_file    = _resolve_pose_file(pose_dir, pose_path)
    euler_frames = _load_euler_frames(pose_file, scale)

    # ------------------------------------------------------------------
    # Retargeting: map pose deltas onto a different rest skeleton.
    #
    # For each trigger frame:
    #   delta     = pose[trigger] - pose[frame_0]   (animation offset)
    #   result    = restpose[restpose_frame] + delta
    #
    # This lets a pose file authored on a different proportioned skeleton
    # (longer/shorter limbs) drive procedural bones using the restpose
    # matrices, so the VRD reflects the actual in-game rest skeleton.
    # ------------------------------------------------------------------
    rest_map       = None   # restpose bone transforms, keyed by bone_name.lower()
    pose_rest_map  = None   # pose frame-0 transforms, for delta computation

    if restpose_path is not None:
        rp_file = _resolve_pose_file(pose_dir, restpose_path)
        try:
            rp_frames = _load_euler_frames(rp_file, scale)
        except Exception as e:
            raise ValueError(f"Failed to load restpose '{restpose_path}': {e}")

        ri        = min(restpose_frame, len(rp_frames) - 1)
        rest_map  = {bt.bone_name.lower(): bt for bt in rp_frames[ri]}
        pose_rest_map = {bt.bone_name.lower(): bt for bt in euler_frames[0]}

        if logger:
            logger.info(f"(VRD retarget): restpose '{rp_file.name}' frame {ri}")

    hierarchy  = {bt.bone_name.lower(): bt for bt in euler_frames[0]}
    d_key      = driver_bone.lower()
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

            if rest_map is not None:
                # --- retargeted path ---
                def _retarget(key):
                    trigger_bt = frame_map.get(key)
                    pose_rest  = pose_rest_map.get(key)
                    rp_base    = rest_map.get(key)

                    t_rot = trigger_bt.rotation if trigger_bt else (0.0, 0.0, 0.0)
                    r_rot = pose_rest.rotation  if pose_rest  else (0.0, 0.0, 0.0)
                    b_rot = rp_base.rotation    if rp_base    else (0.0, 0.0, 0.0)
                    out_rot = tuple(b + (t - r) for b, t, r in zip(b_rot, t_rot, r_rot))

                    t_loc = trigger_bt.location if trigger_bt else (0.0, 0.0, 0.0)
                    r_loc = pose_rest.location  if pose_rest  else (0.0, 0.0, 0.0)
                    b_loc = rp_base.location    if rp_base    else (0.0, 0.0, 0.0)
                    out_loc = tuple(b + (t - r) for b, t, r in zip(b_loc, t_loc, r_loc))

                    return out_rot, out_loc

                d_rot, _     = _retarget(d_key)
                h_rot, h_loc = _retarget(h_key)
            else:
                # --- direct path (original behaviour) ---
                d_bt  = frame_map.get(d_key)
                h_bt  = frame_map.get(h_key)
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