import json, sys
from pathlib import Path
from typing import List, Optional

from .logger import Logger


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

    # In dev mode __file__ is intern/utils/config.py - go up 3 levels to reach the project root.
    base_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent.parent
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
                # In dev mode __file__ is intern/utils/config.py - go up 3 levels to project root.
                if getattr(sys, 'frozen', False):
                    base_dir = Path(sys.executable).parent
                else:
                    base_dir = Path(__file__).parent.parent.parent

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
