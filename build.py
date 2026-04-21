MAIN_SCRIPT = "main.py"
EXE_NAME = "kitsuneresource"
ICON_PATH = "icon.png"
ONE_FILE = True

UTILS_FILE = "utils.py"

import subprocess
import sys
import os
import re
from datetime import datetime


def detect_environment():
    in_venv = hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )
    
    env_type = "virtual environment" if in_venv else "global Python"
    print(f"Using Python from: {env_type}")
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}\n")
    
    return in_venv


def stamp_build(build_stamp: int) -> str:
    """
    Replaces SOFTBUILDDATE in utils.py with the given stamp.
    Returns the original file content so it can be restored later.
    """
    with open(UTILS_FILE, "r", encoding="utf-8") as f:
        original = f.read()

    patched = re.sub(
        r"^(SOFTBUILDDATE\s*=\s*).*$",
        rf"\g<1>{build_stamp}",
        original,
        flags=re.MULTILINE
    )

    with open(UTILS_FILE, "w", encoding="utf-8") as f:
        f.write(patched)

    return original


def restore_build(original_content: str):
    """Writes the original utils.py content back to disk."""
    with open(UTILS_FILE, "w", encoding="utf-8") as f:
        f.write(original_content)


def build_executable():
    if not os.path.exists(MAIN_SCRIPT):
        print(f"Error: Main script '{MAIN_SCRIPT}' not found!")
        return False

    if not os.path.exists(UTILS_FILE):
        print(f"Error: Utils file '{UTILS_FILE}' not found!")
        return False

    # Generate build stamp: YYYYMMDDHHmm  e.g. 202604212345
    now = datetime.now()
    build_stamp = int(now.strftime("%Y%m%d%H%M"))
    print(f"Build stamp: {build_stamp}  ({now.strftime('%Y-%m-%d %H:%M')})")

    # Patch utils.py and keep the original to restore after build
    original_utils = stamp_build(build_stamp)
    print(f"Stamped SOFTBUILDDATE = {build_stamp} into {UTILS_FILE}\n")

    cmd = [
        "pyinstaller",
        "--clean",
        "--noconfirm",
        f"--name={EXE_NAME}"
    ]

    if ONE_FILE:
        cmd.append("--onefile")

    if ICON_PATH and ICON_PATH.strip():
        if not os.path.exists(ICON_PATH):
            print(f"Warning: Icon file '{ICON_PATH}' not found. Building without custom icon.")
        else:
            cmd.append(f"--icon={ICON_PATH}")

    cmd.append(MAIN_SCRIPT)

    print(f"Building executable: {EXE_NAME}")
    print(f"Command: {' '.join(cmd)}\n")

    success = False
    try:
        subprocess.check_call(cmd)
        print(f"\nBuild successful!")
        print(
            f"Executable location: dist/{EXE_NAME}.exe"
            if sys.platform == "win32"
            else f"dist/{EXE_NAME}"
        )
        success = True
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Build failed with error code {e.returncode}")
    finally:
        # Always restore utils.py to its original state
        restore_build(original_utils)
        print(f"\nRestored {UTILS_FILE} to original state (SOFTBUILDDATE = 0).")

    return success


def main():
    print("=" * 50)
    print(f"Building: {EXE_NAME}")
    print(f"Main script: {MAIN_SCRIPT}")
    print(f"Icon: {ICON_PATH if ICON_PATH else 'None (default)'}")
    print(f"Mode: {'Single file' if ONE_FILE else 'Folder bundle'}")
    print("=" * 50)
    print()

    detect_environment()
    success = build_executable()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()