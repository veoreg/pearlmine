//! Unsafe is confined to the checked C ABI; one small tile per call.
#[repr(C)]
#[derive(Default)]
pub struct Output {
    pub transcript: [u32; 16],
    pub digest: [u8; 32],
    pub active_ns: u64,
    pub device_bytes: u64,
    pub reused: u32,
    pub variant: u32,
}
extern "C" {
    fn boris_cuda_tile(
        device: u32,
        a: *const i8,
        b: *const i8,
        k: u32,
        variant: u32,
        key: *const u8,
        out: *mut Output,
    ) -> i32;
    fn boris_cuda_release();
    fn boris_cuda_identity(device: u32, uuid: *mut u8, size: u32) -> i32;
}
pub fn identity(device: u32) -> anyhow::Result<String> {
    let mut uuid = [0u8; 64];
    // SAFETY: fixed writable buffer and explicit capacity match the checked C ABI.
    let status = unsafe { boris_cuda_identity(device, uuid.as_mut_ptr(), uuid.len() as u32) };
    anyhow::ensure!(status == 0, "CUDA identity query failed: {status}");
    Ok(std::ffi::CStr::from_bytes_until_nul(&uuid)?
        .to_str()?
        .to_owned())
}
pub fn tile(
    device: u32,
    a: &[i8],
    b: &[i8],
    k: u32,
    variant: u32,
    key: &[u8; 32],
) -> anyhow::Result<Output> {
    anyhow::ensure!(
        a.len() == 8 * k as usize && b.len() == 16 * k as usize,
        "invalid strip sizes"
    );
    let mut output = Output::default();
    // SAFETY: sizes above match the C ABI; input references/output remain live until synchronous return.
    let status = unsafe {
        boris_cuda_tile(
            device,
            a.as_ptr(),
            b.as_ptr(),
            k,
            variant,
            key.as_ptr(),
            &mut output,
        )
    };
    anyhow::ensure!(status == 0, "CUDA error {status}");
    Ok(output)
}
pub fn release() {
    // SAFETY: called only by this isolated worker after its synchronous calls finish.
    unsafe { boris_cuda_release() }
}

#[repr(C)]
#[derive(Default)]
pub struct GridTiming {
    pub active_ns: u64,
    pub device_bytes: u64,
}
extern "C" {
    fn boris_grid_allocate(device: u32, m: u32, n: u32, k: u32) -> i32;
    fn boris_grid_chunk(
        cursor: u64,
        count: u32,
        gen_seed: u64,
        a_seed: *const u8,
        b_seed: *const u8,
        prefix: *const u8,
        prepare_a: u32,
        prepare_b: u32,
        result: *mut u32,
        timing: *mut GridTiming,
    ) -> i32;
    fn boris_grid_release();
}
pub fn grid_allocate(device: u32, size: u32) -> anyhow::Result<()> {
    // SAFETY: dimensions/device are checked again inside C; no borrowed pointers.
    let status = unsafe { boris_grid_allocate(device, size, size, 4096) };
    anyhow::ensure!(status == 0, "CUDA grid allocation error {status}");
    Ok(())
}
pub fn grid_chunk(
    cursor: u64,
    count: u32,
    seed: u64,
    a: &[u8; 32],
    b: &[u8; 32],
    prefix: &[i8],
    prepare_a: bool,
    prepare_b: bool,
) -> anyhow::Result<(Vec<u32>, GridTiming)> {
    anyhow::ensure!(
        (16..=4096).contains(&count) && count % 16 == 0 && prefix.len() == 1024,
        "grid C ABI input bounds"
    );
    let mut result = vec![0u32; count as usize * 24];
    let mut timing = GridTiming::default();
    // SAFETY: exact fixed seed/prefix sizes and checked count*24 writable words, synchronous call.
    let status = unsafe {
        boris_grid_chunk(
            cursor,
            count,
            seed,
            a.as_ptr(),
            b.as_ptr(),
            prefix.as_ptr().cast(),
            prepare_a as u32,
            prepare_b as u32,
            result.as_mut_ptr(),
            &mut timing,
        )
    };
    anyhow::ensure!(status == 0, "CUDA grid error {status}");
    Ok((result, timing))
}
pub fn grid_release() {
    /* SAFETY: owned synchronous worker context only. */
    unsafe { boris_grid_release() }
}

#[repr(C)]
pub struct DeviceInfo {
    pub index: u32,
    pub major: u32,
    pub minor: u32,
    pub backend_mask: u32,
    pub min_cc: u32,
    pub total_bytes: u64,
    pub name: [u8; 256],
    pub uuid: [u8; 64],
    pub pci: [u8; 32],
    pub profile: [u8; 16],
}
extern "C" {
    fn boris_cuda_count(count: *mut u32) -> i32;
    fn boris_cuda_device_info(device: u32, output: *mut DeviceInfo) -> i32;
}
impl DeviceInfo {
    fn string(bytes: &[u8]) -> anyhow::Result<String> {
        Ok(std::ffi::CStr::from_bytes_until_nul(bytes)?
            .to_str()?
            .to_owned())
    }
    pub fn supports(&self, variant: u32) -> bool {
        let bit = match variant {
            4 => 1,
            16 => 2,
            32 => 4,
            _ => 0,
        };
        self.major * 10 + self.minor >= self.min_cc
            && self.major * 10 + self.minor >= 75
            && bit != 0
            && self.backend_mask & bit != 0
            && (variant != 32 || self.major >= 8)
    }
    pub fn json(&self) -> anyhow::Result<serde_json::Value> {
        Ok(
            serde_json::json!({"cuda_index":self.index,"name":Self::string(&self.name)?,
            "uuid":Self::string(&self.uuid)?,"pci_bus_id":Self::string(&self.pci)?,
            "cc_major":self.major,"cc_minor":self.minor,"memory_bytes":self.total_bytes,
            "build_profile":Self::string(&self.profile)?,"build_min_cc":self.min_cc,"build_backend_mask":self.backend_mask}),
        )
    }
}
pub fn device_info(index: u32) -> anyhow::Result<DeviceInfo> {
    let mut info = std::mem::MaybeUninit::<DeviceInfo>::uninit();
    // SAFETY: exact repr(C) fixed-size output; accessed only after successful native initialization.
    let status = unsafe { boris_cuda_device_info(index, info.as_mut_ptr()) };
    anyhow::ensure!(status == 0, "CUDA device query failed: {status}");
    Ok(unsafe { info.assume_init() })
}
pub fn devices() -> anyhow::Result<serde_json::Value> {
    let mut count = 0;
    // SAFETY: writable u32; native count is bounded32.
    let status = unsafe { boris_cuda_count(&mut count) };
    anyhow::ensure!(
        status == 0 && count <= 32,
        "CUDA inventory failed: {status}"
    );
    let mut result = Vec::new();
    for index in 0..count {
        result.push(device_info(index)?.json()?);
    }
    Ok(serde_json::json!({"devices":result,"scope":"read-only CUDA enumeration; no GPU kernels"}))
}
