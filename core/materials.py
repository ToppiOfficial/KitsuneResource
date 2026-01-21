from pathlib import Path
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Set, Tuple
from utils import (
    Logger, TEXTURE_KEYS
)

def find_material_vmt(material_name: str, search_paths: List[Path]) -> Optional[Path]:
    relative_vmt = Path("materials") / Path(material_name + ".vmt")
    for root in search_paths:
        candidate = (root / relative_vmt).resolve()
        if candidate.exists():
            return candidate
    return None

def map_materials_to_vmt(
    materials_list: List[str], search_paths: List[Path], logger: Optional[Logger] = None
) -> Dict[str, Path]:
    result = {}
    for mat in materials_list:
        vmt = find_material_vmt(mat, search_paths)
        if vmt:
            result[mat] = vmt
        elif logger:
            logger.warn(f"Material not found: {mat}")
    return result

def parse_vmt_structure(vmt_path: Path, logger: Optional[Logger] = None) -> dict:
    if not vmt_path.exists():
        return {"is_patch": False, "textures": {}}
    
    logger and logger.debug(f"Parsing VMT: {vmt_path}")
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
    logger and logger.debug(f"VMT is_patch: {is_patch}")
    include_path = None
    replace_textures = {}
    insert_textures = {}
    regular_textures = {}
    
    if is_patch:
        include_match = re.search(r'include\s+"([^"]+)"', content, flags=re.IGNORECASE)
        if include_match:
            include_path = include_match.group(1).replace("\\", "/")
            logger and logger.debug(f"Found include path: {include_path}")
        
        replace_match = re.search(r'replace\s*\{([^}]*)\}', content, flags=re.IGNORECASE | re.DOTALL)
        if replace_match:
            block_content = replace_match.group(1)
            for match in re.finditer(r'(\$\w+)\s+"([^"]+)"', block_content, flags=re.IGNORECASE):
                key, value = match.groups()
                key_lower = key.lower()
                if key_lower in lowercase_keys:
                    replace_textures[key_lower] = Path(value.replace("\\", "/"))
            logger and logger.debug(f"Found replace textures: {replace_textures}")

        insert_match = re.search(r'insert\s*\{([^}]*)\}', content, flags=re.IGNORECASE | re.DOTALL)
        if insert_match:
            block_content = insert_match.group(1)
            for match in re.finditer(r'(\$\w+)\s+"([^"]+)"', block_content, flags=re.IGNORECASE):
                key, value = match.groups()
                key_lower = key.lower()
                if key_lower in lowercase_keys:
                    insert_textures[key_lower] = Path(value.replace("\\", "/"))
            logger and logger.debug(f"Found insert textures: {insert_textures}")

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
        logger and logger.debug(f"Found regular textures: {regular_textures}")
    
    return {
        "is_patch": is_patch,
        "include_path": include_path,
        "replace_textures": replace_textures,
        "insert_textures": insert_textures,
        "textures": regular_textures
    }

def parse_vmt_textures(vmt_path: Path, logger: Optional[Logger] = None) -> Dict[str, Path]:
    structure = parse_vmt_structure(vmt_path, logger)
    if structure["is_patch"]:
        all_textures = {}
        all_textures.update(structure["replace_textures"])
        all_textures.update(structure["insert_textures"])
        return all_textures
    return structure["textures"]


class MaterialCopyContext:
    def __init__(self, export_dir: Path, search_paths: List[Path], localize_data: bool, logger: Optional[Logger]):
        self.export_dir = export_dir
        self.search_paths = search_paths
        self.localize_data = localize_data
        self.logger = logger
        
        self.copied_files: List[Path] = []
        self.processed_vmts: Dict[Path, Path] = {}
        self.texture_cache: Dict[str, Path] = {}
    
    def relative_to_materials_root(self, path: Path) -> Path:
        try:
            idx = path.parts.index("materials")
            return Path(*path.parts[idx:])
        except ValueError:
            return Path("materials") / path.name
    
    def find_texture(self, tex_rel) -> Optional[Path]:
        tex_rel_str = str(tex_rel).lower()
        if tex_rel_str in self.texture_cache:
            return self.texture_cache[tex_rel_str]
        
        for root in self.search_paths:
            candidate = (root / "materials" / tex_rel).with_suffix(".vtf")
            if candidate.exists():
                self.texture_cache[tex_rel_str] = candidate
                return candidate
        return None
    
    def localize_vtf(self, vtf_path: Path, dest_vmt_path: Path, nosubfolder: bool = False) -> Path:
        if not self.localize_data:
            return self.export_dir / self.relative_to_materials_root(vtf_path)
        
        vtf_rel = self.relative_to_materials_root(vtf_path)
        vmt_rel = dest_vmt_path.relative_to(self.export_dir)
        
        if vtf_rel.parent == vmt_rel.parent or nosubfolder:
            return dest_vmt_path.parent / vtf_path.name
        
        dest = dest_vmt_path.parent
        if dest.name != "shared":
            dest = dest / "shared"
        return dest / vtf_path.name


