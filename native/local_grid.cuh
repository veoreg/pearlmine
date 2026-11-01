// Boris Pearl bounded artificial full-grid experiment, Apache-2.0.
// No live header/target/network API. Uses unchanged Apache/ISC prep sources.
#pragma once
#include "../cuda/prep/prep.cu"

struct GridTiming { uint64_t active_ns; uint64_t device_bytes; };
struct GridWorkspace {
  uint32_t device=0,m=0,n=0,k=0;
  int8_t *a=nullptr,*b=nullptr,*af=nullptr,*bf=nullptr;
  uint8_t *ap=nullptr,*bp=nullptr,*prefix=nullptr;
  uint32_t *key=nullptr,*result=nullptr;
  uint64_t *cursor=nullptr;
  cudaStream_t stream=nullptr;
  cudaEvent_t begin=nullptr,end=nullptr;
  cudaGraph_t graph=nullptr;
  cudaGraphExec_t exec=nullptr;
  uint32_t graph_count=0;
};
static thread_local GridWorkspace grid_workspace;
static void clear_grid_graph() {
  auto& w=grid_workspace;
  if(w.exec)cudaGraphExecDestroy(w.exec);if(w.graph)cudaGraphDestroy(w.graph);
  w.exec=nullptr;w.graph=nullptr;w.graph_count=0;
}
static void clear_grid() {
  auto& w=grid_workspace;
  if(w.k)cudaSetDevice(w.device);
  clear_grid_graph();
  if(w.stream)cudaStreamDestroy(w.stream);
  if(w.begin)cudaEventDestroy(w.begin);if(w.end)cudaEventDestroy(w.end);
  if(w.a)cudaFree(w.a);if(w.b)cudaFree(w.b);if(w.af)cudaFree(w.af);if(w.bf)cudaFree(w.bf);
  if(w.ap)cudaFree(w.ap);if(w.bp)cudaFree(w.bp);if(w.prefix)cudaFree(w.prefix);
  if(w.key)cudaFree(w.key);if(w.result)cudaFree(w.result);if(w.cursor)cudaFree(w.cursor);
  w=GridWorkspace{};
}
__device__ __forceinline__ void grid_mma(int* c,uint32_t a0,uint32_t a1,uint32_t a2,uint32_t a3,uint32_t b0,uint32_t b1) {
#if __CUDA_ARCH__>=800
  asm volatile("mma.sync.aligned.m16n8k32.row.col.s32.s8.s8.s32 "
    "{%0,%1,%2,%3}, {%4,%5,%6,%7}, {%8,%9}, {%0,%1,%2,%3};"
    : "+r"(c[0]),"+r"(c[1]),"+r"(c[2]),"+r"(c[3])
    : "r"(a0),"r"(a1),"r"(a2),"r"(a3),"r"(b0),"r"(b1));
#endif
}
__global__ void evaluate_grid(const int8_t* a,const int8_t* b,uint32_t k,uint32_t n,
                             const uint64_t* cursor,const uint32_t* key,uint32_t* out) {
#if __CUDA_ARCH__ >= 800
  __shared__ __align__(16) int8_t shared_a[16*256];
  const uint32_t columns=n/64*4,warps=blockDim.x/32,warp=threadIdx.x/32;
  const uint64_t block_start=*cursor+uint64_t(blockIdx.x)*warps*2;
  const uint64_t position=block_start+warp*2;
  const uint32_t ri=uint32_t(block_start/(uint64_t(columns)*2))*2;
  const uint32_t ci=uint32_t((position/2)%columns);
  const uint32_t tr=(ri/8)*64+ri%8,tc=(ci/4)*64+(ci%4)*2;
  const uint32_t lane=threadIdx.x%32,row=lane/4,q=(lane%4)*4;
  const uint32_t br=tc+(row/2)*8+row%2;
  int c[4]={0,0,0,0},d[4]={0,0,0,0};uint32_t t[16]={},u[16]={};
  for(uint32_t base=0;base<k;base+=256) {
    for(uint32_t i=threadIdx.x*16;i<16*256;i+=blockDim.x*16) {
      uint32_t r=i/256,l=i%256,ar=tr+(r%8)*8+r/8;
      *reinterpret_cast<uint4*>(shared_a+i)=*reinterpret_cast<const uint4*>(a+uint64_t(ar)*k+base+l);
    }
    __syncthreads();
    for(uint32_t sub_slice=0;sub_slice<256;sub_slice+=128) {
      for(uint32_t sub=sub_slice;sub<sub_slice+128;sub+=32) {
        const uint32_t offset=base+sub+q,sa=sub+q;
        const uint32_t a0=packed_i8(shared_a+row*256+sa),a1=packed_i8(shared_a+(row+8)*256+sa);
        const uint32_t a2=packed_i8(shared_a+row*256+sa+16),a3=packed_i8(shared_a+(row+8)*256+sa+16);
        grid_mma(c,a0,a1,a2,a3,packed_i8(b+uint64_t(br)*k+offset),packed_i8(b+uint64_t(br)*k+offset+16));
        grid_mma(d,a0,a1,a2,a3,packed_i8(b+uint64_t(br+32)*k+offset),packed_i8(b+uint64_t(br+32)*k+offset+16));
      }
      uint32_t f=uint32_t(c[0])^uint32_t(c[1])^uint32_t(d[0])^uint32_t(d[1]);
      uint32_t g=uint32_t(c[2])^uint32_t(c[3])^uint32_t(d[2])^uint32_t(d[3]);
      for(int shift=16;shift;shift/=2){f^=__shfl_xor_sync(0xffffffff,f,shift);g^=__shfl_xor_sync(0xffffffff,g,shift);}
      if(lane==0){uint32_t slice=(base+sub_slice)/128,word=t[slice%16],other=u[slice%16];
        t[slice%16]=((word<<13)|(word>>19))^f;u[slice%16]=((other<<13)|(other>>19))^g;}
    }
    __syncthreads();
  }
  if(lane==0){uint32_t* dst=out+(uint64_t(blockIdx.x)*warps*2+warp*2)*24;
    for(int i=0;i<16;++i){dst[i]=t[i];dst[24+i]=u[i];}
    spm::b3::keyed_hash_one_block(key,t,dst+16);spm::b3::keyed_hash_one_block(key,u,dst+40);}
#endif
}
#define GRID_STEP(expr) do{cudaError_t err=(expr);if(err!=cudaSuccess){status=int(err);goto cleanup;}}while(0)
extern "C" int boris_grid_allocate(uint32_t device,uint32_t m,uint32_t n,uint32_t k) {
  if(device>31 || k!=4096 || !((m==131072 && n==131072)||(m==64 && n==64)||(m==512 && n==512)))return -1;
  clear_grid();auto& w=grid_workspace;w.device=device;w.m=m;w.n=n;w.k=k;
  int status=0;cudaDeviceProp prop;cudaFuncAttributes attributes;
  GRID_STEP(cudaSetDevice(device));context_used=true;
  status=variant_eligible(device,32);if(status)goto cleanup;
  GRID_STEP(cudaFuncGetAttributes(&attributes,evaluate_grid));
  GRID_STEP(cudaMalloc(&w.a,uint64_t(m)*k));GRID_STEP(cudaMalloc(&w.b,uint64_t(n)*k));
  GRID_STEP(cudaMalloc(&w.af,uint64_t(m)*128));GRID_STEP(cudaMalloc(&w.bf,uint64_t(n)*128));
  GRID_STEP(cudaMalloc(&w.ap,k*2));GRID_STEP(cudaMalloc(&w.bp,k*2));
  GRID_STEP(cudaMalloc(&w.prefix,1024));GRID_STEP(cudaMalloc(&w.key,32));
  GRID_STEP(cudaMalloc(&w.cursor,8));GRID_STEP(cudaMalloc(&w.result,4096*24*4));
  GRID_STEP(cudaStreamCreateWithFlags(&w.stream,cudaStreamNonBlocking));
  GRID_STEP(cudaEventCreate(&w.begin));GRID_STEP(cudaEventCreate(&w.end));return 0;
cleanup:clear_grid();return status;
}
extern "C" int boris_grid_chunk(uint64_t cursor,uint32_t count,uint64_t gen_seed,
                                const uint8_t* a_seed,const uint8_t* b_seed,const uint8_t* prefix,
                                uint32_t prepare_a,uint32_t prepare_b,uint32_t* result,GridTiming* timing) {
  auto& w=grid_workspace;
  if(!w.k || !result || !timing || !a_seed || !b_seed || !prefix || !count || count>4096 || count%16 || cursor%16 ||
     cursor+count>uint64_t(w.m)*w.n/128)return -1;
  int status=0;spm::prep::Seed sa,sb;std::memcpy(sa.w,a_seed,32);std::memcpy(sb.w,b_seed,32);
  GRID_STEP(cudaMemcpyAsync(w.cursor,&cursor,8,cudaMemcpyHostToDevice,w.stream));
  GRID_STEP(cudaMemcpyAsync(w.key,a_seed,32,cudaMemcpyHostToDevice,w.stream));
  if(prepare_a)GRID_STEP(cudaMemcpyAsync(w.prefix,prefix,1024,cudaMemcpyHostToDevice,w.stream));
  if(prepare_a || prepare_b || !w.exec || w.graph_count!=count) {
    clear_grid_graph();
    GRID_STEP(cudaStreamBeginCapture(w.stream,cudaStreamCaptureModeGlobal));
    GRID_STEP(cudaEventRecordWithFlags(w.begin,w.stream,cudaEventRecordExternal));
    if(prepare_b){
      GRID_STEP(spm::prep::launch_uniform_factor(w.bf,w.n,sb,spm::LABEL_B_W0,w.stream));
      GRID_STEP(spm::prep::launch_pairs(w.bp,w.k,sb,spm::LABEL_B_W0,w.stream));
      GRID_STEP(spm::prep::launch_noised_operand(w.b,{nullptr,gen_seed^spm::DOMAIN_BT,nullptr,0},w.bf,w.bp,w.n,w.k,false,w.stream));
    }
    if(prepare_a){
      GRID_STEP(spm::prep::launch_uniform_factor(w.af,w.m,sa,spm::LABEL_A_W0,w.stream));
      GRID_STEP(spm::prep::launch_pairs(w.ap,w.k,sa,spm::LABEL_A_W0,w.stream));
      GRID_STEP(spm::prep::launch_noised_operand(w.a,{nullptr,gen_seed^spm::DOMAIN_A,w.prefix,1024},w.af,w.ap,w.m,w.k,false,w.stream));
    }
    {uint32_t warps=(w.n==64?4:8);
      evaluate_grid<<<count/(warps*2),warps*32,0,w.stream>>>(w.a,w.b,w.k,w.n,w.cursor,w.key,w.result);}
    GRID_STEP(cudaGetLastError());GRID_STEP(cudaEventRecordWithFlags(w.end,w.stream,cudaEventRecordExternal));
    GRID_STEP(cudaStreamEndCapture(w.stream,&w.graph));
    GRID_STEP(cudaGraphInstantiate(&w.exec,w.graph,nullptr,nullptr,0));w.graph_count=count;
  }
  GRID_STEP(cudaGraphLaunch(w.exec,w.stream));GRID_STEP(cudaStreamSynchronize(w.stream));
  {float ms=0;GRID_STEP(cudaEventElapsedTime(&ms,w.begin,w.end));timing->active_ns=uint64_t(ms*1000000.0);
    timing->device_bytes=uint64_t(w.m+w.n)*(w.k+128)+4ull*w.k+1024+32+8+4096*24*4;}
  GRID_STEP(cudaMemcpy(result,w.result,count*24*4,cudaMemcpyDeviceToHost));
  if(prepare_a || prepare_b)clear_grid_graph(); // Never repeat prep in a warm graph.
  return 0;
cleanup:clear_grid();return status;
}
extern "C" void boris_grid_release(){clear_grid();boris_cuda_release();}
#undef GRID_STEP
