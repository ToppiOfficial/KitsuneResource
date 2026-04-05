import subprocess
from pathlib import Path
from utils import PrefixedLogger

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


def package_archive(exe: Path, folder: Path, logger=None, verbose=False, **kwargs):
    """
    Package a folder into an archive using a supported tool (vpk.exe, gmad.exe, ...).

    :param exe: Path to the packaging executable
    :param folder: Folder to package
    :param logger: Optional Logger/PrefixedLogger for logging
    :param verbose: If True, show stdout/stderr
    :param kwargs: Extra args forwarded to the command builder (tool-specific)
    """
    tool = _TOOL_REGISTRY.get(exe.name.lower())
    if tool is None:
        if logger:
            logger.error(f"Unsupported packaging tool: {exe.name}")
        return False

    tool_name = tool["name"]
    folder = folder.resolve()

    if not folder.exists() or not folder.is_dir():
        if logger:
            logger.error(f"{tool_name} packaging failed, folder not found: {folder}")
        return False
    
    if validator := tool.get("validate"):
        if error := validator(folder):
            if logger:
                tool_logger = PrefixedLogger(logger, 'PACKAGER')
                tool_logger.error(f"{tool_name} packaging failed: {error} in {folder}")
            return False

    cmd = tool["build_cmd"](exe, folder, **kwargs)

    if logger:
        tool_logger = PrefixedLogger(logger, 'PACKAGER')
        tool_logger.debug(f"Packaging folder: {folder}")

    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=None if verbose else subprocess.DEVNULL,
            stderr=None if verbose else subprocess.DEVNULL,
        )
        if logger:
            tool_logger.info(f"Packaged folder: {folder}")
        return True
    except subprocess.CalledProcessError as e:
        if logger:
            tool_logger.error(f"{tool_name} packaging failed: {e}")
        return False