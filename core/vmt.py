from pathlib import Path
from typing import Optional

from utils import (
    Logger, resolve_json_path
)

class VMTCreator:
    """Creates VMT files from templates"""
    
    @staticmethod
    def create_from_template(vmt_template_json, vtf_path: Path, compile_root: Path, 
                           args, logger: Logger, include_dirs: list = None):
        vmt_template = VMTCreator._resolve_template_path(
            vmt_template_json, args.config_path, args.basedir, include_dirs
        )
        
        if vmt_template is None:
            logger.warn(f"VMT template not found in project or includedirs, skipping: {vmt_template_json}")
            return
        
        vmt_dst = vtf_path.with_suffix(".vmt")
        vtf_rel_posix = VMTCreator._get_relative_path(
            vtf_path, compile_root, single_addon=getattr(args, 'single_addon', False)
        )
        
        template_content = vmt_template.read_text(encoding="utf-8")
        processed_content = VMTCreator._process_template(template_content, vtf_rel_posix)
        
        vmt_dst.write_text(processed_content, encoding="utf-8")
        logger.info(f"VMT created: {vmt_dst.relative_to(compile_root)}")
    
    @staticmethod
    def _resolve_template_path(vmt_template_json, config_path, basedir, 
                                include_dirs: Optional[list]) -> Optional[Path]:
        primary = resolve_json_path(vmt_template_json, config_path, basedir)
        if primary.exists():
            return primary
        
        for inc_dir in (include_dirs or []):
            candidate = Path(inc_dir) / vmt_template_json
            if candidate.exists():
                return candidate
        
        return None

    @staticmethod
    def _get_relative_path(vtf_path: Path, compile_root: Path, single_addon: bool = False) -> str:
        if single_addon:
            materials_root = compile_root
        else:
            materials_root = compile_root / "SharedAssets"
        
        try:
            vtf_rel = vtf_path.relative_to(materials_root).with_suffix("")
            vtf_rel_posix = vtf_rel.as_posix()
            if vtf_rel_posix.startswith("materials/"):
                vtf_rel_posix = vtf_rel_posix[len("materials/"):]
            return vtf_rel_posix
        except ValueError:
            pass
        
        # Fallback: find 'materials/' segment anywhere in the path
        parts = vtf_path.with_suffix("").parts
        for i, part in enumerate(parts):
            if part.lower() == "materials":
                return "/".join(parts[i+1:])
        
        return vtf_path.stem
    
    @staticmethod
    def _process_template(content: str, texture_path: str) -> str:
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            
            if stripped.startswith("$basetexture") or stripped.startswith('"$basetexture"'):
                leading_ws = line[:len(line) - len(line.lstrip())]
                lines.append(f'{leading_ws}$basetexture "{texture_path}"')
            else:
                lines.append(line)
        
        return "\n".join(lines)