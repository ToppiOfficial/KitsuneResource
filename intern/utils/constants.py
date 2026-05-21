import hashlib
import sys

IS_DEV_BUILD: bool = True
SOFTVERSION: str = "0"
SOFTBUILDDATE: str = "dev"

def _compute_exe_sha256() -> "str | None":
    if IS_DEV_BUILD:
        return None
    try:
        h = hashlib.sha256()
        with open(sys.executable, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None

SOFTSHA256: "str | None" = _compute_exe_sha256()

SUPPORTED_TEXT_FORMAT = (
    '.txt', '.lua', '.nut', '.cfg', '.json', '.xml', '.yaml', '.yml',
    '.ini', '.toml', '.md', '.shader', '.hlsl', '.glsl', '.jsonc', '.properties'
)

SUPPORTED_IMAGE_FORMAT = (
    '.jpg', '.jpeg', '.gif', '.psd', '.png', '.tiff', '.tga', '.bmp',
    '.dds', '.hdr', '.exr', '.ico', '.webp', '.svg', '.apng', '.mks'
)

TEXTURE_KEYS = {
    "$basetexture", "$basetexture2", "$bumpmap", "$bumpmap2", "$normaltexture",
    "$lightwarptexture", "$phongexponenttexture", "$normalmap", "$emissiveblendbasetexture",
    "$emissiveblendtexture", "$emissiveblendflowtexture", "$ssbump", "$envmapmask",
    "$detail", "$detail2", "$blendmodulatetexture", "$AmbientOcclTexture", "$CorneaTexture",
    "$envmap", "$phongwarptexture", "$selfillummask", "$selfillumtexture", "$detail1",
    "$iris", "$mraotexture", "$paintsplatnormalmap", "$paintsplatbubblelayout",
    "$paintsplatbubble", "$paintenvmap", "$emissiontexture", "$emissiontexture2",
}
