param([string]$CompilerBin = 'C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Tools\MSVC\14.38.33130\bin\Hostx64\x64')
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot '..\tools\boris-golden-header.py')
if ($LASTEXITCODE -ne 0) { throw "Pinned golden fixture validation failed" }
$destination = Join-Path $PSScriptRoot 'out'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$binary = Join-Path $destination 'boris-offline-kat.exe'
& nvcc -O2 -std=c++17 -ccbin $CompilerBin '-gencode=arch=compute_75,code=sm_75' '-gencode=arch=compute_89,code=sm_89' '-gencode=arch=compute_75,code=compute_75' (Join-Path $PSScriptRoot 'kat.cu') (Join-Path $PSScriptRoot '..\cuda\prep\prep.cu') -o $binary
if ($LASTEXITCODE -ne 0) { throw "CUDA KAT build failed: $LASTEXITCODE" }
Write-Output "Built offline test only: $binary"
