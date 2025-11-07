from pathlib import Path
import re
import shutil
import subprocess
from utils import Logger

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
    relative_vmt = Path("materials") / Path(material_name + ".vmt")
    for root in search_paths:
        candidate = (root / relative_vmt).resolve()
        if candidate.exists():
            return candidate
    return None

def map_materials_to_vmt(materials_list: list[str], search_paths: list[Path]) -> dict[str, Path]:
    result = {}
    for mat in materials_list:
        vmt = find_material_vmt(mat, search_paths)
        if vmt:
            result[mat] = vmt
    return result

def parse_vmt_structure(vmt_path: Path) -> dict:
    """Parse VMT file and detect if it's a patch shader, extract include path and blocks."""
    if not vmt_path.exists():
        return {"is_patch": False, "textures": {}}
    
    with open(vmt_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    lines = content.splitlines()
    lowercase_keys = {k.lower() for k in TEXTURE_KEYS}
    
    first_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("//"):
            first_line = stripped
            break
    
    is_patch = first_line.lower() == "patch"
    include_path = None
    replace_textures = {}
    insert_textures = {}
    regular_textures = {}
    
    if is_patch:
        include_match = re.search(r'include\s+"([^"]+)"', content, flags=re.IGNORECASE)
        if include_match:
            include_path = include_match.group(1).replace("\\", "/")
        
        replace_match = re.search(r'replace\s*\{([^}]*)\}', content, flags=re.IGNORECASE | re.DOTALL)
        if replace_match:
            block_content = replace_match.group(1)
            for match in re.finditer(r'(\$\w+)\s+"([^"]+)"', block_content, flags=re.IGNORECASE):
                key, value = match.groups()
                key_lower = key.lower()
                if key_lower in lowercase_keys:
                    replace_textures[key_lower] = Path(value.replace("\\", "/"))
        
        insert_match = re.search(r'insert\s*\{([^}]*)\}', content, flags=re.IGNORECASE | re.DOTALL)
        if insert_match:
            block_content = insert_match.group(1)
            for match in re.finditer(r'(\$\w+)\s+"([^"]+)"', block_content, flags=re.IGNORECASE):
                key, value = match.groups()
                key_lower = key.lower()
                if key_lower in lowercase_keys:
                    insert_textures[key_lower] = Path(value.replace("\\", "/"))
    else:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            
            match = re.match(r'(\$\w+)\s+"([^"]+)"', stripped, flags=re.IGNORECASE)
            if match:
                key, value = match.groups()
                key_lower = key.lower()
                if key_lower in lowercase_keys:
                    regular_textures[key_lower] = Path(value.replace("\\", "/"))
    
    return {
        "is_patch": is_patch,
        "include_path": include_path,
        "replace_textures": replace_textures,
        "insert_textures": insert_textures,
        "textures": regular_textures
    }

def parse_vmt_textures(vmt_path: Path) -> dict[str, Path]:
    """Legacy function - now calls parse_vmt_structure for backward compatibility."""
    structure = parse_vmt_structure(vmt_path)
    if structure["is_patch"]:
        all_textures = {}
        all_textures.update(structure["replace_textures"])
        all_textures.update(structure["insert_textures"])
        return all_textures
    return structure["textures"]

def copy_materials(
    material_to_vmt: dict[str, Path],
    export_dir: Path,
    search_paths: list[Path],
    localize_data: bool = True,
    logger: Logger | None = None
) -> list[Path]:
    copied_files: list[Path] = []
    processed_vmts: dict[Path, Path] = {}
    texture_cache: dict[str, Path] = {}

    if not material_to_vmt:
        logger and logger.warn("No materials to copy.")
        return copied_files

    def relative_to_materials_root(path: Path) -> Path:
        try:
            idx = path.parts.index("materials")
            return Path(*path.parts[idx:])
        except ValueError:
            return Path("materials") / path.name

    def find_texture(tex_rel: str) -> Path | None:
        """Find and cache the full path of a texture relative path."""
        if tex_rel in texture_cache:
            return texture_cache[tex_rel]
        for root in search_paths:
            candidate = (root / "materials" / tex_rel).with_suffix(".vtf")
            if candidate.exists():
                texture_cache[tex_rel] = candidate
                return candidate
        return None

    def localize_vtf(vtf_path: Path, dest_vmt_path: Path, nosubfolder: bool = False) -> Path:
        """Decide where the VTF should be copied to avoid duplicate shared paths."""
        if not localize_data:
            return export_dir / relative_to_materials_root(vtf_path)

        vtf_rel = relative_to_materials_root(vtf_path)
        vmt_rel = dest_vmt_path.relative_to(export_dir)

        if vtf_rel.parent == vmt_rel.parent or nosubfolder:
            return dest_vmt_path.parent / vtf_path.name

        # Only add 'shared' if not already inside one
        dest = dest_vmt_path.parent
        if dest.name != "shared":
            dest = dest / "shared"
        return dest / vtf_path.name

    def _process_vmt(vmt_path: Path, dest_vmt: Path | None = None, nosubfolder: bool = False):
        """Process a single VMT and copy associated textures."""
        if vmt_path in processed_vmts:
            logger and logger.debug(f"Skipping already processed VMT: {vmt_path}")
            return {}, processed_vmts[vmt_path]

        if not vmt_path.exists():
            logger and logger.warn(f"Missing VMT: {vmt_path}")
            return {}, None

        dest_vmt = dest_vmt or (export_dir / relative_to_materials_root(vmt_path))
        processed_vmts[vmt_path] = dest_vmt

        dest_vmt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vmt_path, dest_vmt)
        copied_files.append(dest_vmt)
        logger and logger.info(f"Copied VMT: {dest_vmt.relative_to(export_dir)}")

        structure = parse_vmt_structure(vmt_path)

        included_textures, included_vmt_dest = {}, None
        if structure["is_patch"] and structure["include_path"]:
            include_path = structure["include_path"]
            for root in search_paths:
                candidate = (root / include_path).with_suffix(".vmt")
                if candidate.exists():
                    logger and logger.debug(f"Found included VMT: {candidate}")
                    included_vmt_dest = (
                        dest_vmt.parent / "shared" / candidate.name
                        if localize_data else None
                    )
                    included_textures, _ = _process_vmt(candidate, included_vmt_dest, nosubfolder=True)
                    break

        # Merge all texture references
        final_textures = {
            **included_textures,
            **structure.get("insert_textures", {}),
            **structure.get("replace_textures", {}),
        }
        if not structure["is_patch"]:
            final_textures.update(structure.get("textures", {}))

        # Copy associated textures
        for key, tex_rel in final_textures.items():
            tex_file = find_texture(tex_rel)
            if not tex_file:
                continue
            dest_tex = localize_vtf(tex_file, dest_vmt, nosubfolder)
            dest_tex.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tex_file, dest_tex)
            copied_files.append(dest_tex)
            logger and logger.debug(f"Copied texture: {dest_tex.relative_to(export_dir)}")

        # Rewrite texture/include paths
        if localize_data:
            with open(vmt_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            def rewrite_path(new_path: Path, base: Path, material_root: bool = True, keep_suffix: bool = False) -> str:
                """
                Generate a relative, normalized material path.
                
                - material_root=True → relative to 'materials'
                - keep_suffix=True → keeps file extension (for includes)
                """
                root = export_dir / "materials" if material_root else export_dir
                rel = new_path.relative_to(root)
                return rel.as_posix() if keep_suffix else rel.with_suffix("").as_posix()

            new_lines = []
            for line in lines:
                modified = line

                # Handle include rewrites
                if structure["is_patch"] and included_vmt_dest:
                    m = re.match(r'(\s*)include\s+"([^"]+)"', line, flags=re.IGNORECASE)
                    if m:
                        leading_ws = m.group(1)
                        new_include_rel = rewrite_path(included_vmt_dest, export_dir, material_root=False, keep_suffix=True)
                        modified = f'{leading_ws}include "{new_include_rel}"\n'
                        logger and logger.debug(f"Rewrote include path to: {new_include_rel}")

                # Handle texture rewrites
                rewrite_targets = (
                    structure["replace_textures"] | structure["insert_textures"]
                    if structure["is_patch"] else structure.get("textures", {})
                )
                for key, tex_rel in rewrite_targets.items():
                    if key not in line.lower():
                        continue
                    tex_file = find_texture(tex_rel)
                    if not tex_file:
                        continue
                    dest_tex = localize_vtf(tex_file, dest_vmt)
                    new_tex_rel = rewrite_path(dest_tex, export_dir)
                    leading_ws = re.match(r"^\s*", line).group(0)
                    modified = f'{leading_ws}{key} "{new_tex_rel}"\n'
                    logger and logger.debug(f"Rewrote texture path for {key} to: {new_tex_rel}")
                    break

                new_lines.append(modified)

            with open(dest_vmt, "w", encoding="utf-8") as f_out:
                f_out.writelines(new_lines)

        return final_textures, dest_vmt

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
        "-resize",
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

    args += ["-alphaformat", alpha_fmt or fmt]

    if silent:
        args.append("-silent")

    if flags:
        for f in flags:
            f_clean = str(f).strip().upper()
            if f_clean:
                args += ["-flag", f_clean]
                if f_clean == "NOMIP":
                    nomipmaps = True

    if nomipmaps:
        args.append("-nomipmaps")

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

    if gamma_correction is not None:
        args += ["-gamma", "-gcorrection", str(gamma_correction)]

    if extra_args:
        args.extend(extra_args)

    try:
        subprocess.run(args, check=True)
    except subprocess.CalledProcessError:
        print(f"[ERROR] VTF conversion failed: {src_path} -> {dst_path}")
        raise

    converted = dst_path.parent / (src_path.stem + ".vtf")
    if converted != dst_path:
        if dst_path.exists():
            dst_path.unlink()
        converted.rename(dst_path)

    return dst_path