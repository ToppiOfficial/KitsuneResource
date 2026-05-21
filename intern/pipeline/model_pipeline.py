import shutil
from pathlib import Path
from typing import List, Optional, NamedTuple

from intern.utils import Logger, PathResolver
from intern.formats.vpk import GameVPKCache
from intern.assets.materials import copy_materials, map_materials_to_vmt
from intern.formats.mdl import read_mdl_materials, build_material_paths
from intern.game.model import model_compile_studiomdl
from intern.game.gameinfo import get_game_search_paths
from intern.game.archiver import Archiver
from intern.game.packager import package_archive
from intern.source.qc import process_qc_file
from .data_processor import DataProcessor


def _resolve_qc_path(raw: str) -> Optional[Path]:
    """Resolve a QC path from config, probing .qc then .qci when no extension is given."""
    p = Path(raw).resolve()
    if p.suffix:
        return p if p.exists() else None
    for ext in ('.qc', '.qci'):
        candidate = p.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


class ModelCompiler:
    def __init__(self, studiomdl_exe: Path, search_paths: List[Path],
                 vtfcmd_exe: Optional[Path], gameinfo_dir: Optional[Path],
                 args, logger: Logger, global_includedirs: list = None,
                 moddir: Optional[Path] = None, vprojectdir: Optional[Path] = None):
        self.studiomdl_exe = studiomdl_exe
        self.search_paths = search_paths
        self.vtfcmd_exe = vtfcmd_exe
        self.gameinfo_dir = gameinfo_dir
        self.args = args
        self.logger = logger
        self._vpk_cache = GameVPKCache(gameinfo_dir, search_paths, logger=logger) if gameinfo_dir else None
        self.global_includedirs = global_includedirs if global_includedirs is not None else []
        self.moddir = moddir
        self.vprojectdir = vprojectdir

    def _parse_model_defines(self, model_define_vars: dict) -> tuple[dict, dict]:
        regular_model_defines = {}
        targeted_model_defines = {}

        for name, value in model_define_vars.items():
            if isinstance(value, dict) and 'targets' in value and 'value' in value:
                for target_name in value['targets']:
                    targeted_model_defines.setdefault(target_name, {})[name] = value['value']
            elif isinstance(value, dict) and 'value' in value:
                regular_model_defines[name] = value['value']
            else:
                regular_model_defines[name] = value

        return regular_model_defines, targeted_model_defines

    def _get_qc_defines(self, target_name: str, regular_vars: dict,
                        targeted_vars: dict, global_vars: dict) -> dict:
        defines = global_vars.copy()
        defines.update(regular_vars)
        if target_name in targeted_vars:
            defines.update(targeted_vars[target_name])
        return defines

    def _compile_single_qc(self, qc_path: Path, base_name: str, variables: dict,
                            output_dir: Optional[Path], game_dir: Optional[Path],
                            logger: Logger, include_dirs: list = None):
        temp_qc, preprocess_errors = self._process_qc(
            qc_path, logger, base_name=base_name,
            variables=variables, include_dirs=include_dirs,
        )

        success = False
        moved_files: list[Path] = []
        try:
            if preprocess_errors == 0:
                success, moved_files = model_compile_studiomdl(
                    studiomdl_exe=self.studiomdl_exe,
                    qc_file=temp_qc,
                    output_dir=output_dir,
                    game_dir=game_dir,
                    vproject_dir=(None if getattr(self.args, 'no_vproject', False)
                                  else (self.vprojectdir or game_dir)),
                    verbose=self.args.verbose,
                    logger=logger,
                )
        finally:
            if temp_qc.exists():
                processed_dir = qc_path.parent / ".processed-qc"
                processed_dir.mkdir(parents=True, exist_ok=True)
                target_path = processed_dir / temp_qc.name.replace("temp_", "")
                try:
                    shutil.move(str(temp_qc), str(target_path))
                    logger.info(f"Moved Processed QC to {target_path}")
                except Exception as e:
                    logger.warn(f"Failed to move Processed QC to processed-qc: {e}")

        return success, moved_files

    def compile_model(self, model_name: str, model_data: dict, compile_root: Path,
                      global_vars: dict = None) -> tuple[bool, list[Path], Optional[Path]]:
        self.logger.info("")
        model_logger = self.logger.with_context("MODEL")

        model_logger.info(f"Compiling model: {model_name}")

        qc_raw = model_data.get("qc")
        if not qc_raw:
            model_logger.error(f"Model '{model_name}' is missing 'qc' field.")
            return False, [], None

        qc_path = _resolve_qc_path(qc_raw)
        if not qc_path:
            model_logger.error(f"QC file not found: {qc_raw} (tried .qc and .qci)")
            return False, [], None

        game_dir = self.gameinfo_dir

        if self.args.game:
            if not game_dir and not self.vprojectdir:
                model_logger.error(
                    "--game mode requires either 'gameinfo' or 'vprojectdir' to be defined in config."
                )
                return False, [], None

            if self.moddir:
                output_dir = self.moddir
            elif getattr(self.args, 'no_vproject', False):
                output_dir = game_dir
            else:
                output_dir = None
            model_logger.info(f"Compiling model {qc_path.name} directly to game directory")
        else:
            output_dir = (compile_root if getattr(self.args, 'single_addon', False)
                          else compile_root / model_name)
            output_dir.mkdir(parents=True, exist_ok=True)
            model_logger.info(f"Compiling model {qc_path.name}")

        global_vars = global_vars or {}
        global_regular, _ = self._parse_model_defines(global_vars)

        model_define_vars = model_data.get("definevariable", {})
        regular_model_defines, targeted_model_defines = self._parse_model_defines(model_define_vars)

        main_model_defines = self._get_qc_defines(
            "qc", regular_model_defines, targeted_model_defines, global_regular
        )

        include_dirs = self.global_includedirs.copy()
        model_include_dirs = model_data.get("includedirs")
        if isinstance(model_include_dirs, list):
            include_dirs.extend(model_include_dirs)

        success, moved_files = self._compile_single_qc(
            qc_path, model_name, main_model_defines, output_dir, game_dir,
            model_logger, include_dirs=include_dirs,
        )

        if not success:
            model_logger.error("Main QC compilation failed.")
            return False, [], None

        model_logger.info(f"Compiled {qc_path.name}")

        self._compile_submodels(
            model_data, qc_path, output_dir, moved_files, model_logger,
            game_dir=game_dir,
            global_vars=global_regular,
            regular_model_vars=regular_model_defines,
            targeted_model_vars=targeted_model_defines,
            model_name=model_name,
            include_dirs=include_dirs,
        )

        if not self.args.game:
            self._process_subdata(model_data, output_dir, compile_root)

        return True, moved_files, output_dir

    def _process_qc(self, qc_path: Path, logger: Logger, base_name: str,
                    variables: dict = None, include_dirs: list = None) -> tuple[Path, int]:
        temp_qc_name = f"temp_{base_name}.qc"
        temp_qc = qc_path.parent / temp_qc_name

        compiler_name = self.studiomdl_exe.stem.lower()

        qc_content, error_count = process_qc_file(
            qc_path, logger=logger, _variables=variables,
            include_dirs=include_dirs, compiler=compiler_name,
            vrd_prefix=base_name,
        )

        with open(temp_qc, 'w', encoding='utf-8') as dst:
            dst.write(qc_content)

        if error_count > 0:
            logger.error(
                f"QC preprocessing failed with {error_count} error(s) - studiomdl will be skipped."
            )
        logger.info(f"Flattened QC {qc_path.name} to {temp_qc.name}")
        return temp_qc, error_count

    def _compile_submodels(self, model_data: dict, qc_path: Path, output_dir: Optional[Path],
                           all_moved_files: list, logger: Logger, game_dir: Optional[Path],
                           global_vars: dict, regular_model_vars: dict,
                           targeted_model_vars: dict, model_name: str = "",
                           include_dirs: list = None):
        for sub_name, sub_qc_file in model_data.get("submodels", {}).items():
            self.logger.root.submodel_total += 1

            sub_qc_path = _resolve_qc_path(
                str(Path(sub_qc_file) if Path(sub_qc_file).is_absolute()
                    else qc_path.parent / sub_qc_file)
            )

            if not sub_qc_path:
                logger.error(f"Sub-QC not found: {sub_qc_file} (tried .qc and .qci)")
                continue

            submodel_defines = self._get_qc_defines(
                sub_name, regular_model_vars, targeted_model_vars, global_vars
            )

            logger.info(f"Compiling sub-QC: {sub_qc_path.name} for submodel '{sub_name}'")

            success, sub_moved = self._compile_single_qc(
                sub_qc_path, f"{model_name}_{sub_name}", submodel_defines,
                output_dir, game_dir, logger, include_dirs=include_dirs,
            )

            if success:
                all_moved_files.extend(sub_moved)
                logger.info(f"Compiled {sub_qc_path.name}")
                self.logger.root.submodel_compiled += 1

    def _process_materials(self, mdl_files: list, output_dir: Path,
                           compile_root: Path, logger: Logger):
        mode = self.args.mat_mode

        if mode == 0:
            logger.warn("Skipping model material copying (mat-mode: 0)")
            return

        mat_logger = self.logger.with_context("MATERIAL")
        localize = not self.args.no_mat_local

        if getattr(self.args, 'single_addon', False):
            copy_target = compile_root
            mat_logger.info(
                f"Single-addon mode: copying materials to addon root "
                f"(localization: {'on' if localize else 'off'})."
            )
        elif mode == 1:
            copy_target = output_dir
            mat_logger.info(
                f"Material mode 'raw-local': copying to model folder "
                f"(localization: {'on' if localize else 'off'})."
            )
        elif mode == 2:
            copy_target = compile_root / "SharedAssets"
            mat_logger.info(
                f"Material mode 'shared': copying to shared folder "
                f"(localization: {'on' if localize else 'off'})."
            )
        else:
            mat_logger.warn(f"Unrecognised mat-mode {mode}, skipping material copy.")
            return

        copy_target.mkdir(parents=True, exist_ok=True)

        all_material_paths: list[str] = []
        all_texture_names: list[str] = []
        seen: set[str] = set()
        seen_tex: set[str] = set()
        for mdl_file in mdl_files:
            try:
                texture_names, cdmaterials = read_mdl_materials(mdl_file)
                mat_logger.info(
                    f"MDL {mdl_file.name}: {len(texture_names)} texture(s), "
                    f"{len(cdmaterials)} cdmaterials dir(s)"
                )
                for p in build_material_paths(texture_names, cdmaterials):
                    if p not in seen:
                        seen.add(p)
                        all_material_paths.append(p)
                for t in texture_names:
                    t_norm = t.strip('/').replace('\\', '/')
                    if t_norm and t_norm not in seen_tex:
                        seen_tex.add(t_norm)
                        all_texture_names.append(t_norm)
            except Exception as e:
                mat_logger.warn(f"Failed to read materials from {mdl_file.name}: {e}")

        mat_logger.info(f"Found {len(all_material_paths)} material path(s) from MDL")
        for mat in all_material_paths:
            mat_logger.debug(mat)

        mat_logger.info(f"Copying materials to {copy_target}...")
        material_to_vmt = map_materials_to_vmt(
            all_material_paths, self.search_paths, logger=mat_logger, base_names=all_texture_names
        )
        copied_files = copy_materials(
            material_to_vmt,
            copy_target,
            self.search_paths,
            localize_data=localize,
            logger=mat_logger,
            vpk_cache=self._vpk_cache,
        )
        mat_logger.info(f"Material copy complete ({len(copied_files)} files).")

    def _process_subdata(self, model_data: dict, output_dir: Path, compile_root: Path):
        subdata = model_data.get("subdata", [])
        if subdata:
            processor = DataProcessor(
                compile_root, self.vtfcmd_exe, self.args, self.logger,
                include_dirs=self.global_includedirs,
            )
            processor.process_items(subdata, output_dir)


