import subprocess
from pathlib import Path
from utils import Logger

def _build_vpk_cmd(exe: Path, folder: Path, **kwargs) -> list[str]:
    return [str(exe), str(folder)]


def _build_gmad_cmd(exe: Path, folder: Path, **kwargs) -> list[str]:
    return [str(exe), "create", "-folder", str(folder), "-warninvalid"]


def _validate_gmad(folder: Path) -> str | None:
    if not (folder / "addon.json").is_file():
        return "missing addon.json"
    return None


_TOOL_REGISTRY: dict[str, dict] = {
    "vpk.exe": {
        "name": "VPK",
        "build_cmd": _build_vpk_cmd,
    },
    "gmad.exe": {
        "name": "GMAD",
        "build_cmd": _build_gmad_cmd,
        "validate": _validate_gmad,
    },
}


def package_archive(exe: Path, folder: Path, logger: Logger = None, verbose=False, **kwargs):
    """
    Package a folder into an archive using a supported tool (vpk.exe, gmad.exe, ...).

    :param exe: Path to the packaging executable
    :param folder: Folder to package
    :param logger: Optional Logger for logging
    :param verbose: If True, show stdout/stderr
    :param kwargs: Extra args forwarded to the command builder (tool-specific)
    """
    tool = _TOOL_REGISTRY.get(exe.name.lower())
    pack_logger = logger.with_context("PACKAGER") if logger else None

    if tool is None:
        if pack_logger:
            pack_logger.error(f"Unsupported packaging tool: {exe.name}")
        return False

    tool_name = tool["name"]
    folder = folder.resolve()

    if not folder.exists() or not folder.is_dir():
        if pack_logger:
            pack_logger.error(f"{tool_name} packaging failed, folder not found: {folder}")
        return False
    
    if validator := tool.get("validate"):
        if error := validator(folder):
            if pack_logger:
                pack_logger.error(f"{tool_name} packaging failed: {error} in {folder}")
            return False

    cmd = tool["build_cmd"](exe, folder, **kwargs)

    try:
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            check=True
        )
        
        if pack_logger:
            if not verbose and result.stdout:
                pack_logger.write_raw_to_log(result.stdout, source=tool_name)
            pack_logger.info(f"Packaged folder: {folder.name}")
            
        return True
    except subprocess.CalledProcessError as e:
        if pack_logger:
            pack_logger.error(f"{tool_name} packaging failed (Exit Code {e.returncode})")
            if e.stdout:
                pack_logger.write_raw_to_log(e.stdout, source=f"{tool_name}_STDOUT")
            if e.stderr:
                pack_logger.write_raw_to_log(e.stderr, source=f"{tool_name}_STDERR")
        return False