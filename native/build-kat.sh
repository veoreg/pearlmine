#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p out
python3 ../tools/boris-golden-header.py
nvcc -O2 -std=c++17 -gencode arch=compute_75,code=sm_75 -gencode arch=compute_89,code=sm_89 -gencode arch=compute_75,code=compute_75 kat.cu ../cuda/prep/prep.cu -o out/boris-offline-kat
echo 'Built offline test only. Run ./out/boris-offline-kat explicitly.'
