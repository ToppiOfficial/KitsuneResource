from utils import SOFTVERSION

MAIN_SCRIPT = "main.py"
EXE_NAME = "kitsuneresource"
ICON_PATH = "icon.png"
ONE_FILE = True

import subprocess
import sys
import os
from pathlib import Path

def detect_environment():
    in_venv = hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )
    
    env_type = "virtual environment" if in_venv else "global Python"
    print(f"Using Python from: {env_type}")
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}\n")
    
    return in_venv

def build_executable():
    if not os.path.exists(MAIN_SCRIPT):
        print(f"Error: Main script '{MAIN_SCRIPT}' not found!")
        return False
    
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
    
    try:
        subprocess.check_call(cmd)
        print(f"\n✓ Build successful!")
        print(f"✓ Executable location: dist/{EXE_NAME}.exe" if sys.platform == "win32" else f"dist/{EXE_NAME}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Build failed with error code {e.returncode}")
        return False

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