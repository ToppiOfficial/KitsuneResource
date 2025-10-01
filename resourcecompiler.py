import argparse, shutil, time, send2trash, re, os
from pathlib import Path
from utils import *
from compilers import materials
from compilers.materials import export_vtf
from compilers.model import model_compile_studiomdl
from compilers.gameinfo import get_game_search_paths
from compilers.vpk import package_vpk
from compilers.image import convert_image
from compilers.qc import qc_read_materials

def ValveModel_clean_compile_folder(compile_root: Path, logger: Logger, archived: bool = False):
    os_logger = PrefixedLogger(logger, "OS")

    if compile_root.exists() and any(compile_root.iterdir()):
        if archived:
            try:
                # Create archive folder next to compile_root
                archive_dir = compile_root.parent / "_archive"
                archive_dir.mkdir(exist_ok=True)

                # Generate timestamped folder name
                created_time = datetime.fromtimestamp(compile_root.stat().st_ctime)
                timestamp = created_time.strftime("%Y-%m-%d_%H-%M-%S")
                archive_target = archive_dir / f"{compile_root.name}_{timestamp}"

                # Move entire compile folder to archive folder
                shutil.move(str(compile_root), str(archive_target))

                os_logger.info(f"Archived compile folder to: {archive_target}")
            except Exception as e:
                os_logger.warn(f"Failed to archive compile folder: {e}")
        else:
            os_logger.info("Cleaning existing compile folder...")
            for item in compile_root.iterdir():
                try:
                    send2trash.send2trash(item)
                    os_logger.info(f"Sent to Recycle Bin: {item.relative_to(compile_root)}")
                except Exception as e:
                    os_logger.warn(f"Failed to remove {item}: {e}")
    else:
        os_logger.info("No existing compile folder to clean.")
                
def ValveModel_create_vmt_from_template(vmt_template_json, vtf_path, compile_root, args, logger : Logger):
    vmt_template = resolve_json_path(vmt_template_json, args.config, args.dir)
    if not vmt_template.exists():
        logger.warn(f"VMT template not found, skipping: {vmt_template}")
        return

    vmt_dst = vtf_path.with_suffix(".vmt")
    materials_root = Path(compile_root / "AssetShared")
    try:
        vtf_rel = vtf_path.relative_to(materials_root).with_suffix("")
        vtf_rel_posix = vtf_rel.as_posix()
        if vtf_rel_posix.startswith("materials/"):
            vtf_rel_posix = vtf_rel_posix[len("materials/"):]
    except ValueError:
        vtf_rel_posix = vtf_path.stem  # fallback

    template_lines = vmt_template.read_text(encoding="utf-8").splitlines()
    new_lines = []

    for line in template_lines:
        stripped = line.strip()

        if stripped.startswith("$basetexture"):
            leading_ws = line[:line.index("$basetexture")]
            new_lines.append(f'{leading_ws}$basetexture "{vtf_rel_posix}"')
        elif stripped.startswith('"$'):
            first_space = line.find(' ')
            key = line[:first_space].replace('"', '')
            rest = line[first_space+1:]
            new_lines.append(f'{key} {rest}')
        else:
            new_lines.append(line)

    vmt_dst.write_text("\n".join(new_lines), encoding="utf-8")
    logger.info(f"VMT created: {vmt_dst.relative_to(compile_root)}")

