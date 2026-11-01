// Boris Pearl CUDA baseline, Apache-2.0. No sockets, wallet, clock or power changes.
// One bounded 8x16 strip tile per call. Existing Pearl/Decred ISC hash notices retained.
#include <cuda_runtime.h>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include "../cuda/common/blake3.cuh"

struct TileOutput { uint32_t transcript[16]; uint8_t digest[32]; uint64_t active_ns; uint64_t device_bytes; uint32_t reused; uint32_t variant; };
static thread_local bool context_used=false;
#ifndef BORIS_CUDA_PROFILE
#define BORIS_CUDA_PROFILE 0
#endif
#if BORIS_CUDA_PROFILE == 1
static constexpr uint32_t build_min_cc=75,build_mask=3;
static constexpr char build_profile[]="turing75";
#elif BORIS_CUDA_PROFILE == 2
static constexpr uint32_t build_min_cc=89,build_mask=7;
static constexpr char build_profile[]="ada89";
#elif BORIS_CUDA_PROFILE == 3
static constexpr uint32_t build_min_cc=121,build_mask=7;
static constexpr char build_profile[]="gb10";
#elif BORIS_CUDA_PROFILE == 0
static constexpr uint32_t build_min_cc=75,build_mask=7;
static constexpr char build_profile[]="portable";
#else
#error Unknown BORIS_CUDA_PROFILE
#endif
struct BorisDeviceInfo {
  uint32_t index,major,minor,backend_mask,min_cc;
  uint64_t total_bytes;
  char name[256],uuid[64],pci[32],profile[16];
};
extern "C" int boris_cuda_count(uint32_t* count) {
  if(!count)return -1;int value=0;cudaError_t e=cudaGetDeviceCount(&value);
  if(e==cudaErrorNoDevice){*count=0;return 0;}if(e!=cudaSuccess)return int(e);
  if(value<0 || value>32)return -1;*count=uint32_t(value);return 0;
}
extern "C" int boris_cuda_device_info(uint32_t device,BorisDeviceInfo* output) {
  if(!output || device>31)return -1;cudaDeviceProp p;
  auto e=cudaGetDeviceProperties(&p,int(device));if(e!=cudaSuccess)return int(e);
  std::memset(output,0,sizeof(*output));
  output->index=device;output->major=p.major;output->minor=p.minor;
  output->backend_mask=build_mask;output->min_cc=build_min_cc;output->total_bytes=p.totalGlobalMem;
  std::snprintf(output->name,sizeof(output->name),"%s",p.name);
  std::snprintf(output->profile,sizeof(output->profile),"%s",build_profile);
  const auto* b=reinterpret_cast<const unsigned char*>(p.uuid.bytes);
  std::snprintf(output->uuid,sizeof(output->uuid),"GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
    b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9],b[10],b[11],b[12],b[13],b[14],b[15]);
  e=cudaDeviceGetPCIBusId(output->pci,sizeof(output->pci),int(device));
  return e==cudaSuccess?0:int(e);
}
static int variant_eligible(uint32_t device,uint32_t variant) {
  BorisDeviceInfo d;int status=boris_cuda_device_info(device,&d);if(status)return status;
  uint32_t bit=variant==4?1:variant==16?2:variant==32?4:0;
  if(!bit || !(d.backend_mask&bit))return -4;
  if(d.major*10+d.minor<d.min_cc || d.major*10+d.minor<75)return -2;
  if(variant==32 && d.major<8)return -3;
  return 0;
}


__global__ void evaluate(const int8_t* a, const int8_t* b, uint32_t k,
                         const uint32_t* key, uint32_t* out) {
  __shared__ uint32_t cells[128];
  const uint32_t cell = threadIdx.x;
  uint32_t value = 0;
  if (cell == 0) for (int i=0; i<16; ++i) out[i]=0;
  __syncthreads();
  for (uint32_t slice=0; slice<k/128; ++slice) {
    for (uint32_t l=slice*128; l<(slice+1)*128; l+=4) {
      int av,bv;
      memcpy(&av,a+(cell/16)*k+l,4); memcpy(&bv,b+(cell%16)*k+l,4);
      value=uint32_t(__dp4a(av,bv,int(value)));
    }
    cells[cell]=value;
    __syncthreads();
    if (cell == 0) {
      uint32_t fold=0;
      for (int i=0; i<128; ++i) fold ^= cells[i];
      uint32_t word=out[slice%16];
      out[slice%16]=((word<<13)|(word>>19))^fold;
    }
    __syncthreads();
  }
  if (cell==0) spm::b3::keyed_hash_one_block(key,out,out+16);
}

#include "tensor_tile.cuh"

struct Workspace {
  uint32_t device=0,k=0,variant=4;
  int8_t *da=nullptr,*db=nullptr;
  uint32_t *state=nullptr;
  cudaEvent_t begin=nullptr,end=nullptr;
  cudaStream_t stream=nullptr;
  cudaGraph_t graph=nullptr;
  cudaGraphExec_t execution=nullptr;
};
static thread_local Workspace workspace;

