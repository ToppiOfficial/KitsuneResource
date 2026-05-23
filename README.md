## INTRODUCTIONS

KitsuneResource is a Python-based software for compiling and packaging Source Engine assets, specifically models, materials, textures, and scripts. It is designed to automate compilation, material processing, texture conversion, and packaging addons for titles such as Left 4 Dead 2 and Garry's Mod.


## COMPATIBILITY

| Platform | Architecture | Support |
| --- | --- | --- |
| Windows 10+ | x86-64, ARM64 | Native |
| Linux | x86-64 | Native (Wine required for `.exe` tools) |
| Linux | ARM64 | Native (Wine + Box64 required for `.exe` tools) |
| macOS 12+ | x86-64, Apple Silicon | Native (Wine required, e.g. Whisky / CrossOver) |

- Python: Version 3.10 or newer (not needed when using the pre-built binary)

## DEPENDENCIES

The following tools must be available in your system path or specified in your configuration file:
- `studiomdl.exe` (Provided with the Source SDK or the `bin/` directory of a Source game)
- `vtfcmd.exe` (Provided by [VTFLib](https://github.com/NeilJed/VTFLib))
- `vpk.exe` or `gmad.exe` (Provided in the `bin/` directory of a Source game)

On Linux and macOS these are Windows binaries. They must be invoked via Wine using the `wine_cmd` config key (see **Running on Linux / macOS** below). If a native Linux build of any tool exists, it can be pointed to directly without `wine_cmd`.

## BUILD

```bash
git clone https://github.com/ToppiOfficial/KitsuneResource.git
cd KitsuneResource
pip install -r requirements.txt
python build.py
```

Produces `dist/kitsuneresource.exe` on Windows or `dist/kitsuneresource` on Linux/macOS.

## USAGE

```
python main.py [options] <config.json>|<model.qc> ...
kitsuneresource[.exe] [options] <config.json>|<model.qc> ...
```

## RUNNING ON LINUX / MACOS

### Option A - Python source (no build needed)

```bash
git clone https://github.com/ToppiOfficial/KitsuneResource.git
cd KitsuneResource
pip install -r requirements.txt
python main.py [options] <config.json>
```

`main.py` auto-activates a local `venv/` if one exists and works identically to the Windows version. All Windows-only dependencies (`pefile`, `pywin32-ctypes`) are excluded automatically via `requirements.txt` markers.

### Option B - Pre-built binary

Download the `kitsuneresource` artifact (no `.exe`) for your architecture from the [releases page](https://github.com/ToppiOfficial/KitsuneResource/releases). No Python installation needed.

### Wine setup for Source Engine tools

The KitsuneResource app itself runs natively. The Source Engine tools (`studiomdl.exe`, `vtfcmd.exe`, `vpk.exe`, `gmad.exe`) are Windows binaries and require a compatibility layer.

**Linux x86-64**

1. Install Wine:
   ```bash
   sudo apt install wine   # Debian / Ubuntu
   ```
2. Add `wine_cmd` to your config JSON:
   ```json
   {
     "wine_cmd": "wine",
     "studiomdl": "/path/to/studiomdl.exe",
     "vtfcmd":    "/path/to/vtfcmd.exe"
   }
   ```

**Linux ARM64**

x86-64 Windows binaries require both Wine and [Box64](https://github.com/ptitSeb/box64) for instruction translation:

```bash
# install Box64 per its README, then:
sudo apt install wine
```

```json
{ "wine_cmd": "box64 wine" }
```

**macOS (Apple Silicon and Intel)**

Use [Whisky](https://github.com/Whisky-App/Whisky) or [CrossOver](https://www.codeweavers.com/crossover) and point `wine_cmd` at their launch helper, for example:

```json
{ "wine_cmd": "/Applications/Whisky.app/Contents/MacOS/rosetta-wine" }
```

When `wine_cmd` is active a red banner is displayed below the header on startup confirming the command in use.

## COMMAND-LINE ARGUMENTS

### Global Options

| Argument | Description |
| --- | --- |
| `INPUT_FILE(S)` | **(Required)** One or more paths to `.json` configuration files or `.qc` files. |
| `--only <entry>` | Only compile the specified model or data entry (case-insensitive). Can be specified multiple times. |
| `--log` | Enable logging. Output is written to a timestamped file in the `.resource-log/` directory. |
| `--verbose` | Enable verbose terminal output. |

### ValveModel Pipeline Options

| Argument | Description |
| --- | --- |
| `--exportdir <dir>` | Root directory for compiled output. Defaults to the base name of the configuration file. |
| `--game [path]` | Compile models directly into the game directory, skipping material/data processing and packaging. An optional path containing `gameinfo.txt` can be provided to override the configuration. |
| `--no-vproject` | Prevent passing the gameinfo directory to `studiomdl`. |
| `--mat-mode <0 1 2>` | Set material processing behavior. `0`: Skip processing. `1`: Copy materials locally to the model's folder (`raw-local`). `2`: Copy materials to a shared directory (default). |
| `--no-mat-local` | Disable the localization of material paths in VMT files when using `--mat-mode 2`. |
| `--package-files` | Package each compiled subfolder into a separate VPK or GMA archive. |
| `--archive-old-ver` | Archive the existing compile folder with a timestamp before starting instead of overwriting. |
| `--single-addon` | Compile all output into a single addon directory defined by the `addonroot` parameter in the configuration. |

### ValveTexture Pipeline Options

| Argument | Description |
| --- | --- |
| `--forceupdate` | Force reprocessing of all textures, ignoring the signature cache. |
| `--allow_reprocess` | Allow a single source file to be processed multiple times during the same execution. |
| `--recursive` | Traverse subdirectories recursively when searching for input texture files. |

## ACKNOWLEDGMENTS

- Valve Software for the Source Engine and SDK tools
- NeilJed and the VTFLib developers for texture conversion tools
