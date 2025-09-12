from pathlib import Path
import re

def get_game_search_paths(gameinfo_file: str | Path) -> list[Path]:
    """
    Parse a Source engine gameinfo.txt and return absolute search paths.

    Args:
        gameinfo_file: Path to gameinfo.txt

    Returns:
        List of absolute Paths where game assets (materials, models, etc.) may exist.
    """
    gameinfo_file = Path(gameinfo_file).resolve()
    if not gameinfo_file.exists():
        raise FileNotFoundError(f"{gameinfo_file} does not exist")

    base_dir = gameinfo_file.parent.parent  # one level up from the folder containing gameinfo.txt
    paths = []

    with open(gameinfo_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract SearchPaths block
    search_paths_match = re.search(r"SearchPaths\s*{([^}]*)}", content, re.DOTALL | re.IGNORECASE)
    if not search_paths_match:
        return [base_dir]  # fallback

    search_paths_block = search_paths_match.group(1)
    # Find all Game entries
    game_entries = re.findall(r'Game\s+"?([^\s"]+)"?', search_paths_block, re.IGNORECASE)

    for entry in game_entries:
        if entry == "|gameinfo_path|.":
            paths.append(base_dir)
        else:
            candidate = (base_dir / entry).resolve()
            if candidate.exists():
                paths.append(candidate)

    return paths
