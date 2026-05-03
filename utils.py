import json, time, re, sys
from pathlib import Path
from datetime import datetime
from functools import wraps
from typing import List, Optional

SOFTVERSION = 4.0
SOFTBUILDDATE = 0

SUPPORTED_TEXT_FORMAT = (
    '.txt', '.lua', '.nut', '.cfg', '.json', '.xml', '.yaml', '.yml',
    '.ini', '.toml', '.md', '.shader', '.hlsl', '.glsl', '.jsonc', '.properties'
)

SUPPORTED_IMAGE_FORMAT = (
    '.jpg', '.jpeg', '.gif', '.psd', '.png', '.tiff', '.tga', '.bmp', 
    '.dds', '.hdr', '.exr', '.ico', '.webp', '.svg', '.apng', '.mks'
)

TEXTURE_KEYS = {
    "$basetexture", "$basetexture2", "$bumpmap", "$bumpmap2", "$normaltexture",
    "$lightwarptexture", "$phongexponenttexture", "$normalmap", "$emissiveblendbasetexture",
    "$emissiveblendtexture", "$emissiveblendflowtexture", "$ssbump", "$envmapmask",
    "$detail", "$detail2", "$blendmodulatetexture", "$AmbientOcclTexture", "$CorneaTexture",
    "$envmap", "$phongwarptexture", "$selfillummask", "$selfillumtexture", "$detail1",
    "$iris", "$mraotexture", "$paintsplatnormalmap", "$paintsplatbubblelayout",
    "$paintsplatbubble", "$paintenvmap", "$emissiontexture", "$emissiontexture2",
}