static void clear_workspace() {
  auto& w=workspace;
  if(w.k) cudaSetDevice(w.device);
  if(w.execution) cudaGraphExecDestroy(w.execution); if(w.graph) cudaGraphDestroy(w.graph);
  if(w.stream) cudaStreamDestroy(w.stream);
  if(w.begin) cudaEventDestroy(w.begin); if(w.end) cudaEventDestroy(w.end);
  if(w.da) cudaFree(w.da); if(w.db) cudaFree(w.db); if(w.state) cudaFree(w.state);
  w=Workspace{};
}

static int prepare_workspace(uint32_t device,uint32_t k,uint32_t variant) {
  clear_workspace();
  auto& w=workspace;
  w.device=device; w.k=k; w.variant=variant;
  cudaFuncAttributes attributes;
  int status=0;
  cudaDeviceProp prop;
#define STEP(expr) do { cudaError_t e=(expr); if(e!=cudaSuccess) {status=int(e); goto cleanup;} } while(0)
  STEP(cudaSetDevice(int(device)));
  context_used=true;
  status=variant_eligible(device,variant);if(status)goto cleanup;
  // Force lazy/JIT loading without executing any unaccounted warm-up work.
  // NVIDIA CUDA Programming Guide, Lazy Loading / Forcing a Module to Load.
  if(variant==32) {STEP(cudaFuncGetAttributes(&attributes,evaluate_mma));}
  else if(variant==16) {STEP(cudaFuncGetAttributes(&attributes,evaluate_wmma));}
  else {STEP(cudaFuncGetAttributes(&attributes,evaluate));}
  STEP(cudaMalloc(&w.da,8ull*k)); STEP(cudaMalloc(&w.db,16ull*k));
  STEP(cudaMalloc(&w.state,32*sizeof(uint32_t)));
  STEP(cudaEventCreate(&w.begin)); STEP(cudaEventCreate(&w.end));
  STEP(cudaStreamCreateWithFlags(&w.stream,cudaStreamNonBlocking));
  STEP(cudaStreamBeginCapture(w.stream,cudaStreamCaptureModeGlobal));
  STEP(cudaEventRecordWithFlags(w.begin,w.stream,cudaEventRecordExternal));
  if(variant==32) evaluate_mma<<<1,32,0,w.stream>>>(w.da,w.db,k,w.state,w.state+8);
  else if(variant==16) evaluate_wmma<<<1,32,0,w.stream>>>(w.da,w.db,k,w.state,w.state+8);
  else evaluate<<<1,128,0,w.stream>>>(w.da,w.db,k,w.state,w.state+8);
  STEP(cudaGetLastError()); STEP(cudaEventRecordWithFlags(w.end,w.stream,cudaEventRecordExternal));
  STEP(cudaStreamEndCapture(w.stream,&w.graph));
  STEP(cudaGraphInstantiate(&w.execution,w.graph,nullptr,nullptr,0));
  return 0;
cleanup:
  clear_workspace(); return status;
}

extern "C" int boris_cuda_identity(uint32_t device,char* uuid,uint32_t size) {
  if(!uuid || size<41)return -1;BorisDeviceInfo info;
  int status=boris_cuda_device_info(device,&info);if(status)return status;
  std::snprintf(uuid,size,"%s",info.uuid);return 0;
}

extern "C" int boris_cuda_tile(uint32_t device, const int8_t* a, const int8_t* b,
                               uint32_t k, uint32_t variant, const uint8_t* key, TileOutput* output) {
  if (!a || !b || !key || !output || k<2048 || k>4096 || k%128 || device>31 || (variant!=4 && variant!=16 && variant!=32)) return -1;
  auto& w=workspace;
  bool reused=w.execution && w.device==device && w.k==k && w.variant==variant;
  if(!reused) {int e=prepare_workspace(device,k,variant); if(e) return e;}
  int status=0;
  STEP(cudaMemcpyAsync(w.da,a,8ull*k,cudaMemcpyHostToDevice,w.stream));
  STEP(cudaMemcpyAsync(w.db,b,16ull*k,cudaMemcpyHostToDevice,w.stream));
  STEP(cudaMemcpyAsync(w.state,key,32,cudaMemcpyHostToDevice,w.stream));
  // One graph queues both timing boundaries and the kernel without host/JIT gaps.
  STEP(cudaGraphLaunch(w.execution,w.stream)); STEP(cudaStreamSynchronize(w.stream));
  {
    float ms=0;
    uint32_t data[24];
    STEP(cudaEventElapsedTime(&ms,w.begin,w.end));
    STEP(cudaMemcpy(data,w.state+8,sizeof(data),cudaMemcpyDeviceToHost));
    std::memcpy(output->transcript,data,64); std::memcpy(output->digest,data+16,32);
    output->active_ns=uint64_t(ms*1000000.0);
    output->device_bytes=24ull*k+128; output->reused=reused; output->variant=variant;
  }
  return 0;
cleanup:
  clear_workspace(); return status;
}

extern "C" void boris_cuda_release() { clear_workspace(); if(context_used){cudaDeviceReset();context_used=false;} }

#include "local_grid.cuh"
