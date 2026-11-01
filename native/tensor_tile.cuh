// Boris Pearl INT8 WMMA prototype, Apache-2.0. No copied fragment layouts.
// Uses documented CUDA WMMA interfaces; all32 lanes follow identical control flow.
#pragma once
#include <mma.h>

__global__ void evaluate_wmma(const int8_t* a,const int8_t* b,uint32_t k,
                              const uint32_t* key,uint32_t* out) {
  namespace wmma=nvcuda::wmma;
  __shared__ __align__(32) signed char as[16*16];
  __shared__ __align__(32) signed char bs[16*16];
  __shared__ __align__(32) int cs[16*16];
  const uint32_t lane=threadIdx.x;
  wmma::fragment<wmma::matrix_a,16,16,16,signed char,wmma::row_major> af;
  wmma::fragment<wmma::matrix_b,16,16,16,signed char,wmma::col_major> bf;
  wmma::fragment<wmma::accumulator,16,16,16,int> acc;
  wmma::fill_fragment(acc,0);
  if(lane<16) out[lane]=0;
  __syncwarp();
  for(uint32_t slice=0;slice<k/128;++slice) {
    for(uint32_t sub=0;sub<128;sub+=16) {
      const uint32_t row=lane/4,q=(lane%4)*4,offset=slice*128+sub+q;
      int av,zero=0;
      memcpy(&av,a+row*k+offset,4);
      memcpy(as+row*16+q,&av,4);
      memcpy(as+(row+8)*16+q,&zero,4);
      for(uint32_t col=row;col<16;col+=8) {
        int bv;memcpy(&bv,b+col*k+offset,4);memcpy(bs+col*16+q,&bv,4);
      }
      __syncwarp();
      // Base pointers aligned32B and leading dimension16 bytes, no sub-tile misalignment.
      wmma::load_matrix_sync(af,as,16);
      wmma::load_matrix_sync(bf,bs,16);
      wmma::mma_sync(acc,af,bf,acc,false);
      __syncwarp();
    }
    wmma::store_matrix_sync(cs,acc,16,wmma::mem_row_major);
    __syncwarp();
    if(lane==0) {
      uint32_t fold=0;
      for(int i=0;i<128;++i) fold^=uint32_t(cs[i]);
      const uint32_t word=out[slice%16];
      out[slice%16]=((word<<13)|(word>>19))^fold;
    }
    __syncwarp();
  }
  if(lane==0) spm::b3::keyed_hash_one_block(key,out,out+16);
}

// Ampere+ direct dense MMA layout from NVIDIA PTX ISA m16n8k32 s8 fragments.
// A's lower eight rows are zero. Two N=8 instructions cover the exact8x16 tile.
__device__ __forceinline__ uint32_t packed_i8(const int8_t* p) {
  uint32_t value;memcpy(&value,p,4);return value;
}
__device__ __forceinline__ void mma_s8_16832(int* c,uint32_t a0,uint32_t a2,uint32_t b0,uint32_t b1) {
#if __CUDA_ARCH__ >= 800
  const uint32_t zero=0;
  asm volatile("mma.sync.aligned.m16n8k32.row.col.s32.s8.s8.s32 "
    "{%0,%1,%2,%3}, {%4,%5,%6,%7}, {%8,%9}, {%0,%1,%2,%3};"
    : "+r"(c[0]),"+r"(c[1]),"+r"(c[2]),"+r"(c[3])
    : "r"(a0),"r"(zero),"r"(a2),"r"(zero),"r"(b0),"r"(b1));
#endif
}
__global__ void evaluate_mma(const int8_t* a,const int8_t* b,uint32_t k,
                             const uint32_t* key,uint32_t* out) {
#if __CUDA_ARCH__ >= 800
  const uint32_t lane=threadIdx.x,row=lane/4,q=(lane%4)*4;
  int c[4]={0,0,0,0},d[4]={0,0,0,0};
  if(lane<16)out[lane]=0;
  __syncwarp();
  for(uint32_t slice=0;slice<k/128;++slice) {
    for(uint32_t sub=0;sub<128;sub+=32) {
      const uint32_t offset=slice*128+sub+q;
      const uint32_t a0=packed_i8(a+row*k+offset),a2=packed_i8(a+row*k+offset+16);
      mma_s8_16832(c,a0,a2,packed_i8(b+row*k+offset),packed_i8(b+row*k+offset+16));
      mma_s8_16832(d,a0,a2,packed_i8(b+(row+8)*k+offset),packed_i8(b+(row+8)*k+offset+16));
    }
    uint32_t fold=uint32_t(c[0])^uint32_t(c[1])^uint32_t(d[0])^uint32_t(d[1]);
    for(int shift=16;shift;shift/=2) fold^=__shfl_xor_sync(0xffffffff,fold,shift);
    if(lane==0) {
      const uint32_t word=out[slice%16];
      out[slice%16]=((word<<13)|(word>>19))^fold;
    }
    __syncwarp();
  }
  if(lane==0)spm::b3::keyed_hash_one_block(key,out,out+16);
#else
  // This entry is never admitted on pre-Ampere hardware; do not emulate silently.
  if(threadIdx.x==0)for(int i=0;i<24;++i)out[i]=0;
#endif
}
