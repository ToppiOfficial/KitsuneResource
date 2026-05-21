import struct
from pathlib import Path

MDL_MAGIC = b'IDST'
_TEXTURE_STRUCT_SIZE = 64  # sizeof(mstudiotexture_t)

# Byte offsets within studiohdr_t (versions 44-49, stable across Source Engine titles)
_OFF_VERSION      = 4
_OFF_NUMTEXTURES  = 204
_OFF_TEXTUREINDEX = 208
_OFF_NUMCDTEX     = 212
_OFF_CDTEXINDEX   = 216


def read_mdl_materials(mdl_path: Path) -> tuple[list[str], list[str]]:
    """
    Parse a compiled MDL and return (texture_names, cdmaterials_dirs).

    texture_names: material names as stored in the MDL (may be full paths or bare names).
    cdmaterials_dirs: cdmaterials search directories stored in the MDL.

    Supports MDL versions 44-49 (HL2 through L4D2 / Portal 2 / CS:GO).
    """
    data = mdl_path.read_bytes()

    if len(data) < 220:
        raise ValueError(f"MDL too small to be valid: {mdl_path}")
    if data[:4] != MDL_MAGIC:
        raise ValueError(f"Not an MDL file (bad magic): {mdl_path}")

    version = struct.unpack_from('<i', data, _OFF_VERSION)[0]
    if version < 44:
        raise ValueError(f"MDL version {version} is not supported (need 44+): {mdl_path}")

    numtextures, textureindex = struct.unpack_from('<2i', data, _OFF_NUMTEXTURES)
    numcdtextures, cdtextureindex = struct.unpack_from('<2i', data, _OFF_NUMCDTEX)

    texture_names = []
    for i in range(numtextures):
        struct_start = textureindex + i * _TEXTURE_STRUCT_SIZE
        # sznameindex is relative to its own struct start
        sznameindex = struct.unpack_from('<i', data, struct_start)[0]
        name = _read_cstring(data, struct_start + sznameindex)
        texture_names.append(name.replace('\\', '/'))

    cdmaterials = []
    for i in range(numcdtextures):
        offset_pos = cdtextureindex + i * 4
        # stored value is absolute offset from start of file (studiohdr_t base)
        string_offset = struct.unpack_from('<i', data, offset_pos)[0]
        dirname = _read_cstring(data, string_offset).replace('\\', '/')
        cdmaterials.append(dirname)

    return texture_names, cdmaterials


def build_material_paths(texture_names: list[str], cdmaterials: list[str]) -> list[str]:
    """
    Combine texture names and cdmaterials dirs into VMT-lookup-ready paths.

    Always includes each texture name as-is (for full-path textures / empty cdmaterials).
    Also prepends any non-empty cdmaterials dirs for bare texture names.
    """
    seen: set[str] = set()
    paths: list[str] = []

    def _add(p: str) -> None:
        p = p.strip('/').replace('\\', '/')
        if p and p not in seen:
            seen.add(p)
            paths.append(p)

    for tex in texture_names:
        _add(tex)

    for cd in cdmaterials:
        cd = cd.strip('/\\').replace('\\', '/')
        if not cd:
            continue
        for tex in texture_names:
            tex_norm = tex.strip('/').replace('\\', '/')
            _add(f"{cd}/{tex_norm}")

    return paths


def get_mdl_material_paths(mdl_path: Path) -> list[str]:
    """Convenience wrapper: read MDL then return combined material paths."""
    return build_material_paths(*read_mdl_materials(mdl_path))


def _read_cstring(data: bytes, offset: int) -> str:
    end = data.index(b'\x00', offset)
    return data[offset:end].decode('utf-8', errors='replace')
