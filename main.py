import sys
import os
from pathlib import Path

def check_and_activate_venv():
    """Check for virtual environment and activate if found"""
    script_dir = Path(__file__).parent.resolve()
    
    venv_names = ['venv', '.venv', 'env', '.env']
    
    for venv_name in venv_names:
        venv_path = script_dir / venv_name
        
        if sys.platform == "win32":
            python_exe = venv_path / "Scripts" / "python.exe"
        else:
            python_exe = venv_path / "bin" / "python"
        
        if venv_path.exists() and python_exe.exists():
            if sys.executable != str(python_exe):
                print(f"Found virtual environment: {venv_name}")
                print(f"Restarting with venv Python: {python_exe}")
                os.execv(str(python_exe), [str(python_exe)] + sys.argv)
            else:
                print(f"Already running in virtual environment: {venv_name}")
            return True
    
    print("No virtual environment found, using global Python")
    return False

check_and_activate_venv()

import argparse, shutil, re
from datetime import datetime
from typing import List, Set, Optional

import send2trash

from utils import (
    Logger, PrefixedLogger, PathResolver, timer, print_header, parse_config_json,
    resolve_json_path, DEFAULT_COMPILE_ROOT, SUPPORTED_TEXT_FORMAT,
    SUPPORTED_IMAGE_FORMAT
)

from core.materials import (
    export_vtf, copy_materials, map_materials_to_vmt
)

from core.model import model_compile_studiomdl
from core.gameinfo import get_game_search_paths
from core.vpk import package_vpk
from core.image import convert_image
from core.qc import qc_read_materials, flatten_qc
from core.vmt import VMTCreator

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
    def _trash(compile_root: Path, logger: Logger, legacy_mode: bool = False):
        """
        Remove compile folder contents. 
        By default removes entire folder at once; legacy_mode removes item by item.
        """
        def _trash_items():
            for item in compile_root.iterdir():
                try:
                    send2trash.send2trash(item)
                    logger.info(f"Sent to Recycle Bin: {item.relative_to(compile_root)}")
                except Exception as e:
                    logger.warn(f"Failed to remove {item}: {e}")
        
        logger.info("Cleaning existing compile folder...")
        
        if legacy_mode:
            _trash_items()
        else:
            try:
                send2trash.send2trash(compile_root)
                logger.info(f"Sent to Recycle Bin: {compile_root.name}")
                compile_root.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to remove compile folder: {e}")
                logger.info("Falling back to item-by-item deletion...")
                _trash_items()

