from pathlib import Path
from intern.formats import bone_animations
from intern.utils import PROCESSED_ASSETS_DIRNAME
import hashlib
import json
import shlex


def _strip_prefix(bone_name: str) -> str:
    return bone_name.split('.')[-1]


def _file_content_sig(path: Path) -> str:
    """Short content signature (truncated SHA-256) of *path*, or "nosig"."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return "nosig"


def _vrd_signature(parts) -> str:
    """Stable hash of all inputs that determine a generated VRD's contents."""
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]


def _vrd_cache_hit(vrd_path: Path, sig_path: Path, sig: str, logger) -> bool:
    """True when a previously generated VRD with the same signature exists."""
    if not (vrd_path.exists() and sig_path.exists()):
        return False
    try:
        if sig_path.read_text(encoding="utf-8").strip() == sig:
            if logger:
                logger.info(f"(VRD cached): {vrd_path.name}")
            return True
    except OSError:
        pass
    return False


def _write_vrd(out_dir: Path, vrd_name: str, vrd_lines: list, sig: str, logger) -> Path:
    """Write the VRD plus its signature sidecar, and return the VRD path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vrd_path = out_dir / f"{vrd_name}.vrd"
    vrd_path.write_text("\n".join(vrd_lines), encoding="utf-8")
    try:
        (out_dir / f"{vrd_name}.vrd.sig").write_text(sig, encoding="utf-8")
    except OSError:
        pass
    if logger:
        logger.info(f"(VRD generated): {vrd_path.name}")
    return vrd_path


def _load_euler_frames(filepath: Path, scale: float, frame_cache=None) -> bone_animations.BoneFrameData:
    """Load a DMX or SMD file and return euler-degree frames, with scale applied.

    When *frame_cache* (a dict) is supplied, results are memoized by
    ``(resolved_path, scale)`` so a pose file shared by many VRD blocks is
    parsed and converted only once per build. Returned frames are treated as
    read-only by callers, so sharing them is safe.
    """
    ext = filepath.suffix.lower()
    if not ext:
        for candidate_ext in (".dmx", ".smd"):
            candidate = filepath.with_suffix(candidate_ext)
            if candidate.exists():
                filepath = candidate
                ext = candidate_ext
                break

    key = (str(filepath), scale)
    if frame_cache is not None and key in frame_cache:
        return frame_cache[key]

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

    if frame_cache is not None:
        frame_cache[key] = frames

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
                        scale: float = 1.0, logger=None, frame_cache=None) -> Path:

    pose_file = _resolve_pose_file(pose_dir, pose_path)

    out_dir  = vrd_dir / PROCESSED_ASSETS_DIRNAME / "vrds"
    vrd_path = out_dir / f"{vrd_name}.vrd"
    sig_path = out_dir / f"{vrd_name}.vrd.sig"
    sig = _vrd_signature([
        "lookat", _file_content_sig(pose_file), target_bone, attachment_name,
        frame_index, list(aimvector), list(upvector), list(helper_bones), scale,
    ])
    if _vrd_cache_hit(vrd_path, sig_path, sig, logger):
        return vrd_path

    euler_frames = _load_euler_frames(pose_file, scale, frame_cache=frame_cache)

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

    return _write_vrd(out_dir, vrd_name, vrd_lines, sig, logger)


def generate_vrd(driver_bone: str, pose_path: str, triggers: list[tuple[float, int]],
                 target_bones: list[str], pose_dir: Path, vrd_dir: Path, vrd_name: str,
                 scale: float = 1.0, logger=None,
                 restpose_path: str | None = None, restpose_frame: int = 0,
                 autotrigger: tuple[int, int] | None = None, frame_cache=None) -> Path:

    pose_file = _resolve_pose_file(pose_dir, pose_path)
    rp_file   = _resolve_pose_file(pose_dir, restpose_path) if restpose_path is not None else None

    out_dir  = vrd_dir / PROCESSED_ASSETS_DIRNAME / "vrds"
    vrd_path = out_dir / f"{vrd_name}.vrd"
    sig_path = out_dir / f"{vrd_name}.vrd.sig"
    sig = _vrd_signature([
        "driver", _file_content_sig(pose_file),
        _file_content_sig(rp_file) if rp_file else None,
        driver_bone, list(target_bones), [list(t) for t in triggers], scale,
        list(autotrigger) if autotrigger else None, restpose_frame,
    ])
    if _vrd_cache_hit(vrd_path, sig_path, sig, logger):
        return vrd_path

    euler_frames = _load_euler_frames(pose_file, scale, frame_cache=frame_cache)

    if autotrigger is not None:
        total = len(euler_frames)
        fmin, fmax = autotrigger
        start = 0         if fmin == -1 else max(0, fmin)
        end   = total - 1 if fmax == -1 else min(total - 1, fmax)
        manual = {frame: angle for angle, frame in triggers}
        triggers = [(manual.get(f, 90.0), f) for f in range(start, end + 1)]
        if logger:
            logger.info(f"(autotrigger): {len(triggers)} triggers generated for frames {start}–{end}")

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
        try:
            rp_frames = _load_euler_frames(rp_file, scale, frame_cache=frame_cache)
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

    return _write_vrd(out_dir, vrd_name, vrd_lines, sig, logger)