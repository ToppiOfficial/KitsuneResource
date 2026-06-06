"""Pose-baking for the $staticbody QC command.

Deforms a skinned DMX mesh to a given animation-file frame, replaces the entire
skeleton with a single root bone named 'static_prop', strips all flex data, and
writes the result with a deterministic CRC-derived filename.
"""

import re
import zlib
from math import cos, sin, radians, sqrt
from pathlib import Path

from intern.utils import PROCESSED_ASSETS_DIRNAME
from intern.formats import datamodel
from intern.formats.bone_animations import (
    _euler_to_quat,
    frames_euler_to_quat,
    read_dmx_bone_animation,
    read_smd_bone_animation,
)


# ---------------------------------------------------------------------------
# Pure-Python 4×4 rigid-body matrix math (row-major, 16-element list)
# Translation lives in column 3: m[3], m[7], m[11].
# ---------------------------------------------------------------------------

def _mat4_identity():
    return [1.,0.,0.,0., 0.,1.,0.,0., 0.,0.,1.,0., 0.,0.,0.,1.]


def _mat4_from_tq(pos, quat_xyzw):
    """Translation + XYZW quaternion → 4×4 matrix."""
    x, y, z, w = quat_xyzw
    tx, ty, tz = pos
    xx, yy, zz = x*x, y*y, z*z
    xy, xz, yz = x*y, x*z, y*z
    wx, wy, wz = w*x, w*y, w*z
    return [
        1-2*(yy+zz),   2*(xy-wz),   2*(xz+wy), tx,
          2*(xy+wz), 1-2*(xx+zz),   2*(yz-wx), ty,
          2*(xz-wy),   2*(yz+wx), 1-2*(xx+yy), tz,
        0., 0., 0., 1.,
    ]


def _mat4_from_teuler_deg(pos, euler_deg_xyz):
    rx, ry, rz = (radians(v) for v in euler_deg_xyz)
    return _mat4_from_tq(pos, _euler_to_quat(rx, ry, rz))


def _mat4_mul(a, b):
    out = [0.] * 16
    for r in range(4):
        for c in range(4):
            out[r*4+c] = sum(a[r*4+k] * b[k*4+c] for k in range(4))
    return out


def _mat4_rigid_inv(m):
    """Inverse of a rigid (rotation + translation, no scale) 4×4 matrix."""
    # Transpose the 3×3 rotation block
    r = [m[0],m[4],m[8], m[1],m[5],m[9], m[2],m[6],m[10]]
    # New translation = -R^T * t
    tx = -(r[0]*m[3] + r[1]*m[7] + r[2]*m[11])
    ty = -(r[3]*m[3] + r[4]*m[7] + r[5]*m[11])
    tz = -(r[6]*m[3] + r[7]*m[7] + r[8]*m[11])
    return [
        r[0], r[1], r[2], tx,
        r[3], r[4], r[5], ty,
        r[6], r[7], r[8], tz,
        0.,   0.,   0.,   1.,
    ]


def _mat4_apply_point(m, v):
    x, y, z = v
    return (
        m[0]*x + m[1]*y + m[2]*z + m[3],
        m[4]*x + m[5]*y + m[6]*z + m[7],
        m[8]*x + m[9]*y + m[10]*z + m[11],
    )


def _mat4_apply_dir(m, v):
    """Rotation only (no translation) — for transforming normals."""
    x, y, z = v
    return (
        m[0]*x + m[1]*y + m[2]*z,
        m[4]*x + m[5]*y + m[6]*z,
        m[8]*x + m[9]*y + m[10]*z,
    )


# ---------------------------------------------------------------------------
# Build world-space 4×4 matrices from a map of local (parent-relative) transforms
# ---------------------------------------------------------------------------

