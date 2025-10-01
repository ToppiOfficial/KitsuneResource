from pathlib import Path
import re
import shutil
from collections import Counter
import subprocess

# Commands in VMT that reference textures
TEXTURE_KEYS = {
    "$basetexture",
    "$basetexture2",
    "$bumpmap",
    "$lightwarptexture",
    "$phongexponenttexture",
    "$normalmap",
    "$emissiveblendbasetexture",
    "$emissiveblendtexture",
    "$emissiveblendflowtexture",
    "$ssbump",
    "$envmapmask",
    "$detail",
    "$detail2",
    "$blendmodulatetexture",
    "$AmbientOcclTexture",
    "$CorneaTexture",
    "$Envmap",
    "$phongwarptexture",
    "$selfillummask",
    "$selfillumtexture",
    "$detail1",
    "$iris",
}

def find_material_vmt(material_name: str, search_paths: list[Path]) -> Path | None:
    """
    Locate the .vmt file for a given material name.

    Args:
        material_name: e.g. "models/characters/toppi/gf2/sharkry_def/cloth1"
        search_paths: list of root paths from gameinfo.txt

    Returns:
        Absolute Path to the VMT file if found, else None
    """
    relative_vmt = Path("materials") / Path(material_name + ".vmt")
    for root in search_paths:
        candidate = (root / relative_vmt).resolve()
        if candidate.exists():
            return candidate
    return None

def map_materials_to_vmt(materials_list: list[str], search_paths: list[Path]) -> dict[str, Path]:
    """
    Map each dumped material name to its .vmt file.
    """
    result = {}
    for mat in materials_list:
        vmt = find_material_vmt(mat, search_paths)
        if vmt:
            result[mat] = vmt
    return result

