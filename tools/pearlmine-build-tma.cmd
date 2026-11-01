@echo off
rem Builds the fast RTX 5090 worker with an existing CUDA 13 toolkit and records its receipt.
rem Usage: tools\pearlmine-build-tma.cmd [toolkit folder] [MSVC Hostx64\x64 folder]
setlocal
set TOOLKIT=%~1
if "%TOOLKIT%"=="" set TOOLKIT=E:\toolchains\cuda-13.4
set SPM_MSVC_BIN=%~2
if "%SPM_MSVC_BIN%"=="" set SPM_MSVC_BIN=C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Tools\MSVC\14.38.33130\bin\Hostx64\x64
call "%SPM_MSVC_BIN%\..\..\..\..\..\..\Auxiliary\Build\vcvars64.bat" -vcvars_ver=14.38 >nul
set CUDA_PATH=%TOOLKIT:\=/%
set NVCC=%TOOLKIT%\bin\nvcc.exe
set SPM_CUDA_ARCH=sm_120a
set CARGO_TARGET_DIR=%~dp0..\tma_worker\target
cargo build --release --manifest-path "%~dp0..\tma_worker\Cargo.toml" || exit /b 1
copy /y "%TOOLKIT%\bin\x64\cudart64_13.dll" "%CARGO_TARGET_DIR%\release\" >nul || exit /b 1
python -X utf8 "%~dp0pearlmine-tma-receipt.py" "%TOOLKIT%"
