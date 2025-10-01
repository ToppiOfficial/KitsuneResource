# utils.py
import json, time
from pathlib import Path
from datetime import datetime
from functools import wraps

SOFTVERSION = 1.0
SOFTVERSTATE = 'Beta'
DEFAULT_COMPILE_ROOT  = 'Resources-Compiled'
SUPPORTED_TEXT_FORMAT = (
    '.txt', '.lua', '.nut', '.cfg', '.json', '.xml', '.yaml', '.yml',
    '.ini', '.toml', '.md', '.shader', '.hlsl', '.glsl', '.jsonc', '.properties'
)

SUPPORTED_IMAGE_FORMAT = (
    '.jpg', '.jpeg', '.gif', '.psd', '.png', '.tiff', '.tga', '.bmp', 
    '.dds', '.hdr', '.exr', '.ico', '.webp', '.svg', '.apng'
)

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
    "OS": "\033[92m",        # green (for filesystem operations)
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
        
def timer(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        logger = None
        try:
            # main() now returns logger so wrapper can use it
            logger = func(*args, **kwargs)
        finally:
            elapsed = time.time() - start_time
            if logger:
                logger.info(f"Total time elapsed: {elapsed:.2f} seconds")
            else:
                print(f"[INFO] Total time elapsed: {elapsed:.2f} seconds")
        return logger
    return wrapper

def resolve_json_path(json_path, config_file, dir_override=None):
    """Resolve a path from JSON relative to --dir if provided, otherwise relative to the JSON file folder."""
    # Clean json_path (remove accidental leading slashes)
    p = Path(json_path.lstrip("/\\"))

    # Clean dir_override if present (strip quotes, spaces)
    clean_dir = None
    if dir_override:
        clean_dir = str(dir_override).strip(' "\'')

    # Resolve relative paths
    if not p.is_absolute():
        if clean_dir:  # prefer --dir root
            p = Path(clean_dir) / p
        else:  # fallback to JSON folder
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

def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge two dicts. override takes precedence over base.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result

def parse_config_json(config_path: str, seen_paths=None, filter_keys=None) -> dict:
    """
    Load a config.json for Source Resource Compiler with optional 'include' support.
    Included JSONs are merged recursively. Current JSON values override included JSONs.
    
    Args:
        config_path: Path to the main JSON file.
        seen_paths: Internal set to detect circular includes (do not pass manually).
        filter_keys: Optional list of keys to exclude from included JSONs.
                     By default we exclude "include" itself to prevent infinite recursion.
    """
    if seen_paths is None:
        seen_paths = set()
    if filter_keys is None:
        filter_keys = []

    config_path = Path(config_path).resolve()
    if config_path in seen_paths:
        raise ValueError(f"Circular include detected: {config_path}")
    seen_paths.add(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    includes = config.get("include")
    if includes:
        if isinstance(includes, str):
            includes = [includes]
        included_data = {}
        for inc_path in includes:
            inc_path = Path(inc_path).resolve()
            # Always filter out "include" from included JSONs to avoid nested includes
            inc_json = parse_config_json(inc_path, seen_paths, filter_keys=["include"] + filter_keys)
            included_data = deep_merge(included_data, inc_json)
        config = deep_merge(included_data, config)

    if "header" not in config:
        raise ValueError("Invalid config.json: missing 'header' field.")

    return config

def print_header():
    ascii_art = r"""
  _  _______ _______ _____ _    _ _   _ ______ _____  ______  _____  ____  _    _ _____   _____ ______ 
 | |/ /_   _|__   __/ ____| |  | | \ | |  ____|  __ \|  ____|/ ____|/ __ \| |  | |  __ \ / ____|  ____|
 | ' /  | |    | | | (___ | |  | |  \| | |__  | |__) | |__  | (___ | |  | | |  | | |__) | |    | |__   
 |  <   | |    | |  \___ \| |  | | . ` |  __| |  _  /|  __|  \___ \| |  | | |  | |  _  /| |    |  __|  
 | . \ _| |_   | |  ____) | |__| | |\  | |____| | \ \| |____ ____) | |__| | |__| | | \ \| |____| |____ 
 |_|\_\_____|  |_| |_____/ \____/|_| \_|______|_|  \_\______|_____/ \____/ \____/|_|  \_\\_____|______|
                                                                                                       
"""

    # Center the extra lines based on the widest line in the ASCII art
    art_lines = ascii_art.splitlines()
    max_width = max(len(line) for line in art_lines)

    extra_lines = [
        f"KitsuneResource {SOFTVERSTATE} {SOFTVERSION}",
        "by Toppi"
    ]

    centered_extra = "\n".join(line.center(max_width) for line in extra_lines)

    print(ascii_art + centered_extra + "\n")