def _build_world_transforms(local_map):
    """
    local_map: {bone_name: (pos_tuple, quat_xyzw_tuple, parent_name_or_None)}
    Returns: {bone_name: 4×4 world matrix}
    """
    world = {}

    def _resolve(name, stack=None):
        if name in world:
            return world[name]
        stack = stack or set()
        if name in stack:  # cycle guard
            world[name] = _mat4_identity()
            return world[name]
        stack.add(name)
        entry = local_map.get(name)
        if entry is None:
            world[name] = _mat4_identity()
            return world[name]
        pos, quat, parent_name = entry
        local_m = _mat4_from_tq(pos, quat)
        if parent_name and parent_name in local_map:
            world[name] = _mat4_mul(_resolve(parent_name, stack), local_m)
        else:
            world[name] = local_m
        return world[name]

    for name in local_map:
        _resolve(name)
    return world


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_dme_model(dm):
    for key in ("skeleton", "model"):
        m = dm.root.get(key)
        if m is not None:
            return m
    for e in dm.elements:
        if e.type == "DmeModel":
            return e
    return None


def _sniff_encoding(mesh_path):
    enc, ver = "keyvalues2", 1
    try:
        with open(mesh_path, "rb") as fh:
            hdr = b""
            while not hdr.endswith(b">"):
                ch = fh.read(1)
                if not ch:
                    break
                hdr += ch
        hdr_str = hdr.decode("ascii", errors="ignore")
        m = re.findall(datamodel.header_format_regex, hdr_str)
        if m:
            enc, ver = m[0][0], int(m[0][1])
        else:
            m = re.findall(datamodel.header_proto2_regex, hdr_str)
            if m:
                enc, ver = "binary_proto", int(m[0][0])
    except Exception:
        pass
    return enc, ver


def _to_local_quat_map(frames, frame_idx):
    """Return {bone_name: (pos, quat_xyzw, parent_name)} for the given frame."""
    result = {}
    for bt in frames[frame_idx]:
        q = bt.rotation
        if len(q) == 3:
            q = _euler_to_quat(*q)
        result[bt.bone_name] = (bt.location, q, bt.parent_name)
    return result


# ---------------------------------------------------------------------------
# Mesh filtering (excludemesh / isolatemesh) — same algorithm as _make_edited_dmx
# ---------------------------------------------------------------------------

