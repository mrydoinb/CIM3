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
echo        CIM city automatic modeling workflow
echo ===================================================

echo [1/3] Downloading Beijing Yizhuang OSM source data...
"%PYTHON_EXE%" scripts\00_download_allianz_arena_osm.py
if errorlevel 1 goto :failed

echo [2/3] Generating CIM city OBJ with roads, buildings, subway, bus stops, and utilities...
"%PYTHON_EXE%" scripts\05_generate_cim_city.py
if errorlevel 1 goto :failed

echo [3/3] Exporting materialized CIM city FBX...
"%BLENDER_EXE%" --background --python scripts\06_export_cim_city_fbx_blender.py
if errorlevel 1 goto :failed

echo ===================================================
echo City workflow finished.
echo OBJ: output\obj\cim_city.obj
echo FBX: output\fbx\cim_city.fbx
pause
exit /b 0

:failed
echo ===================================================
echo City workflow failed. Please check the error log above.
pause
exit /b 1
