import shutil
import zipfile
import send2trash
from datetime import datetime
from pathlib import Path
from utils import Logger

class Archiver:
    """Handles cleanup and compressed archiving of the compile directory."""

    @staticmethod
    def clean(compile_root: Path, logger: Logger, archived: bool = False):
        os_logger = logger.with_context("OS")
        
        if not compile_root.exists() or not any(compile_root.iterdir()):
            os_logger.info("No existing compile folder to clean.")
            return
        
        if archived:
            Archiver._archive(compile_root, os_logger)
        else:
            Archiver._trash(compile_root, os_logger)

    @staticmethod
    def _archive(compile_root: Path, logger: Logger):
        try:
            # 1. Clear any .vpk or .gma files within the compiled folder first
            logger.info("Clearing package files (.vpk, .gma) before archiving...")
            for item in compile_root.rglob("*"):
                if item.is_file() and item.suffix.lower() in (".vpk", ".gma"):
                    try:
                        item.unlink()
                        logger.debug(f"Removed: {item.name}")
                    except Exception as e:
                        logger.warn(f"Could not remove {item.name}: {e}")

            archive_dir = compile_root.parent / "_archive"
            archive_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            archive_path = archive_dir / f"{timestamp}.zip"
            
            logger.info(f"Compressing to archive: {archive_path.name}")
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
                for file in compile_root.rglob('*'):
                    if file.is_file():
                        zipf.write(file, file.relative_to(compile_root))
            
            # 5. Delete the original folder after successful compression
            shutil.rmtree(compile_root)
            compile_root.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Successfully archived to: {archive_path}")
        except Exception as e:
            logger.error(f"Failed to archive and compress compile folder: {e}")

    @staticmethod
    def _trash(compile_root: Path, logger: Logger):
        """Original logic for sending to Recycle Bin."""
        logger.info("Cleaning existing compile folder (Send to Trash)...")
        try:
            send2trash.send2trash(compile_root)
            logger.info(f"Sent to Recycle Bin: {compile_root.name}")
            compile_root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to remove compile folder via send2trash: {e}")
            logger.info("Falling back to item-by-item deletion...")
            Archiver._trash_items(compile_root, logger)

    @staticmethod
    def _trash_items(compile_root: Path, logger: Logger):
        for item in compile_root.iterdir():
            try:
                send2trash.send2trash(item)
                logger.info(f"Sent to Recycle Bin: {item.relative_to(compile_root)}")
            except Exception as e:
                logger.warn(f"Failed to remove {item}: {e}")