import sys, copy, json
from pathlib import Path
from datetime import datetime
from typing import Optional
import argparse

from intern.utils import Logger, timer, print_header, parse_config_json, resolve_config_path
from intern.source.qc import process_qc_file
from intern.pipeline import PIPELINE_REGISTRY


def _normalize_args(argv: list) -> list:
    """Allow single-dash long options (e.g. -verbose) as an alias for --verbose."""
    normalized = []
    for arg in argv:
        if arg.startswith('-') and not arg.startswith('--') and len(arg) > 2:
            normalized.append('-' + arg)
        else:
            normalized.append(arg)
    return normalized


def process_direct_qc(qc_path_str: str, logger: Logger):
    qc_path = Path(qc_path_str).resolve()
    logger.info(f"Processing direct QC file: {qc_path.name}")
    try:
        qc_content, _ = process_qc_file(qc_path, logger=logger)
        processed_dir = qc_path.parent / "processed-qc"
        processed_dir.mkdir(parents=True, exist_ok=True)
        out_path = processed_dir / qc_path.name
        out_path.write_text(qc_content, encoding='utf-8')
        logger.info(f"Successfully processed QC to {out_path}")
    except Exception as e:
        logger.error(f"Failed to process direct QC: {e}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Source Resource Compiler")

    parser.add_argument("config_paths", metavar="INPUT_FILE", nargs="+",
                        help="One or more paths to config.json files or .qc files.")

    parser.add_argument("--log", action="store_true",
                        help="Enable logging to the './.resource-log' directory.")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable verbose logging.")

    model_group = parser.add_argument_group("ValveModel Pipeline")
    model_group.add_argument("--exportdir", metavar="COMPILE_DIR", default=None,
                             help="Root folder for compiled output. Defaults to each config's filename as folder name.")
    model_group.add_argument("--game", nargs='?', const=True, default=False,
                             help="Compile models directly to game directory. Optionally provide a path to a directory with gameinfo.txt to override config.")
    model_group.add_argument("--no-vproject", action="store_true",
                             help="Do not pass the gameinfo directory to studiomdl via -game flag.")
    model_group.add_argument("--mat-mode", type=int, default=2, choices=[0, 1, 2],
                             help="Material mode: 0=skip, 1=raw-local, 2=shared (default).")
    model_group.add_argument("--no-mat-local", action="store_true",
                             help="Disable material localization.")
    model_group.add_argument("--package-files", action="store_true",
                             help="Package each subfolder into VPK or GMA archive.")
    model_group.add_argument("--archive-old-ver", action="store_true",
                             help="Archive existing files instead of deletion.")
    model_group.add_argument("--single-addon", action="store_true",
                             help="Compile all output into a single addon folder defined by 'addonroot' in config.")
    model_group.add_argument("--only", metavar="ENTRY", action="append", default=None,
                             help="Only compile the specified model or data entry (case-insensitive). Can be specified multiple times.")

    texture_group = parser.add_argument_group("ValveTexture Pipeline")
    texture_group.add_argument("--forceupdate", action="store_true",
                               help="Force reprocessing all textures.")
    texture_group.add_argument("--allow_reprocess", action="store_true",
                               help="Allow same file to be processed multiple times.")
    texture_group.add_argument("--recursive", action="store_true",
                               help="Search for files recursively in subfolders.")

    return parser


@timer
def main():
    print_header()

    args = _build_arg_parser().parse_args(_normalize_args(sys.argv[1:]))

    log_file = None
    if args.log:
        log_dir = Path(".resource-log").resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = log_dir / f"{timestamp}.txt"

    logger = Logger(verbose=args.verbose, use_color=True, log_file=log_file)
    if log_file:
        logger.info(f"Logging enabled -> {log_file}")
        logger.info("")

    for config_path_str in args.config_paths:
        resolved_path = resolve_config_path(config_path_str, logger)
        if not resolved_path:
            logger.info("")
            continue

        if resolved_path.lower().endswith('.qc'):
            process_direct_qc(resolved_path, logger)
            logger.info("")
            continue

        try:
            config = parse_config_json(resolved_path)
        except (FileNotFoundError, ValueError) as e:
            logger.error(str(e))
            logger.info("")
            continue

        header = config.get("header")
        if not header:
            logger.error(
                f"[{Path(resolved_path).name}] Missing 'header' field in config "
                "- cannot determine pipeline type."
            )
            logger.info("")
            continue

        pipeline_cls = PIPELINE_REGISTRY.get(header)
        if pipeline_cls is None:
            logger.error(f"Unknown pipeline header: '{header}'")
            logger.info("")
            continue

        run_args = copy.copy(args)
        run_args.basedir = Path.cwd()
        run_args.config_path = resolved_path

        if args.exportdir is not None:
            run_args.exportdir = args.exportdir
        else:
            run_args.exportdir = Path(resolved_path).stem
            if len(args.config_paths) > 1:
                logger.info(
                    f"[{Path(resolved_path).name}] No --exportdir set, "
                    f"using '{run_args.exportdir}' as output folder."
                )

        try:
            pipeline_cls(config, run_args, logger).execute()
        except Exception as e:
            import traceback
            logger.error(f"Pipeline execution failed for '{Path(resolved_path).name}': {e}")
            traceback.print_exc()

        logger.info("")

    return logger
