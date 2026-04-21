import vpk as vpklib
from pathlib import Path
from typing import List, Optional

# TODO: Replace the vpk as when reading L4D2's vpk it just doesn't work!! idk why
class GameVPKCache:
    """Lazily loads *_dir.vpk files from all game search paths and checks file membership."""

    def __init__(self, gameinfo_dir: Path, search_paths: List[Path] = None):
        self._pak_files: Optional[List] = None
        self._search_paths = search_paths if search_paths else [gameinfo_dir]

    def _load(self):
        self._pak_files = []
        for search_path in self._search_paths:
            for vpk_path in search_path.glob("*_dir.vpk"):
                try:
                    self._pak_files.append(vpklib.open(str(vpk_path), path_enc='latin-1'))
                except Exception:
                    pass

    def contains(self, rel_path: str) -> bool:
        if self._pak_files is None:
            self._load()
        rel_path_lower = rel_path.lower()
        for pak in self._pak_files:
            try:
                pak.get_file_meta(rel_path_lower)
                return True
            except KeyError:
                pass
        return False