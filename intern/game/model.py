import subprocess, shutil, sys
from pathlib import Path
from intern.utils import Logger
from intern.formats.mdl import get_model_companion_files

def _extract_modelname(qc_file: Path) -> str | None:
    with qc_file.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if stripped.lower().startswith("$modelname"):
                parts = stripped.split(None, 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"')
    return None


def _get_studiomdl_output_path(studiomdl_exe: Path, qc_file: Path, game_dir: Path | None) -> Path | None:
    modelname = _extract_modelname(qc_file)
    if not modelname:
        return None
    base = game_dir if game_dir else studiomdl_exe.parent
    p = Path(modelname)
    if p.suffix.lower() != ".mdl":
        p = p.with_suffix(".mdl")
    return base / "models" / p


def _ensure_model_output_dir(studiomdl_exe: Path, qc_file: Path, game_dir: Path | None, log: Logger):
    mdl_path = _get_studiomdl_output_path(studiomdl_exe, qc_file, game_dir)
    if not mdl_path:
        return
    mdl_path.parent.mkdir(parents=True, exist_ok=True)
    log.debug(f"Pre-created model output dir: {mdl_path.parent}")


def model_compile_studiomdl(studiomdl_exe: str | Path, qc_file: str | Path, output_dir: str | Path = None,
                            game_dir: str | Path = None, vproject_dir: str | Path = None,
                            verbose: bool = False, logger: Logger = None,
                            wine_prefix: list[str] = []) -> tuple[bool, list[Path]]:
    studiomdl_exe = Path(studiomdl_exe).resolve()
    qc_file = Path(qc_file).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else None

    if qc_file.suffix.lower() != ".qc":
        raise ValueError("Only .qc files are allowed")

    log = logger or Logger(verbose=verbose)

    if sys.platform != "win32" and studiomdl_exe.suffix.lower() == ".exe" and not wine_prefix:
        log.warn(
            f"Tool '{studiomdl_exe.name}' is a Windows executable. "
            "Set 'wine_cmd' in config to run it via Wine on non-Windows systems."
        )

    cmd = wine_prefix + [str(studiomdl_exe), "-nop4", "-verbose"]
    if vproject_dir:
        cmd += ["-game", str(Path(vproject_dir).resolve())]
    cmd.append(str(qc_file))

    log.info(f"studiomdl args: {' '.join(cmd[1:])}")

    game_path = Path(vproject_dir) if vproject_dir else (Path(game_dir) if game_dir else None)
    _ensure_model_output_dir(studiomdl_exe, qc_file, game_path, log)

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=True
        )
        stdout = result.stdout or ""

        log.write_raw_to_log(stdout, source="studiomdl")
        _log_compiler_output_to_console(stdout, log, verbose)

        mdl_path = _get_studiomdl_output_path(studiomdl_exe, qc_file, game_path)
        moved_files = _move_compiled_files(mdl_path, output_dir, log)
        return True, moved_files

    except subprocess.CalledProcessError as e:
        log.error(f"Failed to compile {qc_file.name}")
        
        if e.stdout:
            log.write_raw_to_log(e.stdout, source="studiomdl STDOUT")
            _log_compiler_output_to_console(e.stdout, log, verbose)
        if e.stderr:
            log.write_raw_to_log(e.stderr, source="studiomdl STDERR")
            _log_compiler_output_to_console(e.stderr, log, verbose, is_stderr=True)
        return False, []

    except Exception as e:
        log.error(f"Unexpected exception compiling {qc_file.name}: {e}")
        return False, []


def _log_compiler_output_to_console(output: str, log: Logger, verbose: bool, is_stderr: bool = False):
    if not output:
        return

    if verbose:
        if is_stderr:
            log.error_console(output)
        else:
            log.debug_console(output)
        return

    ORANGE = "\033[38;5;208m"
    RED = "\033[91m"
    RESET = "\033[0m"

    for line in output.splitlines():
        line_stripped = line.strip()
        
        if not line_stripped:
            continue

        line_lower = line_stripped.lower()

        if "error" in line_lower or any(keyword in line_lower for keyword in ["failed", "cannot", "missing", "aborted"]):
            log.error_console(line_stripped)
        elif "warn" in line_lower:
            log.warn_console(line_stripped)
        elif line_stripped.startswith("$"):
            log.info_console(f"{ORANGE}{line_stripped}{RESET}")


def _move_compiled_files(mdl_path: Path | None, output_dir: Path | None, log: Logger) -> list[Path]:
    if not mdl_path or not mdl_path.exists():
        if mdl_path:
            log.warn(f"Expected output file missing: {mdl_path}")
        return []

    moved_files = []
    cleaned_dirs = set()

    for src_path in [mdl_path] + get_model_companion_files(mdl_path):
        if src_path.exists() and output_dir:
            try:
                rel_index = src_path.parts.index("models")
                rel_path = Path(*src_path.parts[rel_index:])
            except ValueError:
                rel_path = Path(src_path.name)

            dest_path = output_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if dest_path.exists():
                dest_path.unlink()
            shutil.move(str(src_path), str(dest_path))
            moved_files.append(dest_path)
            cleaned_dirs.add(src_path.parent)
            if src_path.suffix.lower() == ".mdl":
                log.info(f"Model output: {dest_path}")
            else:
                log.debug(f"Moved: {src_path.name} -> {dest_path}")

    _cleanup_empty_dirs(cleaned_dirs, log)
    return moved_files


def _cleanup_empty_dirs(dirs: set[Path], log: Logger):
    for folder in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            while folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
                log.debug(f"Removed empty folder: {folder}")
                folder = folder.parent
        except Exception:
            pass
