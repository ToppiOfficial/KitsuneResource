import sys, re
from pathlib import Path


def _find_steam_root() -> Path | None:
    if sys.platform == "win32":
        import winreg
        for hive, subkey in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ]:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, "InstallPath")
                    return Path(path)
            except OSError:
                pass

    # TODO: Double check if the common linux distros have identical installation path.
    # NOTE: This project is build to a .exe windows executable, so is this necessary?
    #       or compatibility layer such as WINE already handle this?

    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Steam"
    else:
        for candidate in [
            Path.home() / ".steam" / "steam",
            Path.home() / ".local" / "share" / "Steam",
        ]:
            if candidate.exists():
                return candidate
    return None


def get_steam_library_paths() -> list[Path]:
    """Return all Steam library steamapps/common directories found on this machine."""
    steam_root = _find_steam_root()
    if steam_root is None:
        return []

    libraries = []
    default_common = steam_root / "steamapps" / "common"
    if default_common.exists():
        libraries.append(default_common)

    for vdf_path in [
        steam_root / "config" / "libraryfolders.vdf",
        steam_root / "steamapps" / "libraryfolders.vdf",
    ]:
        if not vdf_path.exists():
            continue
        content = vdf_path.read_text(encoding="utf-8")
        # Matches both new format ("path" key) and old format (numeric keys like "1", "2")
        for match in re.finditer(r'"(?:path|\d+)"\s+"([^"]+)"', content, re.IGNORECASE):
            lib_common = Path(match.group(1).replace("\\\\", "\\")) / "steamapps" / "common"
            if lib_common.exists() and lib_common not in libraries:
                libraries.append(lib_common)
        break

    return libraries


def get_game_search_paths(gameinfo_file: str | Path) -> list[Path]:
    gameinfo_file = Path(gameinfo_file).resolve()
    if not gameinfo_file.exists():
        raise FileNotFoundError(f"{gameinfo_file} does not exist")

    base_dir = gameinfo_file.parent.parent
    paths = []

    with open(gameinfo_file, "r", encoding="utf-8") as f:
        content = f.read()

    search_paths_match = re.search(r"SearchPaths\s*{([^}]*)}", content, re.DOTALL | re.IGNORECASE)
    if not search_paths_match:
        return [base_dir]

    search_paths_block = search_paths_match.group(1)
    game_entries = re.findall(r'game(?:\+\w+)*\s+"?([^\s"]+)"?', search_paths_block, re.IGNORECASE)

    steam_libraries = None  # lazily populated only if needed

    for entry in game_entries:
        if "|gameinfo_path|" in entry:
            paths.append(base_dir)
            continue

        is_source_engine_path = "|all_source_engine_paths|" in entry.lower()
        entry = re.sub(r"\|all_source_engine_paths\|", "", entry, flags=re.IGNORECASE)

        if entry.startswith("|") or "addon" in entry.lower():
            continue

        if "*" in entry:
            for candidate in sorted(base_dir.glob(entry)):
                if candidate.is_dir() and candidate not in paths:
                    paths.append(candidate)
            continue

        if entry.endswith(".vpk"):
            entry = str(Path(entry).parent)

        candidate = (base_dir / entry).resolve()
        if candidate.exists():
            if candidate not in paths:
                paths.append(candidate)
        elif is_source_engine_path and ".." in entry:

            # The path references a sibling game (e.g. ../Counter-Strike Source/cstrike).
            # If it didn't resolve relative to base_dir, the game may be in a different
            # Steam library on another drive, so search all known library common dirs.
            #
            # I hate this...
            entry_parts = Path(entry).parts
            up_count = 0
            while up_count < len(entry_parts) and entry_parts[up_count] == "..":
                up_count += 1

            if up_count < len(entry_parts):
                remaining = Path(*entry_parts[up_count:])
                if steam_libraries is None:
                    steam_libraries = get_steam_library_paths()
                for lib_common in steam_libraries:
                    alt = (lib_common / remaining).resolve()
                    if alt.exists() and alt not in paths:
                        paths.append(alt)
                        break

    return paths