class Logger:
    LEVELS = {"INFO": 1, "WARN": 2, "ERROR": 3, "DEBUG": 4}

    COLOR = {
        "INFO": "\033[97m",
        "WARN": "\033[33m",
        "ERROR": "\033[91m",
        "DEBUG": "\033[35m",
        "RESET": "\033[0m"
    }
    
    CONTEXT_COLORS = {
        "MODEL": ("\033[95m", "MDL"),
        "MATERIAL": ("\033[96m", "MAT"),
        "DATA": ("\033[93m", "DAT"),
        "PACKAGER": ("\033[94m", "PACKAGER"),
        "OS": ("\033[92m", "OS"),
    }

    _ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def __init__(self, verbose=False, use_color=True, log_file=None, context=None, parent=None):
        if parent:
            self.verbose = parent.verbose
            self.use_color = parent.use_color
            self.log_file = parent.log_file
            self.root = parent.root if hasattr(parent, 'root') else parent
        else:
            self.verbose = verbose
            self.use_color = use_color
            self.log_file = log_file
            self.warn_count = 0
            self.error_count = 0
            self.root = self

            self.model_compiled    = 0
            self.model_total       = 0
            self.submodel_compiled = 0
            self.submodel_total    = 0
            self.data_compiled     = 0
            self.data_total        = 0

        self.context = context.upper() if context else None
        self.context_label = self.context
        
        if self.context:
            color, label = self.CONTEXT_COLORS.get(self.context, (None, self.context))
            self.context_label = label
            if color and self.use_color:
                self.prefix = f"{color}[{label}]{self.COLOR['RESET']}"
            else:
                self.prefix = f"[{label}]"
        else:
            self.prefix = ""

    def with_context(self, context: str) -> "Logger":
        return Logger(context=context, parent=self)

    def _write_to_file(self, text):
        if self.log_file:
            try:
                with self.log_file.open("a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception:
                pass

    def _print(self, level, message, console_only=False):
        if level == "WARN":
            self.root.warn_count += 1
        elif level == "ERROR":
            self.root.error_count += 1

        now = datetime.now()

        if self.verbose or level != "DEBUG":
            timestamp_console = now.strftime("%H:%M:%S")
            level_prefix_str = f"[{level}]"
            prefix_part = f"{self.prefix} " if self.prefix else ""

            if self.use_color and level in self.COLOR:
                level_color = self.COLOR[level]
                colored_level_prefix = f"{level_color}{level_prefix_str}{self.COLOR['RESET']}"
                
                if level == "INFO":
                    console_line = f"{timestamp_console} | {prefix_part}{message}"
                else:
                    colored_message = message.replace(self.COLOR['RESET'], level_color)
                    console_line = f"{timestamp_console} | {prefix_part}{colored_level_prefix} {level_color}{colored_message}{self.COLOR['RESET']}"
            else:
                if level == "INFO":
                    console_line = f"{timestamp_console} | {prefix_part}{message}"
                else:
                    console_line = f"{timestamp_console} | {prefix_part}{level_prefix_str} {message}"
            
            print(console_line)

        if self.log_file and not console_only:
            clean_message = self._ansi_escape.sub('', message)
            timestamp_file = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            context_str = f"[{self.context_label}] " if self.context_label else ""
            file_line = f"{timestamp_file}\t[{level.upper()}] {context_str}{clean_message}"
            self._write_to_file(file_line)

    def info(self, message): self._print("INFO", message)
    def warn(self, message): self._print("WARN", message)
    def error(self, message): self._print("ERROR", message)
    def debug(self, message): self._print("DEBUG", message)

    def write_raw_to_log(self, data, source="Generic"):
        if self.log_file:
            clean_data = self._ansi_escape.sub('', data)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            src = f"{self.context_label}/{source}" if self.context_label else source
            header = f"--- BEGIN {src} OUTPUT"
            footer = f"--- END {src} OUTPUT"
            full_log = f"{timestamp}\t{header}\n{clean_data}\n{timestamp}\t{footer}"
            self._write_to_file(full_log)
        
    def info_console(self, message): self._print("INFO", message, console_only=True)
    def warn_console(self, message): self._print("WARN", message, console_only=True)
    def error_console(self, message): self._print("ERROR", message, console_only=True)
    def debug_console(self, message): self._print("DEBUG", message, console_only=True)


class PathResolver:
    @staticmethod
    def resolve_and_validate(config: dict, *keys) -> List[Optional[Path]]:
        paths = []
        for key in keys:
            value = config.get(key)
            if value:
                path = Path(value).resolve()
                paths.append(path if path.exists() else None)
            else:
                paths.append(None)
        return paths
    
    @staticmethod
    def get_root_dir(args, config_path: Path) -> Path:
        if getattr(args, "basedir", None):
            root = Path(args.basedir).resolve()
            if not root.exists() or not root.is_dir():
                raise ValueError(f"Invalid path: {root}")
            return root
        return config_path.parent


def timer(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        logger = None
        try:
            logger = func(*args, **kwargs)
        finally:
            elapsed = time.time() - start_time
            if logger:
                logger.info('')
                logger.info("-" * 54)
                if logger.model_total > 0 or logger.data_total > 0:
                    logger.info(f"  {logger.model_compiled}/{logger.model_total} Models Compiled")
                    if logger.submodel_total > 0:
                        logger.info(f"  {logger.submodel_compiled}/{logger.submodel_total} Submodels Compiled")
                    logger.info(f"  {logger.data_compiled}/{logger.data_total} Data Compiled")
                    logger.info('')
                if logger.warn_count > 0 or logger.error_count > 0:
                    logger.info(f"Build finished with {logger.error_count} errors and {logger.warn_count} warnings.")
                logger.info(f"Total time elapsed: {elapsed:.2f} seconds")
                logger.info("-" * 54)
            else:
                print(f"Total time elapsed: {elapsed:.2f} seconds")
        return logger
    return wrapper


def resolve_json_path(json_path: str, config_file: Path, dir_override: Optional[Path] = None) -> Path:
    p = Path(str(json_path).strip("/\\"))

    if not p.is_absolute():
        if dir_override:
            p = Path(str(dir_override).strip(' "\'')) / p
        else:
            p = Path(config_file).parent / p

    return p.resolve()


def resolve_config_path(config_path_str: str, logger: Optional[Logger] = None) -> Optional[str]:
    
    # well shit.
    def log_info(msg):
        if logger: logger.info(msg)
    def log_warn(msg):
        if logger: logger.warn(msg)
    def log_error(msg):
        if logger: logger.error(msg)

    if not config_path_str or not config_path_str.strip():
        log_error("Config file path argument is empty. Please provide a valid path.")
        return None

    config_path = Path(config_path_str)
    if config_path.exists() and config_path.is_file():
        return str(config_path)

    base_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    configs_dir = base_dir / "configs"
    config_filename = config_path.name

    if not config_filename:
        log_warn(f"Could not determine a filename from '{config_path_str}'.")
        return None

    log_info(f"Config file not found at '{config_path_str}'. Searching in '{configs_dir}'...")

    if not configs_dir.is_dir():
        log_warn(f"The 'configs' directory does not exist at '{configs_dir}'.")
        return None

    candidate = configs_dir / config_path
    found_files = [candidate] if candidate.is_file() else [f for f in configs_dir.rglob(config_filename) if f.is_file()]

    if not found_files:
        log_warn(f"Could not find '{config_path_str}' in subfolders of '{configs_dir}'.")
        return None

    if len(found_files) > 1:
        log_warn(f"Found multiple '{config_filename}' files. Using the first one:")
        for f in found_files: log_warn(f"  - {f}")

    resolved = str(found_files[0])
    log_info(f"Found config file: {resolved}")
    return resolved


def deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def parse_config_json(config_path: str, seen_paths=None, filter_keys=None) -> dict:
    def first_key_hook(pairs):
        d = {}
        for key, value in pairs:
            if key not in d:
                d[key] = value
        return d
        
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

    try:
        with config_path.open("r", encoding="utf-8-sig") as f:
            config = json.load(f, object_pairs_hook=first_key_hook)
    except UnicodeDecodeError:
        with config_path.open("r", encoding="latin-1") as f:
            config = json.load(f, object_pairs_hook=first_key_hook)

    includes = config.get("include")
    if includes:
        if isinstance(includes, str):
            includes = [includes]

        included_data = {}
        for inc_path_str in includes:
            inc_path = Path(inc_path_str)
            if not inc_path.is_absolute() or not inc_path.exists():
                if getattr(sys, 'frozen', False):
                    base_dir = Path(sys.executable).parent
                else:
                    base_dir = Path(__file__).parent
                
                relative_to_config = config_path.parent / inc_path
                if relative_to_config.exists():
                    inc_path = relative_to_config.resolve()
                else:
                    fallback = base_dir / "configs" / inc_path
                    inc_path = fallback.resolve() if fallback.exists() else inc_path.resolve()
            else:
                inc_path = inc_path.resolve()

            inc_json = parse_config_json(inc_path, seen_paths, filter_keys=["include"] + filter_keys)
            included_data = deep_merge(included_data, inc_json)

        for key in filter_keys:
            included_data.pop(key, None)

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

    art_lines = ascii_art.splitlines()
    max_width = max(len(line) for line in art_lines)

    extra_lines = [f"KitsuneResource {SOFTVERSION} - {SOFTBUILDDATE}"]

    centered_extra = "\n".join(line.center(max_width) for line in extra_lines)

    print(ascii_art + centered_extra + "\n")