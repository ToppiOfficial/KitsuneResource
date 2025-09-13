# utils.py
import json
from pathlib import Path
from datetime import datetime

class Logger:
    """
    Simple logger with levels, optional color output, and optional file logging.
    """
    LEVELS = {"INFO": 1, "WARN": 2, "ERROR": 3, "DEBUG": 4}

    COLOR = {
        "INFO": "\033[92m",   # green
        "WARN": "\033[93m",   # yellow
        "ERROR": "\033[91m",  # red
        "DEBUG": "\033[94m",  # blue
        "RESET": "\033[0m"
    }

    def __init__(self, verbose=False, use_color=True, log_file=None):
        self.verbose = verbose
        self.use_color = use_color
        self.log_file = log_file  # Path or None

    def _write_to_file(self, text):
        if self.log_file:
            try:
                with self.log_file.open("a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception:
                # Fail silently on logging errors to avoid crashing main program
                pass

    def _print(self, level, message):
        if level == "DEBUG" and not self.verbose:
            return
        prefix = f"[{level}]"
        if self.use_color and level in self.COLOR:
            prefix = f"{self.COLOR[level]}{prefix}{self.COLOR['RESET']}"
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp} {prefix} {message}"
        print(line)
        self._write_to_file(line)

    def info(self, message):  self._print("INFO", message)
    def warn(self, message):  self._print("WARN", message)
    def error(self, message): self._print("ERROR", message)
    def debug(self, message): self._print("DEBUG", message)
        
class PrefixedLogger:
    """Logger wrapper to prepend a colored context prefix."""
    
    CONTEXT_COLOR = {
    "MODEL": "\033[95m",     # magenta
    "MATERIAL": "\033[96m",  # cyan
    "DATA": "\033[93m",      # yellow
    "VPK": "\033[94m",       # bright blue
    }

    def __init__(self, base_logger, context):
        self.logger = base_logger
        self.context = context.upper()
        self.prefix = f"[{self.context}]"
        if self.context in self.CONTEXT_COLOR:
            self.prefix = f"{self.CONTEXT_COLOR[self.context]}{self.prefix}\033[0m"

    def info(self, msg):
        self.logger.info(f"{self.prefix} {msg}")

    def warn(self, msg):
        self.logger.warn(f"{self.prefix} {msg}")

    def error(self, msg):
        self.logger.error(f"{self.prefix} {msg}")

    def debug(self, msg):
        self.logger.debug(f"{self.prefix} {msg}")

def resolve_json_path(json_path, config_file):
    """Resolve a path from JSON relative to the JSON file folder."""
    p = Path(json_path.lstrip("/\\"))  # remove leading slash to avoid root
    if not p.is_absolute():
        p = Path(config_file).parent / p
    return p.resolve()

def format_file_count(count):
    """Nice formatting for counts."""
    return f"{count:,}"

def relative_to_base(base_file: str | Path, *subpaths: str) -> Path:
    """
    Return a Path relative to the folder of `base_file`, optionally
    appending additional subpaths.

    Args:
        base_file: The reference file (e.g., a QC or JSON)
        *subpaths: Additional path parts to append

    Returns:
        Resolved Path object
    """
    base_folder = Path(base_file).parent.resolve()
    return base_folder.joinpath(*subpaths).resolve()

def rel_path(path: Path, base: Path) -> Path:
    """Return path relative to base, fallback to absolute if not possible."""
    try:
        return path.relative_to(base)
    except ValueError:
        return path

def parse_config_json(config_path):
    config_path = Path(config_path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    # Ensure structure is sane
    if "model" not in config or not isinstance(config["model"], dict):
        raise ValueError("Invalid config.json: 'model' must be a dictionary.")
    
    return config

def print_header():
    ascii_art = r"""
  _____  ______  _____  ____  _    _ _____   _____ ______ _____ ____  __  __ _____ _____ _      ______ _____  
 |  __ \|  ____|/ ____|/ __ \| |  | |  __ \ / ____|  ____/ ____/ __ \|  \/  |  __ \_   _| |    |  ____|  __ \ 
 | |__) | |__  | (___ | |  | | |  | | |__) | |    | |__ | |   | |  | | \  / | |__) || | | |    | |__  | |__) |
 |  _  /|  __|  \___ \| |  | | |  | |  _  /| |    |  __|| |   | |  | | |\/| |  ___/ | | | |    |  __| |  _  / 
 | | \ \| |____ ____) | |__| | |__| | | \ \| |____| |___| |___| |__| | |  | | |    _| |_| |____| |____| | \ \ 
 |_|  \_\______|_____/ \____/ \____/|_|  \_\\_____|______\_____\____/|_|  |_|_|   |_____|______|______|_|  \_\
"""

    # Center the extra lines based on the widest line in the ASCII art
    art_lines = ascii_art.splitlines()
    max_width = max(len(line) for line in art_lines)

    extra_lines = [
        "Resource Compiler v1.0",
        "by Toppi"
    ]

    centered_extra = "\n".join(line.center(max_width) for line in extra_lines)

    print(ascii_art + centered_extra + "\n")

