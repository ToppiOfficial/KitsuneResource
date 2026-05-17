import struct
from pathlib import Path
from typing import List, Optional

VPK_SIGNATURE = 0x55AA1234
_V1_HEADER_SIZE = 12   # 3 × uint32
_V2_HEADER_SIZE = 28   # 7 × uint32


class _VPKDir:
    """Parses a VPK dir file and supports O(1) path membership checks."""

    def __init__(self, path: Path):
        data = path.read_bytes()
        if len(data) < _V1_HEADER_SIZE:
            raise ValueError(f"VPK file too small: {path}")
        sig, ver, tree_size = struct.unpack_from('<3I', data, 0)
        if sig != VPK_SIGNATURE:
            raise ValueError(f"Not a VPK file (bad magic): {path}")
        if ver not in (1, 2):
            raise ValueError(f"Unsupported VPK version {ver}: {path}")
        tree_start = _V2_HEADER_SIZE if ver == 2 else _V1_HEADER_SIZE
        tree = data[tree_start: tree_start + tree_size]
        self.version = ver
        self.tree_size = tree_size
        self._paths = _parse_tree(tree)

    def __contains__(self, rel_path: str) -> bool:
        return rel_path.lower().replace('\\', '/') in self._paths


def _parse_tree(tree: bytes) -> set:
    pos = 0
    paths: set = set()

    def read_str() -> str:
        nonlocal pos
        end = tree.find(b'\x00', pos)  # find() returns -1 instead of raising ValueError
        if end == -1:
            return ''  # out-of-bounds → treat as terminator
        s = tree[pos:end].decode('latin-1')
        pos = end + 1
        return s

    while True:
        ext = read_str()
        if not ext:
            break
        while True:
            path = read_str()
            if not path:
                break
            while True:
                fname = read_str()
                if not fname:
                    break
                if pos + 18 > len(tree):
                    break  # not enough bytes for entry header
                # Entry metadata: crc(4) + preload_bytes(2) + archive_index(2) + offset(4) + length(4) + term(2) = 18 bytes
                preload_bytes = struct.unpack_from('<H', tree, pos + 4)[0]
                pos += 18 + preload_bytes
                path_clean = path.rstrip('\\/')
                rel = f"{fname}.{ext}" if path == ' ' else f"{path_clean}/{fname}.{ext}"
                paths.add(rel.lower().replace('\\', '/'))

    return paths


class GameVPKCache:
    """Lazily loads *_dir.vpk files from all game search paths and checks file membership."""

    def __init__(self, gameinfo_dir: Path, search_paths: List[Path] = None, logger=None):
        self._pak_files: Optional[List[_VPKDir]] = None
        self._search_paths = search_paths if search_paths else [gameinfo_dir]
        self._logger = logger

    def _load(self):
        self._pak_files = []
        for search_path in self._search_paths:
            for vpk_path in search_path.glob("*_dir.vpk"):
                self._logger and self._logger.debug(f"VPK: loading {vpk_path}")
                try:
                    d = _VPKDir(vpk_path)
                    self._pak_files.append(d)
                    self._logger and self._logger.debug(
                        f"VPK: {vpk_path.name} v{d.version} tree={d.tree_size}B — {len(d._paths)} paths"
                    )
                except Exception as e:
                    self._logger and self._logger.warn(
                        f"VPK: failed to load {vpk_path}: {e}"
                    )
        self._logger and self._logger.debug(
            f"VPK cache ready: {len(self._pak_files)} archive(s) loaded"
        )

    def contains(self, rel_path: str) -> bool:
        if self._pak_files is None:
            self._load()
        return any(rel_path in pak for pak in self._pak_files)
