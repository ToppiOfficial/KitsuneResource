import argparse
import shutil
import os
import re
from pathlib import Path
from datetime import datetime
from typing import List, Set, Optional

import send2trash

from utils import (
    Logger, PrefixedLogger, PathResolver, timer, print_header, parse_config_json,
    resolve_json_path, DEFAULT_COMPILE_ROOT, SUPPORTED_TEXT_FORMAT,
    SUPPORTED_IMAGE_FORMAT
)

from compilers.materials import (
    export_vtf, copy_materials, map_materials_to_vmt
)

from compilers.model import model_compile_studiomdl
from compilers.gameinfo import get_game_search_paths
from compilers.vpk import package_vpk
from compilers.image import convert_image
from compilers.qc import qc_read_materials
from compilers.vmt import VMTCreator

class CompileFolderManager:
    """Manages compile folder cleanup and archiving"""
    
    @staticmethod
    def clean(compile_root: Path, logger: Logger, archived: bool = False):
        os_logger = PrefixedLogger(logger, "OS")
        
        if not compile_root.exists() or not any(compile_root.iterdir()):
            os_logger.info("No existing compile folder to clean.")
            return
        
        if archived:
            CompileFolderManager._archive(compile_root, os_logger)
        else:
            CompileFolderManager._trash(compile_root, os_logger)
    
    @staticmethod
    def _archive(compile_root: Path, logger: Logger):
        try:
            archive_dir = compile_root.parent / "_archive"
            archive_dir.mkdir(exist_ok=True)
            
            created_time = datetime.fromtimestamp(compile_root.stat().st_ctime)
            timestamp = created_time.strftime("%Y-%m-%d_%H-%M-%S")
            archive_target = archive_dir / f"{compile_root.name}_{timestamp}"
            
            shutil.move(str(compile_root), str(archive_target))
            logger.info(f"Archived compile folder to: {archive_target}")
        except Exception as e:
            logger.warn(f"Failed to archive compile folder: {e}")
    
    @staticmethod
    def _trash(compile_root: Path, logger: Logger):
        logger.info("Cleaning existing compile folder...")
        for item in compile_root.iterdir():
            try:
                send2trash.send2trash(item)
                logger.info(f"Sent to Recycle Bin: {item.relative_to(compile_root)}")
            except Exception as e:
                logger.warn(f"Failed to remove {item}: {e}")

