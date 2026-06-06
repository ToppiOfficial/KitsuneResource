from pathlib import Path


class ProcessedAssetsTracker:
    """Session-scoped tracker for CRC-named files in .processed-assets/ directories.

    Each caching function (DMX edits, VRD scaling, static mesh bakes) calls
    claim() for every file it uses or writes during a build.  After all models
    are compiled, sweep() deletes any files in the touched directories that were
    never claimed, evicting stale entries left behind by previous source edits.

    The vrds/ subdirectory is excluded from sweeping because VRD generation uses
    fixed output names plus .sig sidecars and never accumulates stale copies.
    """

    def __init__(self):
        self._claimed: set[Path] = set()
        self._dirs: set[Path] = set()

    def claim(self, path: Path) -> None:
        resolved = path.resolve()
        self._claimed.add(resolved)
        self._dirs.add(resolved.parent)

    def sweep(self, logger=None) -> int:
        """Delete unclaimed files from every touched .processed-assets/ directory.

        Returns the number of files deleted.
        """
        deleted = 0
        for d in sorted(self._dirs):
            if not d.exists():
                continue
            for f in d.iterdir():
                if f.is_file() and f.resolve() not in self._claimed:
                    try:
                        f.unlink()
                        if logger:
                            logger.info(f"cache sweep: removed stale '{f.name}'")
                        deleted += 1
                    except OSError:
                        pass
        return deleted
