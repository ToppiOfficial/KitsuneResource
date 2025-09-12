from pathlib import Path
from PIL import Image

def convert_image(src_path: Path, dst_path: Path) -> bool:
    """
    Converts an image to the output format if it's not a .vtf.
    Returns True if conversion happened, False otherwise.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)

    # Skip VTF files
    if dst_path.suffix.lower() == ".vtf":
        return False

    # Only attempt for known image formats
    if src_path.suffix.lower() not in [".png", ".tga", ".psd", ".jpg", ".jpeg", ".bmp"]:
        return False

    with Image.open(src_path) as img:
        # Convert to RGB for JPG
        if dst_path.suffix.lower() in [".jpg", ".jpeg"] and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst_path)
    return True
