from pathlib import Path
import re
import shutil
from collections import Counter
import subprocess

# Commands in VMT that reference textures
TEXTURE_KEYS = [
    "$basetexture",
    "$bumpmap",
    "$lightwarptexture",
    "$phongexponentexture",
    "$normalmap",
    "$emissiveblendbasetexture",
    "$emissiveblendtexture",
    "$emissiveblendflowtexture",
]

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

def parse_vmt_textures(vmt_path: Path) -> dict[str, str]:
    """
    Parse a VMT file and extract all texture references.

    Args:
        vmt_path: Path to the VMT file.

    Returns:
        Dict mapping VMT command -> referenced texture path (as string)
    """
    textures = {}
    if not vmt_path.exists():
        return textures

    with open(vmt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Match lines like: $basetexture "models/characters/toppi/hsr/asta_def/cloth1_d"
            match = re.match(r'(\$\w+)\s+"([^"]+)"', line)
            if match:
                key, value = match.groups()
                key = key.lower()
                if key in TEXTURE_KEYS:
                    textures[key] = value.replace("\\", "/")

    return textures

TEXTURE_KEYS = {
    "$basetexture", "$bumpmap", "$normalmap", "$envmapmask",
    "$detail", "$selfillummask", "$lightwarptexture"
}

def copy_materials(
    material_to_vmt: dict[str, Path],
    export_dir: Path,
    search_paths: list[Path],
    localize_data: bool = True,
    logger: "Logger | None" = None
) -> list[Path]:
    """
    Copy VMTs and their textures into export_dir/materials.
    If localize_data is True, re-map any out-of-place files into a shared/ folder
    under the majority folder, and rewrite VMTs with updated texture paths.
    If localize_data is False, preserve original folder structure and spacing.
    """
    copied_files = []
    processed_vmts = set()

    if not material_to_vmt:
        if logger:
            logger.warn("No materials to copy.")
        return copied_files

    # Resolve majority folder (only used when localizing)
    vmt_dirs = [vmt.parent for vmt in material_to_vmt.values()]
    majority_folder = Counter(vmt_dirs).most_common(1)[0][0]
    if logger:
        logger.debug(f"Majority folder resolved to: {majority_folder}")

    def relative_to_materials(path: Path) -> Path:
        try:
            idx = path.parts.index("materials")
            return Path(*path.parts[idx + 1:])
        except ValueError:
            if logger:
                logger.debug(f"Path not under 'materials': {path}")
            return Path(path.name)

    def localize_path(abs_path: Path) -> Path:
        rel_path = relative_to_materials(abs_path)
        if localize_data:
            try:
                abs_path.relative_to(majority_folder)
            except ValueError:
                rel_path = Path(
                    *majority_folder.parts[majority_folder.parts.index("materials")+1:]
                ) / "shared" / abs_path.name
                if logger:
                    logger.debug(f"Localized {abs_path} -> {rel_path}")
        return Path("materials") / rel_path

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
        dest_rel_vmt = localize_path(vmt_path)
        dest_vmt = export_dir / dest_rel_vmt
        dest_vmt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vmt_path, dest_vmt)
        copied_files.append(dest_vmt)

        if logger:
            logger.info(f"\tCopied VMT: {dest_rel_vmt}")

        # Read lines exactly as-is to preserve spacing
        with open(vmt_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            original_line = line  # Preserve whitespace/spacing
            stripped = line.strip()

            # Include match
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

            # Texture match
            tex_match = re.match(r'(\$\w+)\s+"([^"]+)"', stripped)
            if tex_match:
                key, tex_rel_path = tex_match.groups()
                key = key.lower()
                if key in TEXTURE_KEYS:
                    tex_rel = Path(tex_rel_path.replace("\\", "/"))
                    tex_file = None
                    for root in search_paths:
                        candidate = (root / "materials" / tex_rel).with_suffix(".vtf")
                        if candidate.exists():
                            tex_file = candidate
                            break
                    if tex_file:
                        dest_rel_tex = localize_path(tex_file)
                        dest_tex = export_dir / dest_rel_tex
                        dest_tex.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(tex_file, dest_tex)
                        copied_files.append(dest_tex)
                        if logger:
                            logger.debug(f"Copied texture: {dest_rel_tex}")

                        if localize_data:
                            # Replace only the texture path, preserve indentation
                            leading_ws = re.match(r"^\s*", original_line).group(0)
                            new_tex_rel = dest_rel_tex.relative_to("materials").with_suffix("").as_posix()
                            original_line = f'{leading_ws}{key} "{new_tex_rel}"\n'

            new_lines.append(original_line)

        # Rewrite only if localizing (otherwise leave file untouched)
        if localize_data:
            with open(dest_vmt, "w", encoding="utf-8") as f_out:
                f_out.writelines(new_lines)

    # Process all input VMTs
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
