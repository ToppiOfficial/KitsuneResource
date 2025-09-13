import argparse, shutil, time, send2trash
from pathlib import Path
from utils import *
from compilers import materials
from compilers.model import model_compile_studiomdl
from compilers.gameinfo import get_game_search_paths
from compilers.vpk import package_vpk
from compilers.image import convert_image

DEFAULT_COMPILE_ROOT  = 'Resources-Compiled'
SUPPORTED_TEXT_FORMAT = (
    '.txt', '.lua', '.nut', '.cfg', '.json', '.xml', '.yaml', '.yml',
    '.ini', '.toml', '.md', '.shader', '.hlsl', '.glsl', '.jsonc', '.properties'
)

SUPPORTED_IMAGE_FORMAT = (
    '.jpg', '.jpeg', '.gif', '.psd', '.png', '.tiff', '.tga', '.bmp', 
    '.dds', '.hdr', '.exr', '.ico', '.webp', '.svg', '.apng'
)

def main():
    start_time = time.time()
    print_header()

    parser = argparse.ArgumentParser(description="Source Resource Compiler")
    parser.add_argument("-config", required=True, metavar="CONFIG_JSON",
                        help="Path to config.json file containing studiomdl, gameinfo, model/material entries.")
    parser.add_argument("--exportdir", metavar="COMPILE_DIR", default=DEFAULT_COMPILE_ROOT,
                        help=f"Root folder for compiled output (default: {DEFAULT_COMPILE_ROOT})")
    parser.add_argument("--log", action="store_true",
                        help="Enable logging to a file in 'resourcecompiler-log' relative to the config.json")
    parser.add_argument("--nomaterial", action="store_true", help="Skip material mapping/copying for models.")
    parser.add_argument("--nolocalize", action="store_true", help="Disable material localization (use original folder layout).")
    parser.add_argument("--sharedmaterials", action="store_true", help="Copy model materials into compile/Assetshared instead of model-specific folder.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    parser.add_argument("--vpk", action="store_true", help="Package each compiled subfolder into a VPK.")
    args = parser.parse_args()

    logger = Logger(verbose=args.verbose, use_color=True)
    
    log_file = None
    if args.log:
        config_dir = Path(args.config).resolve().parent
        log_dir = config_dir / "resourcecompiler-log"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = log_dir / f"resourcecompiler-{timestamp}.txt"

    logger = Logger(verbose=args.verbose, use_color=True, log_file=log_file)
    if log_file:
        logger.info(f"Logging enabled → {log_file}")
    
    if args.exportdir is None or str(args.exportdir).strip() == "":
        compile_root = Path(DEFAULT_COMPILE_ROOT)
        logger.warn(f"--compile-root not provided or empty, falling back to default: {compile_root}")
    else:
        compile_root = Path(args.exportdir).resolve()

    # ==== LOAD CONFIG JSON ====
    try:
        config = parse_config_json(args.config)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        return

    # ==== REQUIRED FIELDS ====
    studiomdl_exe = Path(config.get("studiomdl", "")).resolve()
    gameinfo_path = Path(config.get("gameinfo", "")).resolve()
    models = config.get("model", {})
    material_sets = config.get("material", {})

    if not studiomdl_exe.exists() or not gameinfo_path.exists() or (not models and not material_sets):
        logger.error("Config.json missing required fields or has nothing to process.")
        return

    # ==== GAME SEARCH PATHS ====
    search_paths = get_game_search_paths(gameinfo_path)
    logger.info("Game search paths:")
    for p in search_paths:
        logger.info(f"\t{p}")

    localize = not args.nolocalize
    
    # ==== CLEAN COMPILE FOLDER ====
    compile_root = Path(compile_root)
    if compile_root.exists() and any(compile_root.iterdir()):
        logger.info("Cleaning existing compile folder...")
        for item in compile_root.iterdir():
            try:
                send2trash.send2trash(item)
                logger.info(f"Sent to Recycle Bin: {item}")
            except Exception as e:
                logger.warn(f"Failed to remove {item}: {e}")

    # ==== VTF CMD ====
    vtfcmd_path = config.get("vtfcmd")
    vtfcmd_exe = Path(vtfcmd_path).resolve() if vtfcmd_path else None
    if vtfcmd_exe and not vtfcmd_exe.exists():
        logger.warn(f"vtfcmd.exe not found at: {vtfcmd_exe} — VTF conversions will be skipped.")
        vtfcmd_exe = None

    # ==== COMPILE MODELS ====
    for model_name, model_data in models.items():
        model_logger = PrefixedLogger(logger, "MODEL")

        # Default to True if "compile" not specified
        compile_model = model_data.get('compile', True)
        if not compile_model:
            model_logger.warn(f"Skipping model {model_name} because compile=false.")
            continue  # skip this model entirely

        qc_path = Path(model_data.get("qc")).resolve()
        if not qc_path.exists():
            model_logger.error(f"QC file not found: {qc_path}")
            continue

        output_dir = Path(compile_root) / model_name
        output_dir.mkdir(parents=True, exist_ok=True)
        model_logger.info(f"Compiling model {qc_path.name}")

        success, compiled_files, dumped_materials = model_compile_studiomdl(
            studiomdl_exe=studiomdl_exe,
            qc_file=qc_path,
            output_dir=output_dir,
            game_dir=None,
            verbose=args.verbose,
            logger=logger,
        )

        if not success:
            model_logger.error("Main QC compilation failed.")
            continue

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
                sub_dumped_materials = set(sub_dumped_materials)
                dumped_materials.update(sub_dumped_materials)
                model_logger.info(f"Compiled {sub_qc_path.name} ({len(sub_dumped_materials)} materials)")

        # Material copy
        if not args.nomaterial:
            copy_target = Path(f"{compile_root}/Assetshared") if args.sharedmaterials else output_dir
            copy_target.mkdir(parents=True, exist_ok=True)
            model_logger.info(f"Copying {len(dumped_materials)} materials to {copy_target}...")
            material_to_vmt = materials.map_materials_to_vmt(dumped_materials, search_paths)
            copied_files = materials.copy_materials(
                material_to_vmt,
                copy_target,
                search_paths,
                localize_data=localize,
                logger=logger,
            )
            model_logger.info(f"Material copy complete ({len(copied_files)} files).")
        else:
            model_logger.warn("Skipping model material copying (-nomaterial).")

        # subdata
        for subitem in model_data.get("subdata", []):
            mat_logger = PrefixedLogger(logger, "MATERIAL")
            subdata_logger = PrefixedLogger(logger, "DATA")
            input_path = resolve_json_path(subitem.get("input"), args.config)

            subitem_output_str : str = subitem.get('output').strip()
            subitem_input_str : str = subitem.get('input').strip()

            export_path = output_dir / Path(subitem.get("output"))
            export_path.parent.mkdir(parents=True, exist_ok=True)

            # ---- REPLACE STRINGS ----
            if subitem_input_str.endswith(SUPPORTED_TEXT_FORMAT) and subitem_output_str.endswith(SUPPORTED_TEXT_FORMAT):
                replace_map = subitem.get("replace")  # dict {search: replace}
                if replace_map:
                    try:
                        text = input_path.read_text(encoding="utf-8")
                        for k, v in replace_map.items():
                            text = text.replace(k, v)
                        export_path.write_text(text, encoding="utf-8")
                        subdata_logger.info(f"Replaced strings in: {input_path.name} -> {export_path.name}")
                        continue  # skip VTF/copy if replacement handled
                    except Exception as e:
                        subdata_logger.error(f"Failed string replace: {input_path.name} -> {export_path.name} | {e}")

            elif subitem_input_str.endswith(SUPPORTED_IMAGE_FORMAT) and subitem_output_str.endswith(".vtf"):
                # ---- VTF EXPORT ----
                vtf_data = subitem.get("vtf")
                if vtf_data and vtfcmd_exe and subitem_output_str.strip().endswith('.vtf'):
                    try:
                        materials.export_vtf(
                            src_path=input_path,
                            dst_path=export_path,
                            vtfcmd=vtfcmd_exe,
                            flags=vtf_data.get("flags", []),
                            extra_args=vtf_data.get("encoder_args", []),
                            silent=True
                        )
                        subdata_logger.info(f"VTF export: {input_path.name} -> {export_path.name}")

                        # ---- VMT CREATION ----
                        if vtf_data:
                            vmt_json = vtf_data.get("vmt")  # now inside vtf block
                            if vmt_json:
                                vmt_template = resolve_json_path(vmt_json, args.config)
                                if vmt_template.exists():
                                    vmt_dst = export_path.with_suffix(".vmt")

                                    # Compute VTF relative path without extension
                                    materials_root = Path(compile_root) / model_name / "materials"
                                    if not export_path.is_relative_to(materials_root):
                                        vtf_rel_posix = export_path.stem
                                    else:
                                        vtf_rel = export_path.relative_to(materials_root).with_suffix("")
                                        vtf_rel_posix = vtf_rel.as_posix()

                                    template_lines = vmt_template.read_text(encoding="utf-8").splitlines()
                                    new_lines = []

                                    for line in template_lines:
                                        stripped = line.strip()

                                        # Replace $basetexture
                                        if stripped.startswith("$basetexture"):
                                            leading_ws = line[:line.index("$basetexture")]
                                            new_lines.append(f'{leading_ws}$basetexture "{vtf_rel_posix}"')
                                            continue

                                        # Strip quotes from $ keys
                                        if stripped.startswith('"$'):
                                            first_space = line.find(' ')
                                            key = line[:first_space].replace('"', '')  # remove quotes from key
                                            rest = line[first_space+1:]
                                            new_lines.append(f'{key} {rest}')
                                            continue

                                        new_lines.append(line)

                                    vmt_dst.write_text("\n".join(new_lines), encoding="utf-8")
                                    subdata_logger.info(f"VMT created: {vmt_dst.relative_to(Path(compile_root))}")
                                else:
                                    subdata_logger.warn(f"VMT template not found, skipping: {vmt_template}")
                                
                    except Exception as e:
                        subdata_logger.error(f"Failed to export VTF: {input_path.name} -> {export_path.name} | {e}")
                    
            elif subitem_input_str.endswith(SUPPORTED_IMAGE_FORMAT) and subitem_output_str.endswith(SUPPORTED_IMAGE_FORMAT):
                try:
                    converted = convert_image(input_path, export_path)
                    if converted:
                        subdata_logger.info(f"Converted image: {input_path.name} -> {export_path.name}")
                except Exception as e:
                    subdata_logger.error(f"Failed to copy/convert subdata: {input_path.name} -> {export_path.name} | {e}")
                    
            else:
                shutil.copy2(input_path, export_path)
                subdata_logger.info(f"Copied file: {input_path.name} -> {export_path.name}")

    # ==== MATERIAL-ONLY EXPORT ====
    for set_name, set_data in material_sets.items():
        mat_logger = PrefixedLogger(logger, "MATERIAL")
        vmt_list = set_data.get("materials", [])
        if not vmt_list:
            mat_logger.warn("No materials listed — skipping.")
            continue
        
        mat_logger.info(f'[{set_name}]')

        output_dir = Path(compile_root) / set_name
        output_dir.mkdir(parents=True, exist_ok=True)
        material_to_vmt = {Path(vmt): Path(vmt) for vmt in vmt_list}
        copied_files = materials.copy_materials(
            material_to_vmt,
            output_dir,
            search_paths,
            localize_data=localize,
            logger=logger,
        )
        mat_logger.info(f"Material-only copy complete ({len(copied_files)} files).")

    # ---- TOP-LEVEL DATA ----
    for folder_name, items in config.get("data", {}).items():
        data_logger = PrefixedLogger(logger, "DATA")
        export_base = Path(compile_root) / folder_name
        export_base.mkdir(parents=True, exist_ok=True)

        for item in items:
            input_path = resolve_json_path(item.get("input"), args.config)
            export_path = export_base / Path(item.get("output"))
            export_path.parent.mkdir(parents=True, exist_ok=True)
            
            item_output_str : str = item.get("output").strip()
            item_input_str : str = item.get("input").strip()

            if item_input_str.endswith(SUPPORTED_TEXT_FORMAT) and item_output_str.endswith(SUPPORTED_TEXT_FORMAT):
                replace_map = item.get("replace")
                if replace_map:
                    try:
                        text = input_path.read_text(encoding="utf-8")
                        for k, v in replace_map.items():
                            text = text.replace(k, v)
                        export_path.write_text(text, encoding="utf-8")
                        data_logger.info(f"Replaced strings in: {input_path.name} -> {export_path.name}")
                        continue  # skip VTF/copy if replacement handled
                    except Exception as e:
                        data_logger.error(f"Failed string replace: {input_path.name} -> {export_path.name} | {e}")
            
            elif (item_input_str.endswith(SUPPORTED_IMAGE_FORMAT) or item_input_str.endswith('.vtf'))and item_output_str.endswith(".vtf"):
                vtf_data = item.get("vtf")
                
                if not item_input_str.endswith(".vtf"):
                    materials.export_vtf(
                            src_path=input_path,
                            dst_path=export_path,
                            vtfcmd=vtfcmd_exe,
                            flags=vtf_data.get("flags", []),
                            extra_args=vtf_data.get("encoder_args", []),
                            silent=True
                        )
                    subdata_logger.info(f"VTF export: {input_path.name} -> {export_path.name}")
                
                if vtf_data and item_output_str.endswith('.vtf'):
                    vmt_json = vtf_data.get("vmt")  # now inside vtf block
                    if vmt_json:
                        vmt_template = resolve_json_path(vmt_json, args.config)
                        if vmt_template.exists():
                            vmt_dst = export_path.with_suffix(".vmt")

                            # Compute VTF relative path without extension
                            materials_root = Path(compile_root) / "AssetShared"

                            # Compute relative path to this root, strip ".vtf" extension
                            vtf_rel = export_path.relative_to(materials_root).with_suffix("")
                            vtf_rel_posix = vtf_rel.as_posix()

                            # Remove leading "materials/" if present
                            if vtf_rel_posix.startswith("materials/"):
                                vtf_rel_posix = vtf_rel_posix[len("materials/"):]

                            template_lines = vmt_template.read_text(encoding="utf-8").splitlines()
                            new_lines = []

                            for line in template_lines:
                                stripped = line.strip()

                                # Replace $basetexture
                                if stripped.startswith("$basetexture"):
                                    leading_ws = line[:line.index("$basetexture")]
                                    new_lines.append(f'{leading_ws}$basetexture "{vtf_rel_posix}"')
                                    continue

                                # Strip quotes from $ keys (keep values intact)
                                if stripped.startswith('"$'):
                                    first_space = line.find(' ')
                                    key = line[:first_space].replace('"', '')  # remove quotes from key
                                    rest = line[first_space+1:]
                                    new_lines.append(f'{key} {rest}')
                                    continue

                                new_lines.append(line)

                            vmt_dst.write_text("\n".join(new_lines), encoding="utf-8")
                            data_logger.info(f"VMT created: {vmt_dst.relative_to(Path(compile_root))}")
                        else:
                            data_logger.warn(f"VMT template not found, skipping: {vmt_template}")
                        
            elif item_input_str.endswith(SUPPORTED_IMAGE_FORMAT) and item_output_str.endswith(SUPPORTED_IMAGE_FORMAT):
                try:
                    converted = convert_image(input_path, export_path)
                    if converted:
                        data_logger.info(f"Converted image: {input_path.name} -> {export_path.name}")
                    
                except Exception as e:
                    data_logger.error(f"Failed to copy/convert: {input_path.name} -> {export_path.name} | {e}")

            else:
                shutil.copy2(input_path, export_path)
                data_logger.info(f"Copied file: {input_path.name} -> {export_path.name}")
                        
    vpk_exe = Path(config.get("vpk")).resolve() if config.get("vpk") else None
    if args.vpk and (not vpk_exe.exists() if vpk_exe else True):
        logger.warn(f"vpk.exe not found or missing in config, skipping VPK packaging.")
        args.vpk = False

    if args.vpk and vpk_exe:
        compile_root = Path(compile_root)
        for subfolder in compile_root.iterdir():
            if subfolder.is_dir():
                package_vpk(vpk_exe, subfolder, logger)

    # ==== TOTAL TIME ====
    elapsed = time.time() - start_time
    logger.info(f"Total time elapsed: {elapsed:.2f} seconds")


if __name__ == "__main__":
    main()