class DataProcessor:
    """Processes various data items (files, textures, conversions)"""
    
    def __init__(self, compile_root: Path, vtfcmd_exe: Optional[Path], args, logger: Logger):
        self.compile_root = compile_root
        self.vtfcmd_exe = vtfcmd_exe
        self.args = args
        self.logger = PrefixedLogger(logger, "DATA")
        self.handlers = [
            self._handle_text_replacement,
            self._handle_vtf_export,
            self._handle_image_conversion,
        ]
    
    def process_items(self, items: list, base_output: Path):
        for item in items:
            try:
                self._process_single_item(item, base_output)
            except Exception as e:
                self.logger.error(f"Failed to process item: {e}")
    
    def _process_single_item(self, item: dict, base_output: Path):
        input_path = resolve_json_path(item.get("input"), self.args.config_path, self.args.dir)
        output_path = base_output / Path(item.get("output"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        input_str = item.get("input").strip()
        output_str = item.get("output").strip()

        for handler in self.handlers:
            if handler(item, input_path, output_path, input_str, output_str):
                return
        
        self._copy_file(input_path, output_path)
    
    def _handle_text_replacement(self, item: dict, input_path: Path, output_path: Path,
                                input_str: str, output_str: str) -> bool:
        if not (input_str.endswith(SUPPORTED_TEXT_FORMAT) and 
                output_str.endswith(SUPPORTED_TEXT_FORMAT)):
            return False
        
        replace_map = item.get("replace")
        if not replace_map:
            # This is not a text replacement task, but another handler might use text files
            # so we don't return True
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
        
        # This is a VTF export task, even if conversion doesn't happen
        # (e.g. only a .vmt is created)
        
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
    
    def _handle_image_conversion(self, item: dict, input_path: Path, output_path: Path,
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
                 vtfcmd_exe: Optional[Path], gameinfo_dir: Optional[Path], args, logger: Logger):
        self.studiomdl_exe = studiomdl_exe
        self.search_paths = search_paths
        self.vtfcmd_exe = vtfcmd_exe
        self.gameinfo_dir = gameinfo_dir
        self.args = args
        self.logger = logger
    
    def _parse_model_defines(self, model_define_vars: dict) -> tuple[dict, dict]:
        """Parses definevariable from config into regular and targeted defines."""
        regular_model_defines = {}
        targeted_model_defines = {}

        for name, value in model_define_vars.items():
            if isinstance(value, dict) and 'targets' in value and 'value' in value:
                for target_name in value['targets']:
                    if target_name not in targeted_model_defines:
                        targeted_model_defines[target_name] = {}
                    targeted_model_defines[target_name][name] = value['value']
            elif isinstance(value, dict) and 'value' in value:
                regular_model_defines[name] = value['value']
            else:
                regular_model_defines[name] = value
        
        return regular_model_defines, targeted_model_defines

    def _get_qc_defines(self, target_name: str, regular_vars: dict, 
                        targeted_vars: dict, global_vars: dict) -> dict:
        """Constructs a dictionary of variables for a specific QC."""
        defines = global_vars.copy()
        defines.update(regular_vars)
        if target_name in targeted_vars:
            defines.update(targeted_vars[target_name])
        return defines

    def _compile_single_qc(self, qc_path: Path, base_name: str, variables: dict, 
                           output_dir: Optional[Path], game_dir: Optional[Path], logger: Logger):
        """Compiles a single QC file, handling temp file creation and cleanup."""
        temp_qc = self._create_temp_qc(qc_path, logger, base_name=base_name, variables=variables)
        
        try:
            success, _, dumped_materials = model_compile_studiomdl(
                studiomdl_exe=self.studiomdl_exe,
                qc_file=temp_qc,
                output_dir=output_dir,
                game_dir=game_dir,
                verbose=self.args.verbose,
                logger=logger,
            )
        finally:
            if temp_qc != qc_path and not self.args.keep_flat_qc:
                if temp_qc.exists():
                    temp_qc.unlink()
                
        return success, dumped_materials

    def compile_model(self, model_name: str, model_data: dict, compile_root: Path, global_vars: dict = None):
        self.logger.info("")
        model_logger = PrefixedLogger(self.logger, "MODEL")
        
        if not model_data.get('compile', True):
            model_logger.warn(f"Skipping model {model_name} (compile=false)")
            return
        
        model_logger.info(f"Compiling model: {model_name}")
        
        qc_path = Path(model_data.get("qc")).resolve()
        if not qc_path.exists():
            model_logger.error(f"QC file not found: {qc_path}")
            return
        
        game_dir = self.gameinfo_dir
        if self.args.game:
            if not game_dir:
                model_logger.error("--game mode requires a valid 'gameinfo' path in your config.")
                return
            output_dir = None
            model_logger.info(f"Compiling model {qc_path.name} directly to game directory")
        else:
            output_dir = compile_root / model_name
            output_dir.mkdir(parents=True, exist_ok=True)
            model_logger.info(f"Compiling model {qc_path.name}")
        
        global_vars = global_vars or {}
        model_define_vars = model_data.get("definevariable", {})
        regular_model_defines, targeted_model_defines = self._parse_model_defines(model_define_vars)
        
        main_model_defines = self._get_qc_defines("qc", regular_model_defines, targeted_model_defines, global_vars)
        
        success, dumped_materials = self._compile_single_qc(
            qc_path, model_name, main_model_defines, output_dir, game_dir, model_logger
        )
        
        if not success:
            model_logger.error("Main QC compilation failed.")
            return
        
        dumped_materials = set(dumped_materials)
        model_logger.info(f"Compiled {qc_path.name} ({len(dumped_materials)} materials)")
        
        self._compile_submodels(
            model_data, qc_path, output_dir, dumped_materials, model_logger, 
            game_dir=game_dir, 
            global_vars=global_vars, 
            regular_model_vars=regular_model_defines, 
            targeted_model_vars=targeted_model_defines, 
            model_name=model_name
        )
        
        if self.args.game:
            return
        
        self._process_materials(qc_path, dumped_materials, output_dir, compile_root, model_logger)
        self._process_subdata(model_data, output_dir, compile_root)
    
    def _create_temp_qc(self, qc_path: Path, logger: Logger, base_name: str, variables: dict = None) -> Path:
        if self.args.qc_mode == 1:
            logger.info("QC mode 1: Using original QC file directly.")
            return qc_path
        
        # QC mode 2 (default): flatten
        temp_qc_name = f"temp_{base_name}.qc"
        temp_qc = qc_path.parent / temp_qc_name
        qc_content = flatten_qc(qc_path, logger=logger, _variables=variables)
        with open(temp_qc, 'w', encoding='utf-8') as dst:
            dst.write(qc_content)
        logger.info(f"QC mode 2: Flattened QC {qc_path.name} to {temp_qc.name}")
        return temp_qc
    
    def _compile_submodels(self, model_data: dict, qc_path: Path, output_dir: Optional[Path],
                          dumped_materials: Set, logger: Logger, game_dir: Optional[Path],
                          global_vars: dict, regular_model_vars: dict, targeted_model_vars: dict,
                          model_name: str = ""):
        for sub_name, sub_qc_file in model_data.get("submodels", {}).items():
            sub_qc_path = Path(sub_qc_file)
            if not sub_qc_path.is_absolute():
                sub_qc_path = qc_path.parent / sub_qc_path
            sub_qc_path = sub_qc_path.resolve()
            
            if not sub_qc_path.exists():
                logger.error(f"Sub-QC not found: {sub_qc_path}")
                continue
            
            submodel_defines = self._get_qc_defines(sub_name, regular_model_vars, targeted_model_vars, global_vars)
            
            logger.info(f"Compiling sub-QC: {sub_qc_path.name} for submodel '{sub_name}'")

            submodel_base_name = f"{model_name}_{sub_name}"
            success, sub_dumped = self._compile_single_qc(
                sub_qc_path, submodel_base_name, submodel_defines, output_dir, game_dir, logger
            )
            
            if success:
                dumped_materials.update(set(sub_dumped))
                logger.info(f"Compiled {sub_qc_path.name} ({len(sub_dumped)} materials)")
    
    def _process_materials(self, qc_path: Path, dumped_materials: Set, 
                          output_dir: Path, compile_root: Path, logger: Logger):
        mode = self.args.mat_mode
        
        if mode == 0:
            logger.warn("Skipping model material copying (mat-mode: 0)")
            return
        
        mat_logger = PrefixedLogger(self.logger, "MATERIAL")

        localize = True
        copy_target = None

        if mode == 1:  # 'raw-local'
            copy_target = output_dir
            localize = False
            mat_logger.info("Material mode 'raw-local': copying to model folder without localization.")
        elif mode == 2:  # 'shared'
            copy_target = compile_root / "Assetshared"
            localize = not self.args.no_mat_local  # on by default, off with flag
            mat_logger.info(f"Material mode 'shared': copying to shared folder (localization: {'on' if localize else 'off'}).")
        
        copy_target.mkdir(parents=True, exist_ok=True)
        
        qc_material_paths = qc_read_materials(qc_path, dumped_materials)
        mat_logger.info(f"Found {len(dumped_materials)} cdmaterials paths from Compile")
        
        for mat in qc_material_paths:
            mat_logger.debug(mat)
        
        mat_logger.info(f"Copying {len(dumped_materials)} materials to {copy_target}...")
        material_to_vmt = map_materials_to_vmt(
            qc_material_paths, self.search_paths, logger=mat_logger
        )
        copied_files = copy_materials(
            material_to_vmt,
            copy_target,
            self.search_paths,
            localize_data=localize,
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
        
        if not studiomdl_exe:
            self.logger.error("Config missing required field: studiomdl")
            return
        
        # Handle --game flag and optional path override for gameinfo
        if isinstance(self.args.game, str):
            game_override_dir = Path(self.args.game)
            if (game_override_dir / "gameinfo.txt").is_file():
                gameinfo_path = game_override_dir / "gameinfo.txt"
                self.logger.info(f"--game override: Using gameinfo.txt from {game_override_dir}")
            else:
                self.logger.warn(f"--game path '{self.args.game}' provided, but no gameinfo.txt found. Ignoring path.")

        if self.args.game and not gameinfo_path:
            self.logger.error("--game mode requires a valid 'gameinfo' path from config or --game argument.")
            return
        
        gameinfo_dir = None
        search_paths = []
        if gameinfo_path:
            gameinfo_dir = gameinfo_path.parent
            search_paths = get_game_search_paths(gameinfo_path)
        else:
            self.logger.warn("No gameinfo provided. Shared materials and material collection will be limited.")

        compile_root = Path(self.args.exportdir or DEFAULT_COMPILE_ROOT).resolve()
        
        if self.args.exportdir is None:
            self.logger.warn(f"--exportdir not provided, using default: {compile_root}")
        
        if self.args.game:
            self.logger.info("--game mode enabled: Compiling models directly to game directory")
            if gameinfo_dir:
                self.logger.info(f"Game directory: {gameinfo_dir}")
            self.logger.info("Materials, data sections, and VPK packaging will be skipped")
        else:
            CompileFolderManager.clean(compile_root, self.logger, self.args.archive_old_ver)
        
        if search_paths:
            self.logger.info("")
            self.logger.info("Game search paths:")
            for p in search_paths:
                self.logger.info(f"\t{p}")
                
            self.logger.info('')
        
        if vtfcmd_exe:
            self.logger.info(f"VTF conversion enabled: {vtfcmd_exe}")
        
        self._compile_models(compile_root, studiomdl_exe, search_paths, 
                           vtfcmd_exe, gameinfo_dir)
        
        if self.args.game:
            #self.logger.info("--game mode: Compilation complete. Skipping post-processing")
            return
        
        self._process_material_sets(compile_root, search_paths)
        self._process_data_sections(compile_root, vtfcmd_exe)
        
        if self.args.package_files:
            self._package_vpks(compile_root, vpk_exe)
    
    def _compile_models(self, compile_root: Path, studiomdl_exe: Path, 
                       search_paths: List[Path], vtfcmd_exe: Optional[Path], 
                       gameinfo_dir: Optional[Path]):
        compiler = ModelCompiler(studiomdl_exe, search_paths, vtfcmd_exe, 
                                gameinfo_dir, self.args, self.logger)
        
        global_define_vars = self.config.get("definevariable", {})

        for model_name, model_data in self.config.get("model", {}).items():
            compiler.compile_model(model_name, model_data, compile_root, global_vars=global_define_vars)
    
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
            root_dir = PathResolver.get_root_dir(self.args, Path(self.args.config_path).resolve())
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
        
        return output_path.stat().st_mtime == src_file.stat().st_mtime
    
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

def wait_for_keypress():
    """Wait for user to press any key before exiting"""
    try:
        input("\nPress Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        pass

@timer
def main():
    print_header()

    parser = argparse.ArgumentParser(description="Source Resource Compiler")
    
    # Positional argument
    parser.add_argument("config_path", metavar="CONFIG_JSON",
                        help="Path to the config.json file.")

    # Global arguments
    parser.add_argument("--log", action="store_true",
                        help="Enable logging to the './kitsune_log' directory.")
    parser.add_argument("--verbose", action="store_true", 
                        help="Enable verbose logging.")
    parser.add_argument("--basedir", type=str,
                        help="Absolute path to override input/output root.")

    # ValveModel Pipeline arguments
    model_group = parser.add_argument_group("ValveModel Pipeline")
    model_group.add_argument("--exportdir", metavar="COMPILE_DIR", 
                         default=DEFAULT_COMPILE_ROOT,
                         help=f"Root folder for compiled output.")
    model_group.add_argument("--game", nargs='?', const=True, default=False,
                         help="Compile models directly to game directory. Optionally provide a path to a directory with gameinfo.txt to override config.")
    model_group.add_argument("--mat-mode", type=int, default=2, choices=[0,1,2],
                         help="Material mode: 0=skip, 1=raw-local, 2=shared (default).")
    model_group.add_argument("--no-mat-local", action="store_true",
                         help="Disable material localization.")
    model_group.add_argument("--package-files", action="store_true", 
                         help="Package each subfolder into VPK (formerly --vpk).")
    model_group.add_argument("--archive-old-ver", action="store_true", 
                         help="Archive existing files instead of deletion (formerly --archive).")
    model_group.add_argument("--qc-mode", type=int, default=2, choices=[1,2],
                         help="QC mode: 1=use raw QC, 2=use flattened QC (default).")
    model_group.add_argument("--keep-flat-qc", action="store_true",
                         help="Keep flattened QC files after compilation.")

    # ValveTexture Pipeline arguments
    texture_group = parser.add_argument_group("ValveTexture Pipeline")
    texture_group.add_argument("--forceupdate", action="store_true",
                         help="Force reprocessing all textures.")
    texture_group.add_argument("--allow_reprocess", action="store_true",
                         help="Allow same file to be processed multiple times.")
    texture_group.add_argument("--recursive", action="store_true",
                         help="Search for files recursively in subfolders.")

    # Process args to allow single-dash long options
    processed_argv = []
    for arg in sys.argv[1:]:
        if arg.startswith('-') and not arg.startswith('--') and len(arg) > 2:
            is_negative_number = False
            try:
                float(arg)
                is_negative_number = True
            except ValueError:
                pass
            
            if not is_negative_number:
                processed_argv.append('--' + arg[1:])
            else:
                processed_argv.append(arg)
        else:
            processed_argv.append(arg)

    args = parser.parse_args(processed_argv)

    # Setup logger
    log_file = None
    if args.log:
        log_dir = Path("kitsune_log").resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = log_dir / f"kitsune_log_{timestamp}.txt"
    
    logger = Logger(verbose=args.verbose, use_color=True, log_file=log_file)
    if log_file:
        logger.info(f"Logging enabled → {log_file}")
        logger.info(f"")

    # Process config and execute pipeline
    try:
        config = parse_config_json(args.config_path)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        return logger

    header = config.get("header")
    if not header:
        logger.error("Missing 'header' field in config — cannot determine pipeline type.")
        return logger

    # Compatibility: Some parts of the code expect args.dir
    args.dir = args.basedir if args.basedir is not None else os.getcwd()

    try:
        if header == "ValveModel":
            ValveModelPipeline(config, args, logger).execute()
        elif header == 'ValveTexture':
            ValveTexturePipeline(config, args, logger).execute()
        else:
            logger.error(f"Unknown pipeline header: {header}")
            wait_for_keypress()
            return logger
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}")
        wait_for_keypress()
        return logger

    return logger

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        wait_for_keypress()
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFATAL ERROR: {e}")
        wait_for_keypress()
        sys.exit(1)