class DataProcessor:
    """Processes various data items (files, textures, conversions)"""
    
    def __init__(self, compile_root: Path, vtfcmd_exe: Optional[Path], args, logger: Logger):
        self.compile_root = compile_root
        self.vtfcmd_exe = vtfcmd_exe
        self.args = args
        self.logger = PrefixedLogger(logger, "DATA")
    
    def process_items(self, items: list, base_output: Path):
        for item in items:
            try:
                self._process_single_item(item, base_output)
            except Exception as e:
                self.logger.error(f"Failed to process item: {e}")
    
    def _process_single_item(self, item: dict, base_output: Path):
        input_path = resolve_json_path(item.get("input"), self.args.config, self.args.dir)
        output_path = base_output / Path(item.get("output"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        input_str = item.get("input").strip()
        output_str = item.get("output").strip()
        
        if self._handle_text_replacement(item, input_path, output_path, input_str, output_str):
            return
        if self._handle_vtf_export(item, input_path, output_path, input_str, output_str):
            return
        if self._handle_image_conversion(input_path, output_path, input_str, output_str):
            return
        
        self._copy_file(input_path, output_path)
    
    def _handle_text_replacement(self, item: dict, input_path: Path, output_path: Path,
                                input_str: str, output_str: str) -> bool:
        if not (input_str.endswith(SUPPORTED_TEXT_FORMAT) and 
                output_str.endswith(SUPPORTED_TEXT_FORMAT)):
            return False
        
        replace_map = item.get("replace")
        if not replace_map:
            return False
        
        try:
            text = input_path.read_text(encoding="utf-8")
            for k, v in replace_map.items():
                text = text.replace(k, v)
            output_path.write_text(text, encoding="utf-8")
            self.logger.info(f"Replaced strings: {input_path.name} -> {output_path.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed string replace: {input_path} -> {output_path} | {e}")
            return True
    
    def _handle_vtf_export(self, item: dict, input_path: Path, output_path: Path,
                          input_str: str, output_str: str) -> bool:
        if not ((input_str.endswith(SUPPORTED_IMAGE_FORMAT) or input_str.endswith('.vtf')) 
                and output_str.endswith(".vtf")):
            return False
        
        vtf_data = item.get("vtf")
        
        if not input_str.endswith(".vtf") and self.vtfcmd_exe:
            try:
                export_vtf(
                    src_path=input_path,
                    dst_path=output_path,
                    vtfcmd=self.vtfcmd_exe,
                    flags=vtf_data.get("flags", []) if vtf_data else [],
                    extra_args=vtf_data.get("encoder_args", []) if vtf_data else [],
                    silent=True
                )
                self.logger.info(f"VTF export: {input_path.name} -> {output_path.name}")
            except Exception as e:
                self.logger.error(f"Failed to export VTF: {input_path} -> {output_path} | {e}")
        
        if vtf_data and vtf_data.get("vmt"):
            VMTCreator.create_from_template(vtf_data["vmt"], output_path, 
                                          self.compile_root, self.args, self.logger)
        return True
    
    def _handle_image_conversion(self, input_path: Path, output_path: Path,
                                input_str: str, output_str: str) -> bool:
        if not (input_str.endswith(SUPPORTED_IMAGE_FORMAT) and 
                output_str.endswith(SUPPORTED_IMAGE_FORMAT)):
            return False
        
        try:
            if convert_image(input_path, output_path):
                self.logger.info(f"Converted image: {input_path.name} -> {output_path.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to convert: {input_path} -> {output_path} | {e}")
            return True
    
    def _copy_file(self, input_path: Path, output_path: Path):
        shutil.copy2(input_path, output_path)
        self.logger.info(f"Copied file: {input_path.name} -> {output_path.name}")

class ModelCompiler:
    """Handles model compilation and material processing"""
    
    def __init__(self, studiomdl_exe: Path, search_paths: List[Path], 
                 vtfcmd_exe: Optional[Path], gameinfo_dir: Path, args, logger: Logger):
        self.studiomdl_exe = studiomdl_exe
        self.search_paths = search_paths
        self.vtfcmd_exe = vtfcmd_exe
        self.gameinfo_dir = gameinfo_dir
        self.args = args
        self.logger = logger
    
    def compile_model(self, model_name: str, model_data: dict, compile_root: Path):
        model_logger = PrefixedLogger(self.logger, "MODEL")
        
        if not model_data.get('compile', True):
            model_logger.warn(f"Skipping model {model_name} (compile=false)")
            return
        
        qc_path = Path(model_data.get("qc")).resolve()
        if not qc_path.exists():
            model_logger.error(f"QC file not found: {qc_path}")
            return
        
        if self.args.game:
            output_dir = None
            game_dir = self.gameinfo_dir
            model_logger.info(f"Compiling model {qc_path.name} directly to game directory")
        else:
            output_dir = compile_root / model_name
            output_dir.mkdir(parents=True, exist_ok=True)
            game_dir = self.gameinfo_dir
            model_logger.info(f"Compiling model {qc_path.name}")
        
        success, compiled_files, dumped_materials = model_compile_studiomdl(
            studiomdl_exe=self.studiomdl_exe,
            qc_file=qc_path,
            output_dir=output_dir,
            game_dir=game_dir,
            verbose=self.args.verbose,
            logger=self.logger,
        )
        
        if not success:
            model_logger.error("Main QC compilation failed.")
            return
        
        dumped_materials = set(dumped_materials)
        model_logger.info(f"Compiled {qc_path.name} ({len(dumped_materials)} materials)")
        
        self._compile_submodels(model_data, qc_path, output_dir, dumped_materials, model_logger, game_dir=self.gameinfo_dir)
        
        if self.args.game:
            model_logger.info("--game mode: Skipping material copy and subdata processing")
            return
        
        self._process_materials(qc_path, dumped_materials, output_dir, compile_root, model_logger)
        self._process_subdata(model_data, output_dir, compile_root)
    
    def _compile_submodels(self, model_data: dict, qc_path: Path, output_dir: Optional[Path],
                          dumped_materials: Set, logger: Logger, game_dir : Path):
        for sub_name, sub_qc in model_data.get("submodels", {}).items():
            sub_qc_path = Path(sub_qc)
            if not sub_qc_path.is_absolute():
                sub_qc_path = qc_path.parent / sub_qc
            sub_qc_path = sub_qc_path.resolve()
            
            if not sub_qc_path.exists():
                logger.error(f"Sub-QC not found: {sub_qc_path}")
                continue
            
            logger.info(f"Compiling sub-QC: {sub_qc_path.name}")
            
            game_dir = game_dir
            
            success, _, sub_dumped = model_compile_studiomdl(
                studiomdl_exe=self.studiomdl_exe,
                qc_file=sub_qc_path,
                output_dir=output_dir,
                game_dir=game_dir,
                verbose=self.args.verbose,
                logger=self.logger,
            )
            
            if success:
                dumped_materials.update(set(sub_dumped))
                logger.info(f"Compiled {sub_qc_path.name} ({len(sub_dumped)} materials)")
    
    def _process_materials(self, qc_path: Path, dumped_materials: Set, 
                          output_dir: Path, compile_root: Path, logger: Logger):
        if self.args.nomaterial:
            logger.warn("Skipping model material copying (-nomaterial)")
            return
        
        mat_logger = PrefixedLogger(self.logger, "MATERIAL")
        copy_target = (compile_root / "Assetshared" if self.args.sharedmaterials 
                      else output_dir)
        copy_target.mkdir(parents=True, exist_ok=True)
        
        qc_material_paths = qc_read_materials(qc_path, dumped_materials)
        mat_logger.info(f"Found {len(dumped_materials)} cdmaterials paths from Compile")
        
        for mat in qc_material_paths:
            mat_logger.debug(mat)
        
        mat_logger.info(f"Copying {len(dumped_materials)} materials to {copy_target}...")
        material_to_vmt = map_materials_to_vmt(qc_material_paths, self.search_paths)
        copied_files = copy_materials(
            material_to_vmt,
            copy_target,
            self.search_paths,
            localize_data=not self.args.nolocalize,
            logger=mat_logger,
        )
        mat_logger.info(f"Material copy complete ({len(copied_files)} files).")
    
    def _process_subdata(self, model_data: dict, output_dir: Path, compile_root: Path):
        subdata = model_data.get("subdata", [])
        if subdata:
            processor = DataProcessor(compile_root, self.vtfcmd_exe, self.args, self.logger)
            processor.process_items(subdata, output_dir)

class MaterialSetCopier:
    """Copies material sets"""
    
    @staticmethod
    def copy_set(set_name: str, set_data: dict, compile_root: Path, 
                 search_paths: List[Path], logger: Logger):
        mat_logger = PrefixedLogger(logger, "MATERIAL")
        vmt_list = set_data.get("materials", [])
        
        if not vmt_list:
            mat_logger.warn(f"[{set_name}] No materials listed — skipping.")
            return
        
        mat_logger.info(f"[{set_name}] Copying material set...")
        output_dir = compile_root / set_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        material_to_vmt = {Path(vmt): Path(vmt) for vmt in vmt_list}
        copied_files = copy_materials(
            material_to_vmt,
            output_dir,
            search_paths,
            localize_data=True,
            logger=mat_logger,
        )
        
        mat_logger.info(f"Material-only copy complete ({len(copied_files)} files).")

class ValveModelPipeline:
    """Pipeline for ValveModel header"""
    
    def __init__(self, config: dict, args, logger: Logger):
        self.config = config
        self.args = args
        self.logger = logger
    
    def execute(self):
        studiomdl_exe, gameinfo_path, vtfcmd_exe, vpk_exe = PathResolver.resolve_and_validate(
            self.config, "studiomdl", "gameinfo", "vtfcmd", "vpk"
        )
        
        if not studiomdl_exe or not gameinfo_path:
            self.logger.error("Config missing required fields (studiomdl/gameinfo)")
            return
        
        gameinfo_dir = gameinfo_path.parent
        compile_root = Path(self.args.exportdir or DEFAULT_COMPILE_ROOT).resolve()
        
        if self.args.exportdir is None:
            self.logger.warn(f"--exportdir not provided, using default: {compile_root}")
        
        if self.args.game:
            self.logger.info("--game mode enabled: Compiling models directly to game directory")
            self.logger.info(f"Game directory: {gameinfo_dir}")
            self.logger.info("Materials, data sections, and VPK packaging will be skipped")
        else:
            CompileFolderManager.clean(compile_root, self.logger, self.args.archive)
        
        search_paths = get_game_search_paths(gameinfo_path)
        self.logger.info("Game search paths:")
        for p in search_paths:
            self.logger.info(f"\t{p}")
        
        if vtfcmd_exe:
            self.logger.info(f"VTF conversion enabled: {vtfcmd_exe}")
        
        self._compile_models(compile_root, studiomdl_exe, search_paths, 
                           vtfcmd_exe, gameinfo_dir)
        
        if self.args.game:
            self.logger.info("--game mode: Compilation complete. Skipping post-processing")
            return
        
        self._process_material_sets(compile_root, search_paths)
        self._process_data_sections(compile_root, vtfcmd_exe)
        
        if self.args.vpk:
            self._package_vpks(compile_root, vpk_exe)
    
    def _compile_models(self, compile_root: Path, studiomdl_exe: Path, 
                       search_paths: List[Path], vtfcmd_exe: Optional[Path], 
                       gameinfo_dir: Path):
        compiler = ModelCompiler(studiomdl_exe, search_paths, vtfcmd_exe, 
                                gameinfo_dir, self.args, self.logger)
        
        for model_name, model_data in self.config.get("model", {}).items():
            compiler.compile_model(model_name, model_data, compile_root)
    
    def _process_material_sets(self, compile_root: Path, search_paths: List[Path]):
        for set_name, set_data in self.config.get("material", {}).items():
            MaterialSetCopier.copy_set(set_name, set_data, compile_root, 
                                      search_paths, self.logger)
    
    def _process_data_sections(self, compile_root: Path, vtfcmd_exe: Optional[Path]):
        processor = DataProcessor(compile_root, vtfcmd_exe, self.args, self.logger)
        for folder_name, items in self.config.get("data", {}).items():
            processor.process_items(items, compile_root / folder_name)
    
    def _package_vpks(self, compile_root: Path, vpk_exe: Optional[Path]):
        if not vpk_exe:
            self.logger.warn("vpk.exe not found or missing in config, skipping VPK packaging")
            return
        
        for subfolder in compile_root.iterdir():
            if subfolder.is_dir():
                package_vpk(vpk_exe, subfolder, self.logger)

class ValveTexturePipeline:
    """Pipeline for ValveTexture header"""
    
    def __init__(self, config: dict, args, logger: Logger):
        self.config = config
        self.args = args
        self.logger = logger
        self.processed_files: Set[Path] = set()
    
    def execute(self):
        vtfcmd, = PathResolver.resolve_and_validate(self.config, "vtfcmd")
        
        if not vtfcmd:
            self.logger.error(f"vtfcmd not found in config")
            return
        
        vtf_config = self.config.get("vtf", {})
        if not vtf_config:
            self.logger.warn("No 'vtf' section found in config — nothing to process")
            return
        
        try:
            root_dir = PathResolver.get_root_dir(self.args, Path(self.args.config).resolve())
            if getattr(self.args, "dir", None):
                self.logger.info(f"Overriding input/output root with --dir: {root_dir}")
        except ValueError as e:
            self.logger.error(str(e))
            return
        
        for key, entry in vtf_config.items():
            self._process_texture_group(key, entry, root_dir, vtfcmd)
    
    def _process_texture_group(self, key: str, entry: dict, root_dir: Path, vtfcmd: Path):
        self.logger.info(f"Processing texture group: {key}")
        
        input_pattern = entry.get("input")
        if not input_pattern:
            self.logger.warn(f"Skipped {key} — missing 'input'")
            return
        
        matching_files = self._find_matching_files(input_pattern, root_dir)
        if not matching_files:
            self.logger.warn(f"No matching file(s) found for pattern: {input_pattern}")
            return
        
        for src_file in matching_files:
            self._process_texture_file(src_file, entry, root_dir, vtfcmd)
    
    def _find_matching_files(self, pattern: str, root_dir: Path) -> List[Path]:
        if "*" in pattern or re.search(r"[.*+?^${}()|\[\]\\]", pattern):
            regex = re.compile(pattern)
            recursive = getattr(self.args, "recursive", False)
            
            if recursive:
                return [f.resolve() for f in root_dir.rglob("*") 
                       if f.is_file() and regex.search(f.name)]
            else:
                return [f.resolve() for f in root_dir.glob("*") 
                       if f.is_file() and regex.search(f.name)]
        
        input_path = root_dir / pattern
        return [input_path] if input_path.exists() else []
    
    def _process_texture_file(self, src_file: Path, entry: dict, 
                             root_dir: Path, vtfcmd: Path):
        src_file_resolved = src_file.resolve()
        
        if (not getattr(self.args, "allow_reprocess", False) and 
            src_file_resolved in self.processed_files):
            self.logger.info(f"Skipping {src_file.name} — already processed")
            return
        
        output_path = self._resolve_output_path(src_file, entry, root_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self._should_skip_conversion(src_file, output_path):
            self.logger.info(f"Skipping {src_file.name} (already up-to-date)")
            self.processed_files.add(src_file_resolved)
            return
        
        self._convert_to_vtf(src_file, output_path, entry, vtfcmd)
        self.processed_files.add(src_file_resolved)
    
    def _resolve_output_path(self, src_file: Path, entry: dict, root_dir: Path) -> Path:
        output_entry = entry.get("output")
        if not output_entry:
            return root_dir / (src_file.stem + ".vtf")
        
        output_resolved = (root_dir / output_entry if not Path(output_entry).is_absolute() 
                          else Path(output_entry))
        
        if output_resolved.suffix == "":
            return output_resolved / (src_file.stem + ".vtf")
        return output_resolved.with_suffix(".vtf")
    
    def _should_skip_conversion(self, src_file: Path, output_path: Path) -> bool:
        if getattr(self.args, "forceupdate", False):
            return False
        
        if not output_path.exists():
            return False
        
        return output_path.stat().st_mtime >= src_file.stat().st_mtime
    
    def _convert_to_vtf(self, src_file: Path, output_path: Path, 
                       entry: dict, vtfcmd: Path):
        vtf_settings = entry.get("vtf", {})
        flags = vtf_settings.get("flags")
        extra_args = vtf_settings.get("encoder_args")
        
        self.logger.info(f"Converting: {src_file.name} -> {output_path.name}")
        try:
            export_vtf(
                src_path=src_file,
                dst_path=output_path,
                vtfcmd=vtfcmd,
                flags=flags,
                extra_args=extra_args,
            )
            os.utime(output_path, (src_file.stat().st_atime, src_file.stat().st_mtime))
            self.logger.debug(f"Finished VTF: {output_path} (mtime synced to source)")
        except Exception as e:
            self.logger.error(f"Failed to export {src_file} → {output_path}: {e}")

@timer
def main():
    print_header()
    
    global_parser = argparse.ArgumentParser(
        description="Source Resource Compiler", 
        add_help=False
    )
    global_parser.add_argument("--config", "-config", required=True, 
                              metavar="CONFIG_JSON",
                              help="Path to config.json file")
    global_parser.add_argument("--log", action="store_true",
                              help="Enable logging to file")
    global_parser.add_argument("--verbose", action="store_true", 
                              help="Enable verbose logging")
    global_parser.add_argument("--dir", type=str,
                              help="Absolute path to override input/output root")
    
    global_args, remaining_argv = global_parser.parse_known_args()
    
    log_file = None
    if global_args.log:
        clean_dir = str(global_args.dir).strip(' "\'') if global_args.dir else None
        config_dir = (Path(clean_dir).resolve() if clean_dir 
                     else Path(global_args.config).resolve().parent)
        
        log_dir = config_dir / "resourcecompiler-log"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = log_dir / f"resourcecompiler-{timestamp}.txt"
    
    logger = Logger(verbose=global_args.verbose, use_color=True, log_file=log_file)
    if log_file:
        logger.info(f"Logging enabled → {log_file}")
    
    try:
        config = parse_config_json(global_args.config)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        return logger
    
    header = config.get("header")
    if not header:
        logger.error("Missing 'header' field in config — cannot determine pipeline type")
        return logger
    
    parser = argparse.ArgumentParser(parents=[global_parser])
    
    if header == "ValveModel":
        parser.add_argument("--exportdir", metavar="COMPILE_DIR", 
                          default=DEFAULT_COMPILE_ROOT,
                          help=f"Root folder for compiled output")
        parser.add_argument("--nomaterial", action="store_true", 
                          help="Skip material mapping/copying")
        parser.add_argument("--nolocalize", action="store_true", 
                          help="Disable material localization")
        parser.add_argument("--sharedmaterials", action="store_true", 
                          help="Copy materials into compile/Assetshared")
        parser.add_argument("--vpk", action="store_true", 
                          help="Package each subfolder into VPK")
        parser.add_argument("--archive", action="store_true", 
                          help="Archive existing files instead of deletion")
        parser.add_argument("--game", action="store_true",
                          help="Compile models directly to game directory (skips materials/data/VPK)")
        
        args = parser.parse_args()
        ValveModelPipeline(config, args, logger).execute()
        
    elif header == 'ValveTexture':
        parser.add_argument("--forceupdate", action="store_true",
                          help="Force reprocessing all textures")
        parser.add_argument("--allow_reprocess", action="store_true",
                          help="Allow same file to be processed multiple times")
        parser.add_argument("--recursive", action="store_true",
                          help="Search for files recursively in subfolders")
        
        args = parser.parse_args()
        ValveTexturePipeline(config, args, logger).execute()
        
    else:
        logger.error(f"Unknown pipeline header: {header}")
        return logger
    
    return logger

if __name__ == "__main__":
    main()