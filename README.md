# KitsuneResource

KitsuneResource is a Python-based pipeline for compiling and packaging Source Engine resources, including models, materials, textures, and scripts. It is designed to automate compilation, material processing, texture conversion, and packaging for titles such as Left 4 Dead 2 and Garry's Mod.


## Compatibility

- Operating System: Windows 10+ or Linux (via Wine 9.0 for Windows executables)
- Python: Version 3.10 or newer

## Dependencies

The following tools must be available in your system path or specified in your configuration file:
- `studiomdl.exe` (Provided with the Source SDK or the `bin/` directory of a Source game)
- `vtfcmd.exe` (Provided by [VTFLib](https://github.com/NeilJed/VTFLib))
- `vpk.exe` or `gmad.exe` (Provided in the `bin/` directory of a Source game)

## Installation

Clone the repository and install the required dependencies:

```bash
git clone [https://github.com/yourusername/KitsuneResource.git](https://github.com/yourusername/KitsuneResource.git)
cd KitsuneResource
pip install -r requirements.txt
python build.py
```

## Usage

KitsuneResource accepts one or more configuration JSON files or direct `.qc` files via the command line.

```cmd
python main.py [options] <config.json>|<model.qc> ...
kitsuneresource.exe [options] <config.json>|<model.qc> ...
```

## Command-Line Arguments

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

## Acknowledgments

- Valve Software for the Source Engine and SDK tools
- NeilJed and the VTFLib developers for texture conversion tools