def ValveModel_process_data_items(items, base_output: Path, compile_root: Path, vtfcmd_exe, args, logger: Logger):
    data_logger = PrefixedLogger(logger, "DATA")
    
    for item in items:
        input_path = resolve_json_path(item.get("input"), args.config, args.dir)
        output_path = base_output / Path(item.get("output"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        input_str = item.get("input").strip()
        output_str = item.get("output").strip()
        vtf_data = item.get("vtf")

        #- TEXT REPLACEMENT-
        if input_str.endswith(SUPPORTED_TEXT_FORMAT) and output_str.endswith(SUPPORTED_TEXT_FORMAT):
            replace_map = item.get("replace")
            if replace_map:
                try:
                    text = input_path.read_text(encoding="utf-8")
                    for k, v in replace_map.items():
                        text = text.replace(k, v)
                    output_path.write_text(text, encoding="utf-8")
                    data_logger.info(f"Replaced strings: {input_path.name} -> {output_path.name}")
                    continue
                except Exception as e:
                    data_logger.error(f"Failed string replace: {input_path} -> {output_path} | {e}")

        #- VTF EXPORT-
        elif (input_str.endswith(SUPPORTED_IMAGE_FORMAT) or input_str.endswith('.vtf')) and output_str.endswith(".vtf"):
            if not input_str.endswith(".vtf") and vtfcmd_exe:
                try:
                    materials.export_vtf(
                        src_path=input_path,
                        dst_path=output_path,
                        vtfcmd=vtfcmd_exe,
                        flags=vtf_data.get("flags", []),
                        extra_args=vtf_data.get("encoder_args", []),
                        silent=True
                    )
                    data_logger.info(f"VTF export: {input_path.name} -> {output_path.name}")
                except Exception as e:
                    data_logger.error(f"Failed to export VTF: {input_path} -> {output_path} | {e}")

            if vtf_data and vtf_data.get("vmt"):
                ValveModel_create_vmt_from_template(vtf_data["vmt"], output_path, compile_root, args, data_logger)

        #- IMAGE CONVERSION-
        elif input_str.endswith(SUPPORTED_IMAGE_FORMAT) and output_str.endswith(SUPPORTED_IMAGE_FORMAT):
            try:
                converted = convert_image(input_path, output_path)
                if converted:
                    data_logger.info(f"Converted image: {input_path.name} -> {output_path.name}")
            except Exception as e:
                data_logger.error(f"Failed to convert: {input_path} -> {output_path} | {e}")

        #- FILE COPY-
        else:
            shutil.copy2(input_path, output_path)
            data_logger.info(f"Copied file: {input_path.name} -> {output_path.name}")
          
def ValveModel_compile_single_model(model_name, model_data, compile_root, studiomdl_exe, search_paths, vtfcmd_exe, gameinfo_dir, args, logger : Logger):
    model_logger = PrefixedLogger(logger, "MODEL")
    compile_model = model_data.get('compile', True)
    if not compile_model:
        model_logger.warn(f"Skipping model {model_name} because compile=false.")
        return

    qc_path = Path(model_data.get("qc")).resolve()
    if not qc_path.exists():
        model_logger.error(f"QC file not found: {qc_path}")
        return

    output_dir = None
    game_dir = None
    model_logger.info(f"Compiling model {qc_path.name}")
    
    #if args.game:
    #    game_dir = gameinfo_dir
    #else:
    output_dir = compile_root / model_name
    output_dir.mkdir(parents=True, exist_ok=True)

    success, compiled_files, dumped_materials = model_compile_studiomdl(
        studiomdl_exe=studiomdl_exe,
        qc_file=qc_path,
        output_dir=output_dir,
        game_dir=gameinfo_dir,
        verbose=args.verbose,
        logger=logger,
    )

    if not success:
        model_logger.error("Main QC compilation failed.")
        return

    dumped_materials = set(dumped_materials)
    model_logger.info(f"Compiled {qc_path.name} ({len(dumped_materials)} materials)")

    # Sub-QCs
    for sub_name, sub_qc in model_data.get("submodels", {}).items():
        sub_qc_path = Path(sub_qc)
        if not sub_qc_path.is_absolute():
            sub_qc_path = qc_path.parent / sub_qc
        sub_qc_path = sub_qc_path.resolve()

        if not sub_qc_path.exists():
            model_logger.error(f"Sub-QC not found: {sub_qc_path}")
            continue

        model_logger.info(f"Compiling sub-QC: {sub_qc_path.name}")
        success, _, sub_dumped_materials = model_compile_studiomdl(
            studiomdl_exe=studiomdl_exe,
            qc_file=sub_qc_path,
            output_dir=output_dir,
            game_dir=None,
            verbose=args.verbose,
            logger=logger,
        )

        if success:
            dumped_materials.update(set(sub_dumped_materials))
            model_logger.info(f"Compiled {sub_qc_path.name} ({len(sub_dumped_materials)} materials)")
            
    #if args.game:
    #    return

    # Material Copy
    if not args.nomaterial:
        mat_logger = PrefixedLogger(logger, "MATERIAL")
        copy_target = compile_root / "Assetshared" if args.sharedmaterials else output_dir
        copy_target.mkdir(parents=True, exist_ok=True)
        
        qc_material_paths = qc_read_materials(qc_path, dumped_materials)
        mat_logger.info(f"Found {len(dumped_materials)} cdmaterials paths from Compile")
        
        for mat in qc_material_paths:
            mat_logger.debug(mat)
        
        mat_logger.info(f"Copying {len(dumped_materials)} materials to {copy_target}...")
        material_to_vmt = materials.map_materials_to_vmt(qc_material_paths, search_paths)
        copied_files = materials.copy_materials(
            material_to_vmt,
            copy_target,
            search_paths,
            localize_data=not args.nolocalize,
            logger=mat_logger,
        )
        mat_logger.info(f"Material copy complete ({len(copied_files)} files).")
    else:
        model_logger.warn("Skipping model material copying (-nomaterial).")

    # Subdata Processing
    subdata = model_data.get("subdata", [])
    if subdata:
        ValveModel_process_data_items(subdata, output_dir, compile_root, vtfcmd_exe, args, logger)
        
def ValveModel_copy_material_set(set_name, set_data, compile_root, search_paths, logger: Logger):
    mat_logger = PrefixedLogger(logger, "MATERIAL")
    vmt_list = set_data.get("materials", [])
    if not vmt_list:
        mat_logger.warn(f"[{set_name}] No materials listed — skipping.")
        return
    
    mat_logger.info(f"[{set_name}] Copying material set...")
    output_dir = compile_root / set_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    material_to_vmt = {Path(vmt): Path(vmt) for vmt in vmt_list}

    copied_files = materials.copy_materials(
        material_to_vmt,
        output_dir,
        search_paths,
        localize_data=True,
        logger=mat_logger,
    )

    mat_logger.info(f"Material-only copy complete ({len(copied_files)} files).")

def ValveModel(config, args, logger : Logger):
    # Resolve paths & validate
    studiomdl_exe = Path(config.get("studiomdl", "")).resolve()
    gameinfo_path = Path(config.get("gameinfo", "")).resolve()
    gameinfo_dir = gameinfo_path.parent
    
    if not studiomdl_exe.exists() or not gameinfo_path.exists():
        logger.error("Config.json missing required fields or has nothing to process.")
        return

    compile_root = Path(args.exportdir or DEFAULT_COMPILE_ROOT).resolve()
    if args.exportdir is None:
        logger.warn(f"--compile-root not provided, using default: {compile_root}")

    # Clean folder
    ValveModel_clean_compile_folder(compile_root, logger, archived=args.archive)

    search_paths = get_game_search_paths(gameinfo_path)
    logger.info("Game search paths:")
    for p in search_paths:
        logger.info(f"\t{p}")

    vtfcmd_exe = Path(config.get("vtfcmd", "")).resolve() if config.get("vtfcmd") else None
    if vtfcmd_exe and not vtfcmd_exe.exists():
        logger.warn(f"vtfcmd.exe not found: {vtfcmd_exe} — skipping VTF conversions")
        vtfcmd_exe = None

    # Model compile loop
    for model_name, model_data in config.get("model", {}).items():
        ValveModel_compile_single_model(model_name, model_data, compile_root, studiomdl_exe, search_paths, vtfcmd_exe, gameinfo_dir, args, logger)
        
    #if args.game: return

    # Material sets
    for set_name, set_data in config.get("material", {}).items():
        ValveModel_copy_material_set(set_name, set_data, compile_root, search_paths, logger)

    # Data processing
    for folder_name, items in config.get("data", {}).items():
        ValveModel_process_data_items(items, compile_root / folder_name, compile_root, vtfcmd_exe, args, logger)

                        
    vpk_exe = Path(config.get("vpk")).resolve() if config.get("vpk") else None
    if args.vpk and (not vpk_exe.exists() if vpk_exe else True):
        logger.warn(f"vpk.exe not found or missing in config, skipping VPK packaging.")
        args.vpk = False

    if args.vpk and vpk_exe:
        compile_root = Path(compile_root)
        for subfolder in compile_root.iterdir():
            if subfolder.is_dir():
                package_vpk(vpk_exe, subfolder, logger)

def ValveTexture(config: dict, args: argparse.Namespace, logger: Logger):
    """
    Pipeline for header == 'ValveTexture'
    Processes images into VTFs using vtfcmd and user-defined settings.
    Supports:
        --forceupdate: force reprocessing all files
        --allow_reprocess: allow same file to be processed by multiple JSON entries
        --dir: absolute path to override input/output root directory
    """
    vtfcmd = Path(config.get("vtfcmd", "")).resolve()
    if not vtfcmd.exists():
        logger.error(f"vtfcmd not found: {vtfcmd}")
        return

    vtf_config = config.get("vtf", {})
    if not vtf_config:
        logger.warn("No 'vtf' section found in config.json — nothing to process.")
        return

    # Use --dir if present, else fall back to JSON's directory
    if getattr(args, "dir", None):
        root_dir = Path(args.dir).resolve()
        if not root_dir.exists() or not root_dir.is_dir():
            logger.error(f"--dir path is invalid: {root_dir}")
            return
        logger.info(f"Overriding input/output root with --dir: {root_dir}")
    else:
        root_dir = Path(args.config).resolve().parent

    processed_files = set()

    for key, entry in vtf_config.items():
        logger.info(f"Processing texture group: {key}")

        input_pattern = entry.get("input")
        if not input_pattern:
            logger.warn(f"Skipped {key} — missing 'input'")
            continue

        matching_files = []
        input_path = root_dir / input_pattern if not re.search(r"[.*+?^${}()|\[\]\\]", input_pattern) else None

        if "*" in input_pattern or re.search(r"[.*+?^${}()|\[\]\\]", input_pattern):
            # Treat as regex — search recursively under root_dir
            pattern = re.compile(input_pattern)
            for f in root_dir.rglob("*"):
                if f.is_file() and pattern.search(f.name):
                    matching_files.append(f.resolve())
        elif input_path and input_path.exists():
            matching_files.append(input_path)
        else:
            logger.warn(f"No matching file(s) found for pattern: {input_pattern}")
            continue

        for src_file in matching_files:
            src_file_resolved = src_file.resolve()

            if not getattr(args, "allow_reprocess", False) and src_file_resolved in processed_files:
                logger.info(f"Skipping {src_file.name} — already processed by previous entry")
                continue

            output_entry = entry.get("output")
            if output_entry:
                output_resolved = root_dir / output_entry if not Path(output_entry).is_absolute() else Path(output_entry)
                if output_resolved.suffix == "":
                    output_path = output_resolved / (src_file.stem + ".vtf")
                else:
                    output_path = output_resolved.with_suffix(".vtf")
            else:
                output_path = root_dir / (src_file.stem + ".vtf")

            output_path.parent.mkdir(parents=True, exist_ok=True)

            if not getattr(args, "forceupdate", False) and output_path.exists():
                if output_path.stat().st_mtime >= src_file.stat().st_mtime:
                    logger.info(f"Skipping {src_file.name} (already up-to-date)")
                    processed_files.add(src_file_resolved)
                    continue

            vtf_settings = entry.get("vtf", {})
            flags = vtf_settings.get("flags")
            extra_args = vtf_settings.get("encoder_args")

            logger.info(f"Converting: {src_file.name} -> {output_path.name}")
            try:
                export_vtf(
                    src_path=src_file,
                    dst_path=output_path,
                    vtfcmd=vtfcmd,
                    flags=flags,
                    extra_args=extra_args,
                )
                os.utime(output_path, (src_file.stat().st_atime, src_file.stat().st_mtime))
                logger.debug(f"Finished VTF: {output_path} (mtime synced to source)")
                processed_files.add(src_file_resolved)

            except Exception as e:
                logger.error(f"Failed to export {src_file} → {output_path}: {e}")

@timer
def main():
    print_header()

    global_parser = argparse.ArgumentParser(description="Source Resource Compiler", add_help=False)
    global_parser.add_argument("--config", "-config", required=True, metavar="CONFIG_JSON",
                               help="Path to config.json file containing header and pipeline data.")
    global_parser.add_argument("--log", action="store_true",
                               help="Enable logging to a file in 'resourcecompiler-log' relative to the config.json")
    global_parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    global_parser.add_argument("--dir",type=str,help="Absolute path to override input/output root directory for compiling")
    global_args, remaining_argv = global_parser.parse_known_args()

    logger = Logger(verbose=global_args.verbose, use_color=True)
    
    log_file = None
    if global_args.log:
        # Prefer --dir if provided, otherwise fallback to JSON file location
        if global_args.dir:
            clean_dir = str(global_args.dir).strip(' "\'')  # remove accidental quotes/spaces
            config_dir = Path(clean_dir).resolve()
        else:
            config_dir = Path(global_args.config).resolve().parent

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
        logger.error("Missing 'header' field in config.json — cannot determine pipeline type.")
        return logger
    
    parser = argparse.ArgumentParser(parents=[global_parser])
    if header == "ValveModel":
        parser.add_argument("--exportdir", metavar="COMPILE_DIR", default=DEFAULT_COMPILE_ROOT,
                            help=f"Root folder for compiled output (default: {DEFAULT_COMPILE_ROOT})")
        parser.add_argument("--nomaterial", action="store_true", help="Skip material mapping/copying for models.")
        parser.add_argument("--nolocalize", action="store_true", help="Disable material localization.")
        parser.add_argument("--sharedmaterials", action="store_true", help="Copy model materials into compile/Assetshared.")
        parser.add_argument("--vpk", action="store_true", help="Package each compiled subfolder into a VPK.")
        parser.add_argument("--archive", action="store_true", help="Archive existing compiled files instead of deletion")
        #parser.add_argument("--game", action="store_true", help="Compile the model in the game's directory and skip material collection and vpk")
        
    elif header == 'ValveTexture':
        parser.add_argument("--forceupdate", action="store_true",
                       help="Force reprocessing of all textures, even if output VTFs are up-to-date.")
        parser.add_argument("--allow_reprocess", action="store_true",
                       help="Allow the same source file to be processed multiple times if matched by multiple JSON entries.")

    args = parser.parse_args()
    
    if header == 'ValveModel':
        ValveModel(config, args, logger)
    elif header == 'ValveTexture':
        ValveTexture(config, args, logger)
    else:
        logger.error(f"Unknown pipeline header: {header}")
        return logger
    
    return logger

if __name__ == "__main__":
    main()
