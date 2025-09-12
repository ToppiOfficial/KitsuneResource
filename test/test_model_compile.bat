@echo off
REM === Test Compile Batch ===

REM Path to your main pipeline script
set PIPELINE_SCRIPT=D:\Local-runtimes\Source_ResourceCompiler\resourcecompiler.py

REM Path to test JSON config
set CONFIG_JSON=D:\Local-runtimes\Source_ResourceCompiler\test\test_json.json

echo [INFO] Running test compile using %CONFIG_JSON%
C:\Python\312\python.exe %PIPELINE_SCRIPT% -config "%CONFIG_JSON%" --sharedmaterials --vpk

pause