class MaterialSetCopier:
    @staticmethod
    def copy_set(set_name: str, set_data: dict, compile_root: Path,
                 search_paths: List[Path], logger: Logger):
        mat_logger = logger.with_context("MATERIAL")
        vmt_list = set_data.get("materials", [])

        if not vmt_list:
            mat_logger.warn(f"[{set_name}] No materials listed - skipping.")
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


class _PipelineTools(NamedTuple):
    studiomdl_exe: Path
    gameinfo_dir: Optional[Path]
    vtfcmd_exe: Optional[Path]
    packager_exe: Optional[Path]
    search_paths: List[Path]
    compile_root: Path


class ValveModelPipeline:
    def __init__(self, config: dict, args, logger: Logger):
        self.config = config
        self.args = args
        self.logger = logger

    # ── Prepare ──────────────────────────────────────────────────────────────

    def _prepare(self) -> Optional["_PipelineTools"]:
        studiomdl_exe, gameinfo_path, vtfcmd_exe, packager_exe = PathResolver.resolve_and_validate(
            self.config, "studiomdl", "gameinfo", "vtfcmd", "packager"
        )

        if not studiomdl_exe:
            self.logger.error("Config missing required field: studiomdl")
            return None

        if isinstance(self.args.game, str):
            game_override_dir = Path(self.args.game)
            candidate_gi = game_override_dir / "gameinfo.txt"
            if candidate_gi.is_file():
                gameinfo_path = candidate_gi
                self.logger.info(f"--game override: Using gameinfo.txt from {game_override_dir}")
            else:
                self.logger.warn(
                    f"--game path '{self.args.game}' provided, but no gameinfo.txt found. Ignoring path."
                )

        if self.args.game and not gameinfo_path:
            self.logger.error(
                "--game mode requires a valid 'gameinfo' path from config or --game argument."
            )
            return None

        gameinfo_dir = None
        search_paths = []
        if gameinfo_path:
            gameinfo_dir = gameinfo_path.parent
            search_paths = get_game_search_paths(gameinfo_path)
        else:
            self.logger.warn(
                "No gameinfo provided. Shared materials and material collection will be limited."
            )

        compile_root = Path(self.args.exportdir.strip('"')).resolve()
        base_compile_root = compile_root

        if self.args.single_addon:
            addon_folder = self.config.get("addonroot", "").strip().strip('"')
            if not self.args.game and not addon_folder:
                self.logger.error(
                    "--single-addon requires 'addonroot' to be defined and non-empty in config."
                )
                return None
            compile_root = compile_root / addon_folder
            self.args.mat_mode = 1
            self.logger.info(f"Output root set to '{compile_root}', mat-mode forced to 1")

        if self.args.game:
            self.logger.info("--game mode enabled: Compiling models directly to game directory")
            if gameinfo_dir:
                self.logger.info(f"Game directory: {gameinfo_dir}")
            self.logger.info("Materials, data sections, and packaging will be skipped")
        else:
            Archiver.clean(compile_root, self.logger, self.args.archive_old_ver,
                           archive_root=base_compile_root)

        if search_paths:
            self.logger.info("")
            self.logger.info("Game search paths:")
            for p in search_paths:
                self.logger.info(f"\t{p}")
            self.logger.info("")

        if vtfcmd_exe:
            self.logger.info(f"VTF conversion enabled: {vtfcmd_exe}")

        return _PipelineTools(
            studiomdl_exe=studiomdl_exe,
            gameinfo_dir=gameinfo_dir,
            vtfcmd_exe=vtfcmd_exe,
            packager_exe=packager_exe,
            search_paths=search_paths,
            compile_root=compile_root,
        )

    def _make_compiler(self, tools: "_PipelineTools") -> "ModelCompiler":
        global_includedirs = self.config.get("includedirs", [])
        moddir_val = self.config.get("moddir")
        vprojectdir_val = self.config.get("vprojectdir")
        return ModelCompiler(
            tools.studiomdl_exe,
            tools.search_paths,
            tools.vtfcmd_exe,
            tools.gameinfo_dir,
            self.args,
            self.logger,
            global_includedirs=global_includedirs,
            moddir=Path(moddir_val).resolve() if moddir_val else None,
            vprojectdir=Path(vprojectdir_val).resolve() if vprojectdir_val else None,
        )

    # ── Process ──────────────────────────────────────────────────────────────

    def _compile_all_models(self, compiler: "ModelCompiler",
                            tools: "_PipelineTools") -> list[tuple[list[Path], Optional[Path]]]:
        global_define_vars = self.config.get("definevariable", {})
        only_filter = [e.lower() for e in self.args.only] if self.args.only else None
        results: list[tuple[list[Path], Optional[Path]]] = []

        for model_name, model_data in self.config.get("model", {}).items():
            self.logger.root.model_total += 1
            if only_filter and model_name.lower() not in only_filter:
                continue
            success, moved_files, output_dir = compiler.compile_model(
                model_name, model_data, tools.compile_root, global_vars=global_define_vars
            )
            if success:
                self.logger.root.model_compiled += 1
                mdl_files = [f for f in moved_files if f.suffix.lower() == ".mdl"]
                results.append((mdl_files, output_dir))

        return results

    # ── Package ───────────────────────────────────────────────────────────────

    def _process_all_materials(self, compiler: "ModelCompiler",
                               compile_results: list[tuple[list[Path], Optional[Path]]],
                               tools: "_PipelineTools"):
        mode = self.args.mat_mode
        if mode == 0:
            self.logger.warn("Skipping model material copying (mat-mode: 0)")
            return

        mat_logger = self.logger.with_context("MATERIAL")

        if getattr(self.args, "single_addon", False) or mode == 2:
            # Batch: one deduped copy pass for all models combined.
            all_mdl_files = [f for mdl_files, _ in compile_results for f in mdl_files]
            if all_mdl_files:
                compiler._process_materials(
                    all_mdl_files, tools.compile_root, tools.compile_root, mat_logger
                )
        else:
            # mode=1: each model's materials go to its own output folder.
            for mdl_files, output_dir in compile_results:
                if mdl_files:
                    compiler._process_materials(
                        mdl_files, output_dir, tools.compile_root, mat_logger
                    )

    def _process_material_sets(self, compile_root: Path, search_paths: List[Path]):
        for set_name, set_data in self.config.get("material", {}).items():
            MaterialSetCopier.copy_set(set_name, set_data, compile_root, search_paths, self.logger)

    def _process_data_sections(self, compile_root: Path, vtfcmd_exe: Optional[Path]):
        include_dirs = self.config.get("includedirs", [])
        processor = DataProcessor(compile_root, vtfcmd_exe, self.args, self.logger,
                                  include_dirs=include_dirs)
        only_filter = [e.lower() for e in self.args.only] if self.args.only else None

        for folder_name, items in self.config.get("data", {}).items():
            self.logger.root.data_total += 1
            if only_filter and folder_name.lower() not in only_filter:
                continue
            output_dir = compile_root if self.args.single_addon else compile_root / folder_name
            processor.process_items(items, output_dir)
            self.logger.root.data_compiled += 1

    def _package_archives(self, compile_root: Path, packager_exe: Optional[Path]):
        if not packager_exe:
            self.logger.warn(
                "Packager executable not found or missing in config, skipping packaging"
            )
            return

        if self.args.single_addon:
            package_archive(packager_exe, compile_root, self.logger)
        else:
            for subfolder in compile_root.iterdir():
                if subfolder.is_dir():
                    package_archive(packager_exe, subfolder, self.logger)

    # ── Orchestration ─────────────────────────────────────────────────────────

    def execute(self):
        tools = self._prepare()
        if tools is None:
            return

        compiler = self._make_compiler(tools)

        compile_results = self._compile_all_models(compiler, tools)

        if self.args.game:
            return

        self._process_all_materials(compiler, compile_results, tools)
        self._process_material_sets(tools.compile_root, tools.search_paths)
        self._process_data_sections(tools.compile_root, tools.vtfcmd_exe)

        if self.args.package_files:
            self._package_archives(tools.compile_root, tools.packager_exe)
