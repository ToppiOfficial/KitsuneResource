import subprocess
from pathlib import Path
from utils import PrefixedLogger

def package_vpk(vpk_exe: Path, folder: Path, logger=None, verbose=False):
    """
    Package a folder into a VPK using the vpk.exe command line.

    :param vpk_exe: Path to vpk.exe
    :param folder: Folder to package (absolute or relative)
    :param logger: Optional Logger/PrefixedLogger for logging
    :param verbose: If True, show VPK stdout/stderr
    """
    folder = folder.resolve()
    if not folder.exists() or not folder.is_dir():
        if logger:
            logger.error(f"VPK packaging failed, folder not found: {folder}")
        return False

    cmd = [str(vpk_exe), str(folder)]
    if logger:
        vpk_logger = PrefixedLogger(logger, "VPK")
        vpk_logger.info(f"Packaging folder: {folder}")

    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=None if verbose else subprocess.DEVNULL,
            stderr=None if verbose else subprocess.DEVNULL
        )
        if logger:
            vpk_logger.info(f"Packaged folder into VPK: {folder}")
        return True
    except subprocess.CalledProcessError as e:
        if logger:
            vpk_logger.error(f"VPK packaging failed: {e}")
        return False
