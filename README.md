# KitsuneResource

A Python-based **pipeline for compiling, and packaging Source engine resources** (models, materials, scripts, etc). Designed for games like **Left 4 Dead 2 or Garry's Mod**, this tool automates compilation, material processing, subdata export, and optional VPK packaging.  

<img width="1493" height="808" alt="image" src="https://github.com/user-attachments/assets/d40186b4-e47b-416c-b43e-e8c6f49a10a8" />

## Compatibility

Windows & Python 3.10+

## Features

- **Model Compiling**
  - Compile main QC and sub-QC files using a specified StudioMDL executable.
  - Supports multiple models per JSON.

- **Materials**
  - Copy or localize dumped materials automatically.
  - Supports shared material folders.
  - Material-only export mode.

- **Subdata & Top-level Data Handling**
  - Copy scripts, configs, and other files into the compile folder.
  - Optional **VTF conversion** with customizable flags.
  - Replace strings inside `.txt`, `.lua`, `.nut` files using `$PLACEHOLDER$` keys in JSON.

- **VPK Packaging**
  - Optionally package each compiled subfolder into a **VPK**.
  - Runs silently unless verbose logging is enabled.

- **Logging**
  - Detailed logs with timestamps and colored prefixes for **MODEL**, **MATERIAL**, **DATA**, and **VPK** operations.
  - Total elapsed time reported at the end of the pipeline.

- **Configurable**
  - JSON-based configuration to define:
    - Models, QC files, submodels.
    - Materials and material sets.
    - Subdata and top-level data files.
    - Optional VTF and VPK executables.

## Installation
Ensure you have Python 3.10+ installed.
1. Clone the repository:

```bash
git clone https://github.com/yourusername/source-resource-compiler.git
cd source-resource-compiler
pip install -r requirements.txt
```

## Sample JSON
```Refer to the sample json in sample_json/```

## Run
```cmd
python resourcecompiler.py -config path/to/<config name>.json [args...]
```

## Command-Line Arguments

| Argument             | Description                                                                 |
|----------------------|-----------------------------------------------------------------------------|
| `-config` / `--config` | **Required** — Path to the JSON config file containing models, materials, and pipeline data. |
| `--dir`              | Optional — Absolute path to override the input/output root directory for compiling. |
| `--log`              | Enable logging to a timestamped file under `resourcecompiler-log` relative to the config file or `--dir`. |
| `--verbose`          | Enable verbose logging for detailed output in the console and log file.    |

### ValveModel Pipeline Options
| Argument             | Description                                                                 |
|----------------------|-----------------------------------------------------------------------------|
| `--exportdir`        | Root folder for compiled output (default: `compile`).                       |
| `--nomaterial`       | Skip material mapping and copying for models.                               |
| `--nolocalize`       | Keep original folder structure for materials instead of localizing.         |
| `--sharedmaterials`  | Copy model materials into a shared folder (`compile/Assetshared`) instead of per-model folders. |
| `--vpk`              | Package each compiled subfolder into a VPK.                                 |
| `--archive`          | Archive existing compiled files instead of deletion.                        |
| `--game (DISABLED)`             | Compile models directly in the game's directory and skip material collection and VPK packaging. |

### ValveTexture Pipeline Options
| Argument             | Description                                                                 |
|----------------------|-----------------------------------------------------------------------------|
| `--forceupdate`      | Force reprocessing of all textures, even if output VTFs are up-to-date.    |
| `--allow_reprocess`  | Allow the same source file to be processed multiple times if matched by multiple JSON entries. |

