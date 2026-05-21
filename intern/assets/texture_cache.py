"""
core/texture_cache.py
---------------------
Content-based skip cache for the ValveTexture pipeline.

Instead of relying on file size or modification time, this module hashes
the raw bytes of each source image (SHA-256) and stores those signatures
in a small .texsig file that lives next to the config.  On subsequent
runs, if the digest still matches the file on disk the conversion step is
skipped, regardless of the image format, whether the file was re-saved,
or whether timestamps were touched by an external tool.

File format  (.texsig)
----------------------
Plain JSON, human-readable, safe to commit or delete at will:

    {
      "version": 1,
      "signatures": {
        "/absolute/path/to/source.png": "sha256hex...",
        ...*
      }
    }

Deleting the .texsig file is always safe - the pipeline will simply
re-process every file on the next run and rebuild it from scratch.
"""

import hashlib
import json
from pathlib import Path

# Bump this if the stored schema ever changes incompatibly.
_CACHE_VERSION = 1
# Custom extension that won't collide with anything in the project.
CACHE_EXTENSION = ".texsig"
# Read images in 64 KiB blocks to keep memory flat on large files.
_CHUNK = 65_536


class TextureSignatureCache:
    """
    Manages a per-config SHA-256 signature store for source images.

    Usage
    -----
    1.  Create via the factory:  cache = TextureSignatureCache.for_config(config_path)
    2.  Before converting a file:   if cache.is_unchanged(src): skip
    3.  After a successful convert: cache.record(src)
    4.  When the pipeline is done:  cache.save()
    """

    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path
        self._data: dict[str, str] = self._load()
        self._dirty = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_unchanged(self, src_file: Path) -> bool:
        """
        Return ``True`` when *src_file*'s current content matches the
        signature recorded from the last successful conversion.

        A missing entry (i.e. never processed before) always returns
        ``False`` so the file will be converted and recorded.
        """
        stored = self._data.get(str(src_file.resolve()))
        if stored is None:
            return False
        try:
            return stored == _sha256(src_file)
        except OSError:
            return False

    def record(self, src_file: Path) -> None:
        """
        Compute and store the SHA-256 digest for *src_file*.

        Call this immediately after a successful conversion so the next
        run can skip an identical file.
        """
        key = str(src_file.resolve())
        try:
            sig = _sha256(src_file)
        except OSError:
            return
        if self._data.get(key) != sig:
            self._data[key] = sig
            self._dirty = True

    def invalidate(self, src_file: Path) -> None:
        """Remove the stored signature for *src_file* (forces reprocess)."""
        key = str(src_file.resolve())
        if key in self._data:
            del self._data[key]
            self._dirty = True

    def save(self) -> None:
        """
        Write the cache to disk only when something actually changed.
        Safe to call even if nothing was recorded this run.
        """
        if not self._dirty:
            return
        payload = {"version": _CACHE_VERSION, "signatures": self._data}
        try:
            self._path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass  # Non-fatal - worst case we just reprocess next time.

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("version") == _CACHE_VERSION:
                sigs = raw.get("signatures", {})
                if isinstance(sigs, dict):
                    return sigs
        except Exception:
            pass
        # Corrupt or wrong version - start fresh.
        return {}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_config(cls, config_path: Path) -> "TextureSignatureCache":
        """
        Create (or load) a signature cache file named after *config_path*.
        """
        cache_file = config_path.with_suffix(CACHE_EXTENSION)
        return cls(cache_file)

    @classmethod
    def for_output_dir(cls, output_dir: Path) -> "TextureSignatureCache":
        """
        Create (or load) a signature cache file inside *output_dir*,
        named after the directory itself. This keeps the cache
        co-located with the VTFs it describes rather
        """
        cache_file = output_dir / (output_dir.name + CACHE_EXTENSION)
        return cls(cache_file)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _sha256(file_path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of *file_path*'s raw bytes."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()