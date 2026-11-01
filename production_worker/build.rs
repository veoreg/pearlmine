// New portable scalar CUDA backend. No driver/toolchain installation.
use std::{env, path::PathBuf, process::Command};
mod build_plan;
fn main() {
    let root = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("..");
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let windows = env::var("CARGO_CFG_TARGET_OS").unwrap() == "windows";
    let lib = out.join(if windows {
        "boris_cuda.lib"
    } else {
        "libboris_cuda.a"
    });
    let mut cmd = Command::new(env::var("NVCC").unwrap_or("nvcc".into()));
    let profile = env::var("BORIS_CUDA_PROFILE").unwrap_or("portable".into());
    let plan = build_plan::plan(
        &profile,
        &env::var("HOST").unwrap(),
        &env::var("TARGET").unwrap(),
    )
    .unwrap_or_else(|reason| panic!("{reason}"));
    if plan.gb10 {
        let check = Command::new(env::var("NVCC").unwrap_or("nvcc".into()))
            .arg("--list-gpu-arch")
            .output()
            .expect("Existing native CUDA toolkit required; no installer is run");
        assert!(check.status.success() && String::from_utf8_lossy(&check.stdout).lines().any(|line| line.trim() == "compute_121"),
            "gb10 requires a native CUDA toolkit listing compute_121 (expected CUDA13+); no lower-architecture fallback");
    }
    cmd.args(["--lib", "-O2", "-std=c++17"])
        .arg(format!("-DBORIS_CUDA_PROFILE={}", plan.id));
    for target in plan.targets {
        cmd.arg(format!("-gencode=arch={target}"));
    }
    if windows {
        let compiler=env::var("BORIS_CUDA_COMPILER_BIN").unwrap_or(
            "C:\\Program Files\\Microsoft Visual Studio\\2022\\Professional\\VC\\Tools\\MSVC\\14.38.33130\\bin\\Hostx64\\x64".into());
        cmd.arg("-ccbin").arg(compiler);
    } else {
        cmd.args(["-Xcompiler", "-fPIC"]);
    }
    let result = cmd
        .arg(root.join("native/worker_kernel.cu"))
        .arg("-o")
        .arg(&lib)
        .output()
        .expect("CUDA toolkit/nvcc is required");
    assert!(
        result.status.success(),
        "CUDA build failed: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=static=boris_cuda");
    let cuda = env::var("CUDA_PATH")
        .or_else(|_| env::var("CUDA_HOME"))
        .unwrap_or(if windows {
            "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.4".into()
        } else {
            "/usr/local/cuda".into()
        });
    let cuda_root = PathBuf::from(cuda);
    let lib_dir = if windows {
        cuda_root.join("lib/x64")
    } else {
        build_plan::linux_cuda_libs(&env::var("CARGO_CFG_TARGET_ARCH").unwrap())
            .iter()
            .map(|rel| cuda_root.join(rel))
            .find(|p| p.is_dir())
            .expect(
                "CUDA library directory missing; point CUDA_HOME at the existing native toolkit",
            )
    };
    println!("cargo:rustc-link-search=native={}", lib_dir.display());
    println!("cargo:rustc-link-lib=dylib=cudart");
    if !windows {
        println!("cargo:rustc-link-lib=dylib=stdc++");
    }
    println!(
        "cargo:rerun-if-changed={}",
        root.join("native/worker_kernel.cu").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        root.join("native/tensor_tile.cuh").display()
    );
    for file in [
        "native/local_grid.cuh",
        "cuda/prep/prep.cu",
        "cuda/prep/prep.cuh",
    ] {
        println!("cargo:rerun-if-changed={}", root.join(file).display());
    }
    println!("cargo:rerun-if-env-changed=BORIS_CUDA_COMPILER_BIN");
    println!("cargo:rerun-if-env-changed=BORIS_CUDA_PROFILE");
    println!("cargo:rerun-if-env-changed=NVCC");
    println!("cargo:rerun-if-env-changed=CUDA_HOME");
    println!("cargo:rerun-if-env-changed=CUDA_PATH");
    println!("cargo:rerun-if-changed=build_plan.rs");
}
