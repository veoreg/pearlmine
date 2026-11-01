// SPDX-License-Identifier: Apache-2.0
// Pure native-build policy; tests require only the already installed Rust compiler.
#[derive(Debug, PartialEq)]
pub struct Plan {
    pub id: u32,
    pub targets: &'static [&'static str],
    pub gb10: bool,
}
pub fn plan(profile: &str, host: &str, target: &str) -> Result<Plan, &'static str> {
    if host != target {
        return Err("Cross compilation is unconfigured: use the matching native Linux/Windows host and CUDA toolkit");
    }
    match profile {
        "portable" => Ok(Plan {
            id: 0,
            targets: &[
                "compute_75,code=sm_75",
                "compute_89,code=sm_89",
                "compute_75,code=compute_75",
                "compute_80,code=compute_80",
                "compute_89,code=compute_89",
            ],
            gb10: false,
        }),
        "turing75" => Ok(Plan {
            id: 1,
            targets: &["compute_75,code=sm_75", "compute_75,code=compute_75"],
            gb10: false,
        }),
        "ada89" => Ok(Plan {
            id: 2,
            targets: &["compute_89,code=sm_89", "compute_89,code=compute_89"],
            gb10: false,
        }),
        "gb10" if target == "aarch64-unknown-linux-gnu" => Ok(Plan {
            id: 3,
            targets: &["compute_121,code=sm_121", "compute_121,code=compute_121"],
            gb10: true,
        }),
        "gb10" => Err(
            "gb10 requires a native aarch64-unknown-linux-gnu host; no WSL/cross-compiler fallback",
        ),
        _ => Err("BORIS_CUDA_PROFILE must be portable, turing75, ada89 or gb10"),
    }
}
pub fn linux_cuda_libs(arch: &str) -> Vec<String> {
    let triple = if arch == "aarch64" {
        "aarch64-linux"
    } else {
        "x86_64-linux"
    };
    vec![format!("targets/{triple}/lib"), "lib64".into()]
}
#[cfg(test)]
mod tests {
    use super::*;
    const WIN: &str = "x86_64-pc-windows-msvc";
    const ARM: &str = "aarch64-unknown-linux-gnu";
    #[test]
    fn existing_profiles_unchanged() {
        for (name, id, count) in [("portable", 0, 5), ("turing75", 1, 2), ("ada89", 2, 2)] {
            let p = plan(name, WIN, WIN).unwrap();
            assert_eq!(p.id, id);
            assert_eq!(p.targets.len(), count);
            assert!(!p.gb10);
        }
    }
    #[test]
    fn gb10_native_only_has_generic_121_sass_and_ptx() {
        let p = plan("gb10", ARM, ARM).unwrap();
        assert_eq!(p.id, 3);
        assert!(p.gb10);
        assert_eq!(
            p.targets,
            &["compute_121,code=sm_121", "compute_121,code=compute_121"]
        );
        assert!(!p.targets.iter().any(|x| x.contains("121a")));
    }
    #[test]
    fn gb10_rejects_windows() {
        assert!(plan("gb10", WIN, WIN).is_err());
    }
    #[test]
    fn gb10_rejects_x64_linux() {
        assert!(plan(
            "gb10",
            "x86_64-unknown-linux-gnu",
            "x86_64-unknown-linux-gnu"
        )
        .is_err());
    }
    #[test]
    fn cross_compiler_is_not_silently_selected() {
        assert!(plan("gb10", WIN, ARM).is_err());
    }
    #[test]
    fn unknown_profile_rejected() {
        assert!(plan("gb1O", ARM, ARM).is_err());
    }
    #[test]
    fn arm64_library_path_is_not_x64() {
        assert_eq!(
            linux_cuda_libs("aarch64"),
            vec!["targets/aarch64-linux/lib", "lib64"]
        );
        assert_eq!(
            linux_cuda_libs("x86_64"),
            vec!["targets/x86_64-linux/lib", "lib64"]
        );
    }
}
