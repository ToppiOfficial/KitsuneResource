import shutil
from pathlib import Path
from typing import Optional

from intern.utils import Logger, resolve_json_path, SUPPORTED_TEXT_FORMAT, SUPPORTED_IMAGE_FORMAT
from intern.assets.materials import export_vtf
from intern.assets.vmt import VMTCreator
from intern.assets.image import convert_image


class DataProcessor:
    def __init__(self, compile_root: Path, vtfcmd_exe: Optional[Path], args,
                 logger: Logger, include_dirs: list = None, wine_prefix: list = None):
        self.compile_root = compile_root
        self.vtfcmd_exe = vtfcmd_exe
        self.args = args
        self.logger = logger.with_context("DATA")
        self.include_dirs = include_dirs or []
        self.wine_prefix = wine_prefix or []
        self.handlers = [
            self._handle_text_replacement,
            self._handle_vtf_export,
            self._handle_image_conversion,
        ]

    def process_items(self, items: list, base_output: Path):
        for item in items:
            try:
                self._process_single_item(item, base_output)
            except Exception as e:
                self.logger.error(f"Failed to process item: {e}")

    def _process_single_item(self, item: dict, base_output: Path):
        input_raw = item.get("input")
        output_raw = item.get("output")
        if not input_raw or not output_raw:
            self.logger.error(f"Item is missing 'input' or 'output' key: {item}")
            return

        input_str = input_raw.strip()
        output_str = output_raw.strip()

        input_path = resolve_json_path(input_str, Path(self.args.config_path), self.args.basedir)
        output_path = base_output / Path(output_str)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        for handler in self.handlers:
            if handler(item, input_path, output_path, input_str, output_str):
                return

        self._copy_file(input_path, output_path)

    def _handle_text_replacement(self, item: dict, input_path: Path, output_path: Path,
                                  input_str: str, output_str: str) -> bool:
        if not (input_str.endswith(SUPPORTED_TEXT_FORMAT) and
                output_str.endswith(SUPPORTED_TEXT_FORMAT)):
            return False

        replace_map = item.get("replace")
        if not replace_map:
            return False

        try:
            text = input_path.read_text(encoding="utf-8")
            for k, v in replace_map.items():
                text = text.replace(k, v)
            output_path.write_text(text, encoding="utf-8")
            self.logger.info(f"Replaced strings: {input_path.name} -> {output_path.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed string replace: {input_path} -> {output_path} | {e}")
            return True

    def _handle_vtf_export(self, item: dict, input_path: Path, output_path: Path,
                            input_str: str, output_str: str) -> bool:
        if not ((input_str.endswith(SUPPORTED_IMAGE_FORMAT) or input_str.endswith('.vtf'))
                and output_str.endswith(".vtf")):
            return False

        vtf_data = item.get("vtf")

        if input_str.endswith(".vtf"):
            self._copy_file(input_path, output_path)
        elif self.vtfcmd_exe:
            try:
                export_vtf(
                    src_path=input_path,
                    dst_path=output_path,
                    vtfcmd=self.vtfcmd_exe,
                    flags=vtf_data.get("flags", []) if vtf_data else [],
                    extra_args=vtf_data.get("encoder_args", []) if vtf_data else [],
                    silent=True,
                    wine_prefix=self.wine_prefix,
                )
                self.logger.info(f"VTF export: {input_path.name} -> {output_path.name}")
            except Exception as e:
                self.logger.error(f"Failed to export VTF: {input_path} -> {output_path} | {e}")

        if vtf_data and vtf_data.get("vmt"):
            VMTCreator.create_from_template(
                vtf_data["vmt"], output_path, self.compile_root,
                self.args, self.logger, include_dirs=self.include_dirs,
            )
        return True

    def _handle_image_conversion(self, item: dict, input_path: Path, output_path: Path,
                                   input_str: str, output_str: str) -> bool:
        if not (input_str.endswith(SUPPORTED_IMAGE_FORMAT) and
                output_str.endswith(SUPPORTED_IMAGE_FORMAT)):
            return False

        try:
            if convert_image(input_path, output_path):
                self.logger.info(f"Converted image: {input_path.name} -> {output_path.name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to convert: {input_path} -> {output_path} | {e}")
            return True

    def _copy_file(self, input_path: Path, output_path: Path):
        shutil.copy2(input_path, output_path)
        self.logger.info(f"Copied file: {input_path.name} -> {output_path.name}")
