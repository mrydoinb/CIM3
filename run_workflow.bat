@echo off
chcp 65001 >nul
setlocal

if not defined PYTHON_EXE set "PYTHON_EXE=D:\kaifahuanjing\anaconda3\envs\cim-road\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
if not defined BLENDER_EXE set "BLENDER_EXE=D:\ruanjian\Blender 5.1\blender.exe"

if not exist "%BLENDER_EXE%" (
    echo [ERROR] Blender executable not found:
    echo         %BLENDER_EXE%
    echo.
    echo Set BLENDER_EXE before running this script, for example:
    echo         set "BLENDER_EXE=D:\path\to\blender.exe"
    pause
    exit /b 1
)

echo ===================================================
echo        CIM3 road automatic modeling workflow
echo ===================================================

echo [1/2] Generating CIM3 road geometry and topology...
"%PYTHON_EXE%" scripts\01_generate_cim3_road.py
if errorlevel 1 goto :failed

echo [2/2] Running Blender background service: applying materials and exporting FBX...
"%BLENDER_EXE%" --background --python scripts\02_export_fbx_blender.py
if errorlevel 1 goto :failed

echo ===================================================
echo Workflow finished.
echo OBJ: output\obj\road_test.obj
echo FBX: output\fbx\road_test.fbx
pause
exit /b 0

:failed
echo ===================================================
echo Workflow failed. Please check the error log above.
pause
exit /b 1