def parse_vmt_textures(vmt_path: Path) -> dict[str, Path]:
    """
    Parse a VMT file and extract all texture references (case-insensitive).

    Args:
        vmt_path: Path to the VMT file.

    Returns:
        Dict mapping VMT command -> referenced texture path (as Path)
    """
    textures = {}
    if not vmt_path.exists():
        return textures

    # Lowercase keys for case-insensitive matching
    lowercase_keys = {k.lower() for k in TEXTURE_KEYS}

    with open(vmt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("//"):
                continue  # skip commented lines

            match = re.match(r'(\$\w+)\s+"([^"]+)"', line, flags=re.IGNORECASE)
            if match:
                key, value = match.groups()
                key_lower = key.lower()
                if key_lower in lowercase_keys:
                    # Normalize path and store as Path
                    textures[key_lower] = Path(value.replace("\\", "/"))

    return textures

def copy_materials(
    material_to_vmt: dict[str, Path],
    export_dir: Path,
    search_paths: list[Path],
    localize_data: bool = True,
    logger: "Logger | None" = None
) -> list[Path]:
    copied_files = []
    processed_vmts = set()

    if not material_to_vmt:
        if logger:
            logger.warn("No materials to copy.")
        return copied_files

    def relative_to_materials_root(path: Path) -> Path:
        """Return path starting from the first 'materials' folder."""
        try:
            idx = path.parts.index("materials")
            return Path(*path.parts[idx:])
        except ValueError:
            return Path("materials") / path.name

    def localize_vtf(vtf_path: Path, vmt_path: Path) -> Path:
        """Move VTF to vmt.parent/shared/ if outside vmt folder."""
        if not localize_data or vtf_path.parent == vmt_path.parent:
            return export_dir / relative_to_materials_root(vtf_path)
        return export_dir / relative_to_materials_root(vmt_path).parent / "shared" / vtf_path.name

    def _process_vmt(vmt_path: Path):
        if vmt_path in processed_vmts:
            if logger:
                logger.debug(f"Skipping already processed VMT: {vmt_path}")
            return
        if not vmt_path.exists():
            if logger:
                logger.warn(f"Missing VMT: {vmt_path}")
            return

        processed_vmts.add(vmt_path)

        # Copy VMT
        dest_vmt = export_dir / relative_to_materials_root(vmt_path)
        dest_vmt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vmt_path, dest_vmt)
        copied_files.append(dest_vmt)
        if logger:
            logger.info(f"Copied VMT: {dest_vmt.relative_to(export_dir)}")

        with open(vmt_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        textures = parse_vmt_textures(vmt_path)
        new_lines = []

        for line in lines:
            original_line = line
            stripped = line.strip()

            # Handle included VMTs recursively
            include_match = re.match(r'include\s+"([^"]+)"', stripped, flags=re.IGNORECASE)
            if include_match:
                include_path = include_match.group(1).replace("\\", "/")
                for root in search_paths:
                    candidate = (root / "materials" / include_path).with_suffix(".vmt")
                    if candidate.exists():
                        if logger:
                            logger.debug(f"Processing included VMT: {candidate}")
                        _process_vmt(candidate)
                        break

            # Handle texture references
            for key, tex_rel in textures.items():
                if key in stripped.lower():
                    tex_file = None
                    for root in search_paths:
                        candidate = (root / "materials" / tex_rel).with_suffix(".vtf")
                        if candidate.exists():
                            tex_file = candidate
                            break

                    if tex_file:
                        dest_tex = localize_vtf(tex_file, vmt_path)
                        dest_tex.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(tex_file, dest_tex)
                        copied_files.append(dest_tex)
                        if logger:
                            logger.debug(f"Copied texture: {dest_tex.relative_to(export_dir)}")

                        # Rewrite VMT line to path relative to materials/ root
                        if localize_data and tex_file.parent != vmt_path.parent:
                            leading_ws = re.match(r"^\s*", original_line).group(0)
                            materials_root = export_dir / "materials"
                            new_tex_rel = dest_tex.relative_to(materials_root).with_suffix("").as_posix()
                            original_line = f'{leading_ws}{key} "{new_tex_rel}"\n'

            new_lines.append(original_line)

        if localize_data:
            with open(dest_vmt, "w", encoding="utf-8") as f_out:
                f_out.writelines(new_lines)

    for vmt_path in material_to_vmt.values():
        _process_vmt(vmt_path)

    return copied_files

def export_vtf(
    src_path,
    dst_path,
    vtfcmd,
    fmt="DXT5",
    alpha_fmt=None,
    version="7.4",
    flags=None,
    resize=None,
    resize_method=None,
    resize_filter=None,
    sharpen_filter=None,
    nomipmaps=False,
    normal_map=False,
    normal_options=None,
    gamma_correction=None,
    extra_args=None,
    silent=True,
):
    """
    Flexible wrapper for vtfcmd.exe to convert images to VTF, always enforcing -resize.
    """
    src_path = Path(src_path).resolve()
    dst_path = Path(dst_path).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    if src_path.suffix.lower() == ".vtf":
        shutil.copy2(src_path, dst_path)
        return dst_path

    args = [
        str(vtfcmd),
        "-file", str(src_path),
        "-output", str(dst_path.parent),
        "-format", fmt,
        "-version", version,
        "-resize",  # Always include -resize
    ]

    if resize:
        w, h = resize
        args += ["-rwidth", str(w), "-rheight", str(h)]

    if resize_method:
        args += ["-rmethod", resize_method.upper()]
    if resize_filter:
        args += ["-rfilter", resize_filter.upper()]
    if sharpen_filter:
        args += ["-rsharpen", sharpen_filter.upper()]

    # Alpha format
    args += ["-alphaformat", alpha_fmt or fmt]

    if silent:
        args.append("-silent")

    # Flags
    if flags:
        for f in flags:
            f_clean = str(f).strip().upper()
            if f_clean:
                args += ["-flag", f_clean]
                if f_clean == "NOMIP":
                    nomipmaps = True

    if nomipmaps:
        args.append("-nomipmaps")

    # Normal map
    if normal_map:
        args.append("-normal")
        if normal_options:
            if "kernel" in normal_options:
                args += ["-nkernel", normal_options["kernel"]]
            if "height" in normal_options:
                args += ["-nheight", normal_options["height"]]
            if "alpha" in normal_options:
                args += ["-nalpha", normal_options["alpha"]]
            if "scale" in normal_options:
                args += ["-nscale", str(normal_options["scale"])]

    # Gamma correction
    if gamma_correction is not None:
        args += ["-gamma", "-gcorrection", str(gamma_correction)]

    # Extra args
    if extra_args:
        args.extend(extra_args)

    try:
        subprocess.run(args, check=True)
    except subprocess.CalledProcessError:
        print(f"[ERROR] VTF conversion failed: {src_path} -> {dst_path}")
        raise

    # Rename output file if necessary
    converted = dst_path.parent / (src_path.stem + ".vtf")
    if converted != dst_path:
        if dst_path.exists():
            dst_path.unlink()
        converted.rename(dst_path)

    return dst_path
