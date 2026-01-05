# KitsuneResource

A Python-based pipeline for compiling and packaging Source Engine resources (models, materials, textures, scripts). Designed for games like **Left 4 Dead 2** and **Garry's Mod**, this tool automates compilation, material processing, texture conversion, and optional VPK packaging.

<img width="1502" height="943" alt="Screenshot 2026-01-06 023140" src="https://github.com/user-attachments/assets/8f1f81c2-5143-478e-bf5d-4af4ab26d453" />

## Compatibility

Windows 10+ or Linux with Wine 9.0 for the executables & Python 3.10+

## Installation

1. **Prerequisites:**
   - Python 3.10+
   - `studiomdl.exe` (from Source SDK or any Source game's `bin/` folder)
   - `vtfcmd.exe` ([VTFLib](https://github.com/NeilJed/VTFLib))
   - `vpk.exe` (from any Source game's `bin/` folder)

2. **Clone and Install:**
```bash
git clone https://github.com/yourusername/KitsuneResource.git
cd KitsuneResource
pip install -r requirements.txt
```

## Usage

### Basic Command
```cmd
python resourcecompiler.py --config path/to/config.json [options]
```

### Sample Configurations
```
Refer to sample_json/ directory for example configurations
```

## Command-Line Arguments

### Global Options
| Argument | Description |
|----------|-------------|
| `--config` | **(Required)** Path to JSON configuration file |
| `--dir` | Override input/output root directory |
| `--log` | Enable logging to timestamped file in `resourcecompiler-log/` |
| `--verbose` | Enable verbose output for debugging |

### ValveModel Pipeline Options
| Argument | Description |
|----------|-------------|
| `--exportdir` | Root folder for compiled output (default: `ExportedResource`) |
| `--nomaterial` | Skip material mapping and copying |
| `--nolocalize` | Keep original folder structure for materials |
| `--sharedmaterials` | Copy materials to `compile/Assetshared` folder |
| `--vpk` | Package each compiled subfolder into VPK |
| `--archive` | Archive existing compile folder instead of deletion |
| `--game` | Compile directly to game directory (skips materials/data/VPK) |
| `--flatten-qc` | Flatten QC files before compilation (0: no temp files, 1: keep temp files) |

### ValveTexture Pipeline Options
| Argument | Description |
|----------|-------------|
| `--forceupdate` | Force reprocessing all textures |
| `--allow_reprocess` | Allow same file to be processed multiple times |
| `--recursive` | Search for files recursively in subfolders |

## Configuration Examples

### ValveModel Pipeline
```json
{
  "header": "ValveModel",
  "studiomdl": "C:/Steam/steamapps/common/Team Fortress 2/bin/studiomdl.exe",
  "gameinfo": "C:/Steam/steamapps/common/Team Fortress 2/tf/gameinfo.txt",
  "vtfcmd": "C:/Tools/VTFCmd.exe",
  "vpk": "C:/Steam/steamapps/common/Team Fortress 2/bin/vpk.exe",
  
  "model": {
    "MyModel": {
      "qc": "models/mymodel/mymodel.qc",
      "compile": true,
      "submodels": {
        "phymodel": "models/mymodel/phymodel.qc"
      },
      "subdata": [
        {
          "input": "textures/custom.tga",
          "output": "materials/models/mymodel/custom.vtf",
          "vtf": {
            "flags": ["NOMIP"],
            "vmt": "templates/basic.vmt"
          }
        }
      ]
    }
  },
  
  "material": {
    "SharedTextures": {
      "materials": [
        "models/shared/metal01",
        "models/shared/concrete"
      ]
    }
  },
  
  "data": {
    "scripts": [
      {
        "input": "scripts/game_sounds.txt",
        "output": "scripts/game_sounds_custom.txt",
        "replace": {
          "OLD_PATH": "NEW_PATH"
        }
      }
    ]
  }
}
```

### ValveTexture Pipeline
```json
{
  "header": "ValveTexture",
  "vtfcmd": "C:/Tools/VTFCmd.exe",
  
  "vtf": {
    "skybox_textures": {
      "input": "skybox_.*\\.tga",
      "output": "materials/skybox/",
      "vtf": {
        "flags": ["NOMIP", "NOLOD"],
        "encoder_args": ["-format", "DXT1"]
      }
    }
  }
}
```

### Configuration Includes
Split configurations across multiple files:
```json
{
  "header": "ValveModel",
  "include": ["common_settings.json", "tool_paths.json"],
  "model": { ... }
}
```

## How It Works

### Model Compilation Flow
1. Parse JSON configuration and validate tool paths
2. Extract game search paths from `gameinfo.txt`
3. Compile QC files using `studiomdl.exe`
4. Parse QC for materials (`$cdmaterials`, `$texturegroup`, `$renamematerial`)
5. Locate VMT/VTF files in game search paths
6. Copy materials and textures to output folder
7. Optionally localize VMT paths for self-contained packages
8. Process additional data files
9. Package into VPK archives if requested

### Material Localization
When enabled, VMT files are rewritten to use relative paths:
```vmt
Before: $basetexture "models/weapons/shared/texture"
After:  $basetexture "shared/texture"
```

### QC Material
- Materials dumped by `studiomdl.exe` during compilation
- `$cdmaterials` search paths
- `$texturegroup skinfamilies` alternate skins
- `$renamematerial` mappings
- `$include` files (recursive)

## Acknowledgments

- Valve Software for Source Engine and SDK tools
- VTFLib developers for texture conversion tools
- Source modding community