class VMTProcessor:
    def __init__(self, ctx: MaterialCopyContext):
        self.ctx = ctx
    
    def process_vmt(self, vmt_path: Path, dest_vmt: Optional[Path] = None, nosubfolder: bool = False, copy_textures: bool = True) -> Tuple[Dict[str, Path], Optional[Path]]:
        if vmt_path in self.ctx.processed_vmts:
            self.ctx.logger and self.ctx.logger.debug(f"Skipping already processed VMT: {vmt_path}")
            return {}, self.ctx.processed_vmts[vmt_path]
        
        if not vmt_path.exists():
            self.ctx.logger and self.ctx.logger.warn(f"Missing VMT: {vmt_path}")
            return {}, None
        
        dest_vmt = dest_vmt or (self.ctx.export_dir / self.ctx.relative_to_materials_root(vmt_path))
        self.ctx.processed_vmts[vmt_path] = dest_vmt
        
        self._copy_vmt_file(vmt_path, dest_vmt)
        structure = parse_vmt_structure(vmt_path, self.ctx.logger)
        
        included_textures, included_vmt_dest = self._process_include(structure, dest_vmt, nosubfolder)
        final_textures = self._merge_textures(structure, included_textures)
        
        if copy_textures:
            self._copy_referenced_textures(final_textures, dest_vmt, nosubfolder)
        
        if self.ctx.localize_data:
            self._rewrite_vmt_paths(vmt_path, dest_vmt, structure, included_vmt_dest, final_textures)
        
        return final_textures, dest_vmt
    
    def _copy_vmt_file(self, vmt_path: Path, dest_vmt: Path):
        dest_vmt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vmt_path, dest_vmt)
        self.ctx.copied_files.append(dest_vmt)
        self.ctx.logger and self.ctx.logger.info(f"Copied VMT: {dest_vmt.relative_to(self.ctx.export_dir)}")
    
    def _process_include(self, structure: dict, dest_vmt: Path, nosubfolder: bool) -> Tuple[Dict[str, Path], Optional[Path]]:
        if not structure["is_patch"] or not structure["include_path"]:
            return {}, None
        
        include_path = structure["include_path"]
        for root in self.ctx.search_paths:
            candidate = (root / include_path).with_suffix(".vmt")
            if candidate.exists():
                self.ctx.logger and self.ctx.logger.debug(f"Found included VMT: {candidate}")
                included_vmt_dest = (dest_vmt.parent / "shared" / candidate.name 
                                   if self.ctx.localize_data else None)
                included_textures, _ = self.process_vmt(candidate, included_vmt_dest, nosubfolder=True, copy_textures=False)
                return included_textures, included_vmt_dest
        
        self.ctx.logger and self.ctx.logger.warn(f"Included VMT not found: {include_path}")
        return {}, None
    
    def _merge_textures(self, structure: dict, included_textures: Dict[str, Path]) -> Dict[str, Path]:
        self.ctx.logger and self.ctx.logger.debug(f"Merging textures. Included: {included_textures}, Current: {structure}")
        final_textures = {**included_textures}
        final_textures.update(structure.get("insert_textures", {}))
        final_textures.update(structure.get("replace_textures", {}))
        
        if not structure["is_patch"]:
            final_textures.update(structure.get("textures", {}))
        
        self.ctx.logger and self.ctx.logger.debug(f"Final merged textures: {final_textures}")
        return final_textures
    
    def _copy_referenced_textures(self, textures: Dict[str, Path], dest_vmt: Path, nosubfolder: bool):
        for key, tex_rel in textures.items():
            tex_file = self.ctx.find_texture(tex_rel)
            if not tex_file:
                self.ctx.logger and self.ctx.logger.warn(f'Texture file not found for "{key}" -> {tex_rel}')
                continue
            
            dest_tex = self.ctx.localize_vtf(tex_file, dest_vmt, nosubfolder)
            dest_tex.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tex_file, dest_tex)
            self.ctx.copied_files.append(dest_tex)
            self.ctx.logger and self.ctx.logger.debug(f"Copied texture: {dest_tex.relative_to(self.ctx.export_dir)}")
    
    def _rewrite_vmt_paths(self, vmt_path: Path, dest_vmt: Path, structure: dict, 
                          included_vmt_dest: Optional[Path], textures: Dict[str, Path]):
        with open(vmt_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        
        rewriter = VMTPathRewriter(self.ctx, dest_vmt, structure, included_vmt_dest, textures)
        new_lines = [rewriter.rewrite_line(line) for line in lines]
        
        with open(dest_vmt, "w", encoding="utf-8") as f_out:
            f_out.writelines(new_lines)


class VMTPathRewriter:
    def __init__(self, ctx: MaterialCopyContext, dest_vmt: Path, structure: dict,
                 included_vmt_dest: Optional[Path], textures: Dict[str, Path]):
        self.ctx = ctx
        self.dest_vmt = dest_vmt
        self.structure = structure
        self.included_vmt_dest = included_vmt_dest
        self.textures = textures
        self.ctx.logger and self.ctx.logger.debug(
            f"VMTPathRewriter initialized for {dest_vmt.name}:\n"
            f"  structure: {structure}\n"
            f"  included_vmt_dest: {included_vmt_dest}\n"
            f"  textures: {textures}"
        )

    def rewrite_line(self, line: str) -> str:
        self.ctx.logger and self.ctx.logger.debug(f"Rewriting line: {line.strip()}")
        
        stripped = line.strip()
        if stripped.startswith("//"):
            return line

        if self._should_rewrite_include(line):
            return self._rewrite_include_line(line)
        
        texture_rewrite = self._try_rewrite_texture(line)
        if texture_rewrite:
            return texture_rewrite
        
        return line
    
    def _should_rewrite_include(self, line: str) -> bool:
        return (self.structure["is_patch"] and 
                self.included_vmt_dest and 
                re.match(r'\s*include\s+"', line, flags=re.IGNORECASE))
    
    def _rewrite_include_line(self, line: str) -> str:
        m = re.match(r'(\s*)include\s+"([^"]+)"', line, flags=re.IGNORECASE)
        if m:
            leading_ws = m.group(1)
            new_include_rel = self._get_relative_path(self.included_vmt_dest, 
                                                      self.ctx.export_dir, 
                                                      material_root=False, 
                                                      keep_suffix=True)
            self.ctx.logger and self.ctx.logger.debug(f"Rewrote include path to: {new_include_rel}")
            return f'{leading_ws}include "{new_include_rel}"\n'
        return line
    
    def _try_rewrite_texture(self, line: str) -> Optional[str]:
        rewrite_targets = (self.structure["replace_textures"] | self.structure["insert_textures"]
                          if self.structure["is_patch"] else self.structure.get("textures", {}))
        
        for key, tex_rel in rewrite_targets.items():
            if key not in line.lower():
                continue
            
            tex_file = self.ctx.find_texture(tex_rel)
            if not tex_file:
                continue
            
            dest_tex = self.ctx.localize_vtf(tex_file, self.dest_vmt)
            new_tex_rel = self._get_relative_path(dest_tex, self.ctx.export_dir)
            leading_ws = re.match(r"^\s*", line).group(0)
            
            self.ctx.logger and self.ctx.logger.debug(f"Rewrote texture path for {key} to: {new_tex_rel}")
            return f'{leading_ws}{key} "{new_tex_rel}"\n'
        
        return None
    
    def _get_relative_path(self, new_path: Path, base: Path, 
                          material_root: bool = True, keep_suffix: bool = False) -> str:
        root = self.ctx.export_dir / "materials" if material_root else self.ctx.export_dir
        rel = new_path.relative_to(root)
        return rel.as_posix() if keep_suffix else rel.with_suffix("").as_posix()


def copy_materials(
    material_to_vmt: Dict[str, Path],
    export_dir: Path,
    search_paths: List[Path],
    localize_data: bool = True,
    logger: Optional[Logger] = None
) -> List[Path]:
    if not material_to_vmt:
        logger and logger.warn("No materials to copy.")
        return []
    
    ctx = MaterialCopyContext(export_dir, search_paths, localize_data, logger)
    processor = VMTProcessor(ctx)
    
    for vmt_path in material_to_vmt.values():
        processor.process_vmt(vmt_path)
    
    return ctx.copied_files


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