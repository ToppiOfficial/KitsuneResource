# KitsuneResource

A Python-based pipeline for compiling and packaging Source Engine resources (models, materials, textures, scripts). Designed for games like **Left 4 Dead 2** and **Garry's Mod**, this tool automates compilation, material processing, texture conversion, and optional packaging.

<img width="1502" height="943" alt="Screenshot 2026-01-06 023140" src="https://github.com/user-attachments/assets/8f1f81c2-5143-478e-bf5d-4af4ab26d453" />

## Compatibility

Windows 10+ or Linux with Wine 9.0 for the executables & Python 3.10+

## Installation

1. **Prerequisites:**
   - Python 3.10+
   - `studiomdl.exe` (from Source SDK or any Source game's `bin/` folder)
   - `vtfcmd.exe` ([VTFLib](https://github.com/NeilJed/VTFLib))
   - `vpk.exe or gmad.exe` (from any Source game's `bin/` folder)

2. **Clone and Install:**
```bash
git clone https://github.com/yourusername/KitsuneResource.git
cd KitsuneResource
pip install -r requirements.txt
python build.py
```

## Usage

### Basic Command
```cmd
python main.py [options] path/to/config.json or kitsuneresource.exe [options] path/to/config.json
```

## Command-Line Arguments

### Global Options
| Argument | Description |
|----------|-------------|
| `CONFIG_JSON` | **(Required)** Path to JSON configuration file |
| `--basedir <path>` | Absolute path to override the input/output root directory. |
| `--log` | Enable logging to a timestamped file in the `kitsune_log/` directory. |
| `--verbose` | Enable verbose output for debugging purposes. |

### ValveModel Pipeline Options
| Argument | Description |
|----------|-------------|
| `--exportdir <dir>` | Root folder for the compiled output. Defaults to `ExportedResource`. |
| `--game [path]` | Compile models directly into the game directory, skipping material/data processing and VPK packaging. Can optionally take a path to a directory containing `gameinfo.txt` to override the config. |
| `--mat-mode <0,1,2>` | **0**: Skip all material processing. **1**: Copy materials locally to the model's folder (`raw-local`). **2**: Copy materials to a shared folder (default). |
| `--no-mat-local` | When using `--mat-mode 2`, this disables the localization of material paths in VMT files. |
| `--package-files` | Package each compiled subfolder into a separate VPK archive. (Formerly `--vpk`) |
| `--archive-old-ver` | Archive the existing compile folder with a timestamp before starting, instead of sending it to the Recycle Bin. (Formerly `--archive`) |
| `--qc-mode <1,2>` | **1**: Use the original QC file directly. **2**: Generate a flattened QC file that includes all sub-models and variables (default). |
| `--keep-flat-qc` | Prevents the deletion of the temporary flattened QC files after compilation. |
| `--single-addon` | Compile all output into a single addon folder defined by 'addonroot' in config. |

### ValveTexture Pipeline Options
| Argument | Description |
|----------|-------------|
| `--forceupdate` | Force reprocessing of all textures, even if they appear to be up-to-date. |
| `--allow_reprocess` | Allow the same source file to be processed multiple times in a single run. |
| `--recursive` | Search for input texture files recursively through all subfolders. |

## Known Issues

- $definevariable and similar gets passed down to submodels incorrectly causing "defined" yet empty value for flatten qc mode (Workaround: Use definevariable in JSON for values getting passed along to every QC or used unique naming for submodels)

## Acknowledgments

- Valve Software for Source Engine and SDK tools
- VTFLib developers for texture conversion tools
- Source modding community
