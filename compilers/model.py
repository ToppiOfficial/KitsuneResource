import subprocess
from pathlib import Path
import shutil
import re

def model_compile_studiomdl(
    studiomdl_exe: str | Path,
    qc_file: str | Path,
    output_dir: str | Path = None,
    game_dir: str | Path = None,
    verbose: bool = False,
    logger: "Logger" = None,  # NEW: optional logger
) -> tuple[bool, list[Path], list[str]]:
    """
    Compile a Source model using studiomdl.exe and optionally return dumped materials.

    Args:
        studiomdl_exe: Path to studiomdl.exe
        qc_file: QC file to compile
        output_dir: Folder where compiled files should be moved
        game_dir: Optional game directory (passed to studiomdl)
        verbose: Whether to print full compiler stdout
        logger: Optional Logger instance (from utils.py)
    """
    studiomdl_exe = Path(studiomdl_exe).resolve()
    qc_file = Path(qc_file).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else None

    if qc_file.suffix.lower() != ".qc":
        raise ValueError("Only .qc files are allowed")

    log = logger or Logger(verbose=verbose)  # fallback to default logger if not passed

    cmd = [str(studiomdl_exe), "-nop4", "-verbose", "-dumpmaterials"]
    if game_dir:
        cmd += ["-game", str(Path(game_dir).resolve())]
    cmd.append(str(qc_file))

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        stdout = result.stdout
        if verbose:
            log.debug(stdout)
        else:
            #log.info(f"Compiled {qc_file.name} successfully.")
            pass

        # Extract compiled file paths
        mdl_matches = re.findall(r'writing\s+([^\n\r]+\.mdl)', stdout, flags=re.IGNORECASE)
        moved_files = []
        cleaned_dirs = set()

        for mdl_path_str in mdl_matches:
            mdl_path = Path(mdl_path_str.strip())
            if not mdl_path.exists():
                log.warn(f"Expected output file missing: {mdl_path}")
                continue

            base_name = mdl_path.stem
            folder = mdl_path.parent
            candidates = [
                mdl_path,
                folder / f"{base_name}.vvd",
                folder / f"{base_name}.ani",
                folder / f"{base_name}.phy",
            ]
            candidates += list(folder.glob(f"{base_name}*.vtx"))

            for src_path in candidates:
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
                    log.debug(f"Moved: {src_path} -> {dest_path}")

        # Clean up empty directories
        for folder in sorted(cleaned_dirs, key=lambda p: len(p.parts), reverse=True):
            try:
                while folder.exists() and not any(folder.iterdir()):
                    folder.rmdir()
                    log.debug(f"Removed empty folder: {folder}")
                    folder = folder.parent
            except Exception:
                pass

        # Extract materials from compiler output
        materials = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.lower().startswith("material"):
                parts = line.split(maxsplit=3)
                if len(parts) == 4:
                    materials.append(parts[3].replace("\\", "/"))

        materials = sorted(set(materials))
        log.debug(f"Found {len(materials)} unique materials.")

        return True, moved_files, materials

    except subprocess.CalledProcessError as e:
        log.error(f"Failed to compile {qc_file.name}")
        if verbose:
            log.debug("STDOUT:\n" + e.stdout)
            log.debug("STDERR:\n" + e.stderr)
        return False, [], []

    except Exception as e:
        log.error(f"Unexpected exception compiling {qc_file.name}: {e}")
        return False, [], []
