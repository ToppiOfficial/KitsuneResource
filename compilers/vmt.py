from pathlib import Path

from utils import (
    Logger, resolve_json_path
)

class VMTCreator:
    """Creates VMT files from templates"""
    
    @staticmethod
    def create_from_template(vmt_template_json, vtf_path: Path, compile_root: Path, 
                           args, logger: Logger):
        vmt_template = resolve_json_path(vmt_template_json, args.config, args.dir)
        if not vmt_template.exists():
            logger.warn(f"VMT template not found, skipping: {vmt_template}")
            return
        
        vmt_dst = vtf_path.with_suffix(".vmt")
        vtf_rel_posix = VMTCreator._get_relative_path(vtf_path, compile_root)
        
        template_content = vmt_template.read_text(encoding="utf-8")
        processed_content = VMTCreator._process_template(template_content, vtf_rel_posix)
        
        vmt_dst.write_text(processed_content, encoding="utf-8")
        logger.info(f"VMT created: {vmt_dst.relative_to(compile_root)}")
    
    @staticmethod
    def _get_relative_path(vtf_path: Path, materials_root: Path) -> str:
        materials_root = materials_root / "AssetShared"
        try:
            vtf_rel = vtf_path.relative_to(materials_root).with_suffix("")
            vtf_rel_posix = vtf_rel.as_posix()
            if vtf_rel_posix.startswith("materials/"):
                vtf_rel_posix = vtf_rel_posix[len("materials/"):]
            return vtf_rel_posix
        except ValueError:
            return vtf_path.stem
    
    @staticmethod
    def _process_template(content: str, texture_path: str) -> str:
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            
            if stripped.startswith("$basetexture"):
                leading_ws = line[:line.index("$basetexture")]
                lines.append(f'{leading_ws}$basetexture "{texture_path}"')
            elif stripped.startswith('"$'):
                first_space = line.find(' ')
                key = line[:first_space].replace('"', '')
                rest = line[first_space+1:]
                lines.append(f'{key} {rest}')
            else:
                lines.append(line)
        
        return "\n".join(lines)