def _apply_mesh_filter(dm, del_names, keep_names, logger=None):
    """Remove DmeMesh elements (and exclusively-owned sub-elements) by name.
    keep_names triggers inverse deletion: every mesh NOT in the list is removed."""
    if not del_names and keep_names is None:
        return

    mesh_map = {e.name: e for e in dm.elements if e.type == "DmeMesh"}

    if keep_names is not None:
        if not keep_names:
            raise ValueError("isolatemesh/keeponlymesh block is empty — would produce an empty mesh")
        for m_name in list(mesh_map):
            if m_name not in keep_names and m_name not in (del_names or []):
                del_names = list(del_names or []) + [m_name]

    if not del_names:
        return

    def _iter_refs(elem):
        for key in elem.keys():
            val = elem[key]
            if isinstance(val, datamodel.Element):
                yield val
            elif isinstance(val, datamodel._ElementArray):
                for v in val:
                    if isinstance(v, datamodel.Element):
                        yield v

    all_ids  = {e.id: e for e in dm.elements}
    refcount = {e.id: 0 for e in dm.elements}
    for elem in dm.elements:
        for ref in _iter_refs(elem):
            if ref.id in refcount:
                refcount[ref.id] += 1

    condemned = set()

    def _condemn(elem):
        if elem in condemned:
            return
        condemned.add(elem)
        for ref in _iter_refs(elem):
            if ref.id not in refcount:
                continue
            refcount[ref.id] -= 1
            if refcount[ref.id] <= 0:
                _condemn(all_ids[ref.id])

    for mesh_name in del_names:
        mesh_elem = mesh_map.get(mesh_name)
        if mesh_elem is None:
            if logger:
                logger.warn(f"$staticbody excludemesh: mesh '{mesh_name}' not found")
            continue
        _condemn(mesh_elem)
        for e in dm.elements:
            if e.type == "DmeDag" and e.get("shape") is mesh_elem:
                _condemn(e)
                trfm = e.get("transform")
                if isinstance(trfm, datamodel.Element):
                    _condemn(trfm)

    for e in list(condemned):
        if e in dm.elements:
            dm.elements.remove(e)

    for parent in dm.elements:
        for attr_key in list(parent.keys()):
            val = parent[attr_key]
            if isinstance(val, datamodel.Element) and val in condemned:
                del parent[attr_key]
            elif isinstance(val, datamodel._ElementArray):
                for e in list(condemned):
                    while e in val:
                        val.remove(e)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def bake_static_mesh(
    mesh_path: Path,
    pose_path: Path,
    frame_idx: int,
    origin: tuple,           # (x, y, z, rx, ry, rz) degrees
    logger=None,
    del_names: list = None,  # excludemesh names
    keep_names: list = None, # isolatemesh names
    tracker=None,
) -> Path:
    """
    Deform *mesh_path* (DMX) to the skeletal pose in *pose_path* at *frame_idx*.
    Replace the skeleton with a single 'static_prop' bone at *origin*, strip flex,
    and write a new DMX file with a CRC-derived name alongside the source mesh.
    Returns the output path.
    """
    # ---- deterministic output path ----------------------------------------
    origin_key = ":".join(f"{v:.6f}" for v in origin)
    filter_key = f"del={sorted(del_names or [])}:keep={sorted(keep_names or []) if keep_names is not None else None}"
    crc = zlib.crc32(
        f"static:{pose_path}:{frame_idx}:{origin_key}:{filter_key}".encode()
    ) & 0xFFFFFFFF
    out_path = mesh_path.parent / PROCESSED_ASSETS_DIRNAME / f"{mesh_path.stem}_static_{crc:08x}.dmx"

    if out_path.exists():
        if logger:
            logger.info(f"$staticbody: reusing cached '{out_path.name}'")
        if tracker:
            tracker.claim(out_path)
        return out_path

    orig_enc, orig_ver = _sniff_encoding(mesh_path)

    # ---- load mesh --------------------------------------------------------
    dm = datamodel.load(str(mesh_path))

    # ---- apply mesh filter (excludemesh / isolatemesh) before deforming ---
    _apply_mesh_filter(dm, del_names, keep_names, logger)

    # ---- bind-pose (local transforms from mesh DMX) -----------------------
    bind_frames = read_dmx_bone_animation(str(mesh_path))
    if bind_frames:
        bind_local = _to_local_quat_map(bind_frames, 0)
    else:
        bind_local = {}
    bind_world = _build_world_transforms(bind_local)

    # ---- posed transforms at frame_idx ------------------------------------
    ext = Path(pose_path).suffix.lower()
    if ext == ".dmx":
        pose_frames = read_dmx_bone_animation(str(pose_path))
    else:
        pose_frames = frames_euler_to_quat(read_smd_bone_animation(str(pose_path)))

    if pose_frames and 0 <= frame_idx < len(pose_frames):
        pose_local = _to_local_quat_map(pose_frames, frame_idx)
    else:
        if logger:
            logger.warn(
                f"$staticbody: frame {frame_idx} out of range in '{pose_path.name}', "
                "using bind pose"
            )
        pose_local = dict(bind_local)
    pose_world = _build_world_transforms(pose_local)

    # ---- per-bone skinning matrices (pose_world @ inv(bind_world)) --------
    skin_mats = {}
    for bone in set(bind_world) | set(pose_world):
        skin_mats[bone] = _mat4_mul(
            pose_world.get(bone, _mat4_identity()),
            _mat4_rigid_inv(bind_world.get(bone, _mat4_identity())),
        )

    # ---- locate DmeModel and build joint index → name map -----------------
    # Done AFTER mesh filter so original_jl reflects any removed DmeDag entries.
    DmeModel = _find_dme_model(dm)

    original_jl = []
    model_at_slot0 = False
    if DmeModel is not None:
        jl = DmeModel.get("jointList")
        if jl:
            original_jl = list(jl)
            model_at_slot0 = bool(original_jl) and (original_jl[0] is DmeModel)

    # Map each joint-list index to a bone name (None for DmeModel placeholder)
    joint_idx_to_name = {}
    for i, j in enumerate(original_jl):
        joint_idx_to_name[i] = None if (j is DmeModel) else j.name

    # After replacement, static_prop will be at slot 0 if DmeModel was not there,
    # or at slot 1 if DmeModel occupies slot 0.
    sp_joint_slot = 1 if model_at_slot0 else 0

    # ---- deform all DmeMesh vertex data -----------------------------------
    for elem in dm.elements:
        if elem.type != "DmeMesh":
            continue

        # Resolve the DmeVertexData that holds positions and joint weights.
        # Three conventions exist across format versions:
        #   model 18 (Blender Source Tools): DmeMesh.bindState → DmeVertexData
        #   newer exports: DmeMesh.vertexData → DmeVertexData
        #   oldest: positions/normals stored directly on DmeMesh
        vd = elem.get("vertexData")
        if vd is None:
            vd = elem.get("bindState")
        if vd is None:
            bs = elem.get("baseStates")
            vd = bs[0] if bs else None
        if vd is None and elem.get("positions") is not None:
            vd = elem
        if vd is None:
            continue

        positions    = vd.get("positions")
        normals      = vd.get("normals")
        jweights     = vd.get("jointWeights")
        jindices     = vd.get("jointIndices")
        jcount_raw   = vd.get("jointCount", 0)

        if not positions:
            continue

        if hasattr(jcount_raw, "__len__"):
            joint_count = int(jcount_raw[0]) if jcount_raw else 0
        else:
            joint_count = int(jcount_raw or 0)

        n_pos = len(positions)

        # --- build normal→joint-weights mapping via face index arrays ---
        # This allows proper deformation even when the normals pool size ≠
        # positions pool size (normal splitting at hard edges).
        pos_idx_arr = vd.get("positionsIndices") or vd.get("posIndices")
        nrm_idx_arr = vd.get("normalsIndices")   or vd.get("nrmIndices")

        nrm_weights = None  # {normal_idx: [(weight, joint_idx), ...]}
        if (normals and jweights and joint_count > 0
                and pos_idx_arr is not None and nrm_idx_arr is not None
                and len(pos_idx_arr) == len(nrm_idx_arr)):
            nrm_weights = {}
            for fv in range(len(pos_idx_arr)):
                pi = int(pos_idx_arr[fv])
                ni = int(nrm_idx_arr[fv])
                if ni in nrm_weights:
                    continue
                wl = []
                for k in range(joint_count):
                    fi = pi * joint_count + k
                    if fi >= len(jweights):
                        break
                    wl.append((float(jweights[fi]),
                                int(jindices[fi]) if jindices else 0))
                nrm_weights[ni] = wl
        elif normals and jweights and joint_count > 0 and len(normals) == n_pos:
            nrm_weights = {
                ni: [
                    (float(jweights[ni*joint_count+k]),
                     int(jindices[ni*joint_count+k]) if jindices else 0)
                    for k in range(joint_count)
                    if ni*joint_count+k < len(jweights)
                ]
                for ni in range(len(normals))
            }

        def _apply_skin(v, weighted_pairs, is_dir=False):
            result = (0., 0., 0.)
            fn = _mat4_apply_dir if is_dir else _mat4_apply_point
            for w, ji in weighted_pairs:
                if w == 0.:
                    continue
                jn = joint_idx_to_name.get(ji)
                sm = skin_mats.get(jn, _mat4_identity()) if jn else _mat4_identity()
                tp = fn(sm, v)
                result = (result[0]+w*tp[0], result[1]+w*tp[1], result[2]+w*tp[2])
            return result

        # --- deform positions ---
        for vi, pv in enumerate(positions):
            pos = (float(pv[0]), float(pv[1]), float(pv[2]))
            if jweights and joint_count > 0:
                pairs = [
                    (float(jweights[vi*joint_count+k]),
                     int(jindices[vi*joint_count+k]) if jindices else 0)
                    for k in range(joint_count)
                    if vi*joint_count+k < len(jweights)
                ]
                pos = _apply_skin(pos, pairs, is_dir=False)
            positions[vi] = datamodel.Vector3([pos[0], pos[1], pos[2]])

        # --- deform normals ---
        if normals and nrm_weights is not None:
            for ni, nv in enumerate(normals):
                nrm = (float(nv[0]), float(nv[1]), float(nv[2]))
                wl = nrm_weights.get(ni)
                if wl:
                    nrm = _apply_skin(nrm, wl, is_dir=True)
                    ll = sqrt(nrm[0]**2 + nrm[1]**2 + nrm[2]**2)
                    if ll > 1e-8:
                        nrm = (nrm[0]/ll, nrm[1]/ll, nrm[2]/ll)
                normals[ni] = datamodel.Vector3([nrm[0], nrm[1], nrm[2]])

        # --- reset joint weights: all vertices fully bind to static_prop ---
        if jweights is not None and joint_count > 0:
            for vi in range(n_pos):
                for k in range(joint_count):
                    fi = vi * joint_count + k
                    if fi < len(jweights):
                        jweights[fi] = 1.0 if k == 0 else 0.0
                    if jindices is not None and fi < len(jindices):
                        jindices[fi] = sp_joint_slot

        # --- clear flex morph delta states ---
        ds = elem.get("deltaStates")
        if ds is not None:
            try:
                elem["deltaStates"] = datamodel.make_array([], datamodel.Element)
            except Exception:
                pass
        dw = elem.get("deltaStateWeights")
        if dw is not None:
            try:
                dw.clear()
            except Exception:
                pass

    # ---- replace skeleton with single static_prop bone --------------------
    if DmeModel is not None:
        ox, oy, oz, orx, ory, orz = origin
        q_sp = _euler_to_quat(radians(orx), radians(ory), radians(orz))

        sp_trfm = datamodel.Element(dm, "static_prop", "DmeTransform")
        sp_trfm["position"]    = datamodel.Vector3([float(ox), float(oy), float(oz)])
        sp_trfm["orientation"] = datamodel.Quaternion([q_sp[0], q_sp[1], q_sp[2], q_sp[3]])

        sp_joint = datamodel.Element(dm, "static_prop", "DmeJoint")
        sp_joint["transform"] = sp_trfm   # auto-imports sp_trfm into dm.elements
        try:
            sp_joint["children"] = datamodel.make_array([], datamodel.Element)
        except Exception:
            pass

        # DmeModel.children → keep mesh DmeDag containers, replace all joints
        original_children = list(DmeModel.get("children") or [])
        mesh_dags = [c for c in original_children if c.type == "DmeDag"]
        DmeModel["children"] = datamodel.make_array(mesh_dags + [sp_joint], datamodel.Element)

        # DmeModel.jointList: keep the DmeDag mesh-container entries (studiomdl
        # requires every DmeDag node in the hierarchy to appear in the list),
        # replace all DmeJoint bones with the single static_prop bone.
        # Preserve DmeModel at slot 0 if the original file had it there.
        if DmeModel.get("jointList") is not None:
            dag_entries = [j for j in original_jl if j.type == "DmeDag"]
            if model_at_slot0:
                new_jl = [DmeModel, sp_joint] + dag_entries
            else:
                new_jl = [sp_joint] + dag_entries
            DmeModel["jointList"] = datamodel.make_array(new_jl, datamodel.Element)

        # Update baseStates rest-pose transforms
        bs = DmeModel.get("baseStates")
        if bs and len(bs) > 0:
            try:
                bs[0]["transforms"] = datamodel.make_array([sp_trfm], datamodel.Element)
            except Exception:
                pass

    # ---- strip flex controller elements -----------------------------------
    _FLEX_TYPES = {"DmeCombinationInputControl", "DmeCombinationDominationRule"}
    flex_elems  = {e for e in dm.elements if e.type in _FLEX_TYPES}
    for e in flex_elems:
        dm.elements.remove(e)
    for parent in dm.elements:
        for key in list(parent.keys()):
            val = parent[key]
            if isinstance(val, datamodel.Element) and val in flex_elems:
                del parent[key]
            elif isinstance(val, datamodel._ElementArray):
                for fe in flex_elems:
                    while fe in val:
                        val.remove(fe)

    # ---- write ------------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dm.write(str(out_path), orig_enc, orig_ver)
    if logger:
        logger.info(f"$staticbody: wrote baked mesh → '{out_path.name}'")
    if tracker:
        tracker.claim(out_path)
    return out_path
