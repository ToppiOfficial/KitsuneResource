import re, os
from pathlib import Path
from typing import List, Set, Optional

from intern.utils import Logger, PathResolver, get_wine_prefix, print_wine_badge
from intern.assets.materials import export_vtf
from intern.assets.texture_cache import TextureSignatureCache


class ValveTexturePipeline:
    def __init__(self, config: dict, args, logger: Logger):
        self.config = config
        self.args = args
        self.logger = logger
        self.processed_files: Set[Path] = set()
        self._sig_cache: Optional[TextureSignatureCache] = None
        self.wine_prefix = get_wine_prefix(config)

    def execute(self):
        if self.wine_prefix:
            print_wine_badge(self.wine_prefix)

        import sys as _sys
        vtfcmd, = PathResolver.resolve_and_validate(self.config, "vtfcmd", logger=self.logger)

        if not vtfcmd:
            self.logger.error("vtfcmd not found in config")
            return

        if self.wine_prefix:
            self.logger.info(f"Wine prefix: {' '.join(self.wine_prefix)}")
        elif _sys.platform != "win32" and vtfcmd.suffix.lower() == ".exe":
            self.logger.warn(
                f"'{vtfcmd.name}' is a Windows executable. "
                "Set 'wine_cmd' in config to run it via Wine."
            )

        vtf_config = self.config.get("vtf", {})
        if not vtf_config:
            self.logger.warn("No 'vtf' section found in config - nothing to process")
            return

        try:
            root_dir = PathResolver.get_root_dir(self.args, Path(self.args.config_path).resolve())
            if getattr(self.args, "dir", None):
                self.logger.info(f"Overriding input/output root with --dir: {root_dir}")
        except ValueError as e:
            self.logger.error(str(e))
            return

        root_dir.mkdir(parents=True, exist_ok=True)
        self._sig_cache = TextureSignatureCache.for_output_dir(root_dir)
        self.logger.info(
            f"Texture signature cache: {root_dir / (root_dir.name + '.texsig')}"
        )

        for key, entry in vtf_config.items():
            self._process_texture_group(key, entry, root_dir, vtfcmd)

        self._sig_cache.save()

    def _process_texture_group(self, key: str, entry: dict, root_dir: Path, vtfcmd: Path):
        self.logger.info(f"Processing texture group: {key}")

        input_pattern = entry.get("input")
        if not input_pattern:
            self.logger.warn(f"Skipped {key} - missing 'input'")
            return

        matching_files = self._find_matching_files(input_pattern, root_dir)
        if not matching_files:
            self.logger.info(f"No matching file(s) found for pattern: {input_pattern}")
            return

        for src_file in matching_files:
            self._process_texture_file(src_file, entry, root_dir, vtfcmd)

    def _find_matching_files(self, pattern: str, root_dir: Path) -> List[Path]:
        if "*" in pattern or re.search(r"[.*+?^${}()|\[\]\\]", pattern):
            regex = re.compile(pattern)
            recursive = getattr(self.args, "recursive", False)
            glob_iter = root_dir.rglob("*") if recursive else root_dir.glob("*")
            return [f.resolve() for f in glob_iter if f.is_file() and regex.search(f.name)]

        input_path = root_dir / pattern
        return [input_path] if input_path.exists() else []

    def _process_texture_file(self, src_file: Path, entry: dict, root_dir: Path, vtfcmd: Path):
        src_file_resolved = src_file.resolve()

        if (not getattr(self.args, "allow_reprocess", False) and
                src_file_resolved in self.processed_files):
            self.logger.info(f"Skipping {src_file.name} - already processed")
            return

        output_path = self._resolve_output_path(src_file, entry, root_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if self._should_skip_conversion(src_file, output_path):
            self.logger.info(f"Skipping {src_file.name} (already up-to-date)")
            self.processed_files.add(src_file_resolved)
            return

        self._convert_to_vtf(src_file, output_path, entry, vtfcmd)
        self.processed_files.add(src_file_resolved)

    def _resolve_output_path(self, src_file: Path, entry: dict, root_dir: Path) -> Path:
        output_entry = entry.get("output")
        if not output_entry:
            return root_dir / (src_file.stem + ".vtf")

        output_resolved = (root_dir / output_entry if not Path(output_entry).is_absolute()
                           else Path(output_entry))

        if output_resolved.suffix == "":
            return output_resolved / (src_file.stem + ".vtf")
        return output_resolved.with_suffix(".vtf")

    def _should_skip_conversion(self, src_file: Path, output_path: Path) -> bool:
        if getattr(self.args, "forceupdate", False):
            return False
        if not output_path.exists():
            return False
        if self._sig_cache is not None:
            return self._sig_cache.is_unchanged(src_file)
        return False

    def _convert_to_vtf(self, src_file: Path, output_path: Path, entry: dict, vtfcmd: Path):
        vtf_settings = entry.get("vtf", {})
        flags = vtf_settings.get("flags")
        extra_args = vtf_settings.get("encoder_args")

        self.logger.info(f"Converting: {src_file.name} -> {output_path.name}")
        try:
            export_vtf(
                src_path=src_file,
                dst_path=output_path,
                vtfcmd=vtfcmd,
                flags=flags,
                extra_args=extra_args,
                wine_prefix=self.wine_prefix,
            )
            stat = src_file.stat()
            os.utime(output_path, (stat.st_atime, stat.st_mtime))
            self.logger.debug(f"Finished VTF: {output_path} (mtime synced to source)")

            if self._sig_cache is not None:
                self._sig_cache.record(src_file)
        except Exception as e:
            self.logger.error(f"Failed to export {src_file} -> {output_path}: {e}")
