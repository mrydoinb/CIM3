@echo off
chcp 65001 >nul
setlocal

if not defined PYTHON_EXE set "PYTHON_EXE=D:\ProgramData\miniconda3\envs\cim-road\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
if not defined BLENDER_EXE set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
if not exist "%BLENDER_EXE%" set "BLENDER_EXE=D:\ruanjian\Blender 5.1\blender.exe"
if not exist "%BLENDER_EXE%" (
    for /f "delims=" %%B in ('where blender 2^>nul') do (
        set "BLENDER_EXE=%%B"
        goto :found_blender
    )
)
:found_blender

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
echo        CIM road iteration workflow
echo ===================================================

echo [1/2] Generating road OBJ and separate junction debug models...
"%PYTHON_EXE%" scripts\02_generate_cim_roads.py
if errorlevel 1 goto :failed

echo [2/2] Exporting road and junction debug FBX...
"%PYTHON_EXE%" scripts\03_export_cim_roads_fbx.py --blender "%BLENDER_EXE%"
if errorlevel 1 goto :failed

echo ===================================================
echo Road workflow finished.
echo OBJ: output\obj\modules\cim_city_roads.obj
echo FBX: output\fbx\modules\cim_city_roads.fbx
echo Junction debug FBX directory: output\fbx\junctions
pause
exit /b 0

:failed
echo ===================================================
echo Road workflow failed. Please check the error log above.
pause
exit /b 1
