# Source Resource Compiler

A Python-based **pipeline for compiling, managing, and packaging Source engine resources** (models, materials, scripts, and more). Designed for games like **Left 4 Dead 2**, this tool automates compilation, material processing, subdata export, and optional VPK packaging.  

## Features

- **Model Compilation**
  - Compile main QC and sub-QC files using a custom StudioMDL executable.
  - Supports multiple models per project.

- **Material Management**
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

- **Flexible Configuration**
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
