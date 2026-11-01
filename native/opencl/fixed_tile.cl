// SPDX-License-Identifier: Apache-2.0 AND ISC
// Original portable fixed-tile test, not a mining/search kernel.
// BLAKE3 one-block algorithm adapted from cuda/common/blake3.cuh; preserved notice:
//
//   Copyright (c) 2025-2026 Pearl Research Labs
//   Copyright (c) 2015-2016 The Decred developers
//
//   Permission to use, copy, modify, and distribute this software for any purpose with or without
//   fee is hereby granted, provided that the above copyright notice and this permission notice
//   appear in all copies.
//
//   THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
//   SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
//   AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
//   WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
//   NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
//   PERFORMANCE OF THIS SOFTWARE.
//
// What PearlHash needs from BLAKE3 is always a one-block input (64 bytes, counter 0), so the hash
// is exactly one compression with CHUNK_START | CHUNK_END | ROOT (| KEYED_HASH when keyed):
//   * the noise hash   H(i, label, seed, slot) = blake3_keyed(seed, msg(i, slot, label))
// No subgroups, vendor intrinsics, matrix libraries or floating-point arithmetic.
inline uint rr(uint x,uint n) { return rotate(x,32u-n); }
inline void mix(__private uint *v,uint a,uint b,uint c,uint d,uint x,uint y) {
    v[a]+=v[b]+x;v[d]=rr(v[d]^v[a],16u);v[c]+=v[d];v[b]=rr(v[b]^v[c],12u);
    v[a]+=v[b]+y;v[d]=rr(v[d]^v[a],8u);v[c]+=v[d];v[b]=rr(v[b]^v[c],7u);
}
inline void keyed_block(__global const uint *key,__private uint *block,__global uint *out) {
    uint v[16],m[16],next[16];
    const uint perm[16]={2,6,3,10,7,0,4,13,1,11,12,5,9,14,15,8};
    for(uint i=0;i<8;i++)v[i]=key[i];
    v[8]=0x6A09E667u;v[9]=0xBB67AE85u;v[10]=0x3C6EF372u;v[11]=0xA54FF53Au;
    v[12]=0;v[13]=0;v[14]=64;v[15]=27; // KEYED_HASH | ROOT | CHUNK_START | CHUNK_END
    for(uint i=0;i<16;i++)m[i]=block[i];
    for(uint r=0;r<7;r++) {
        mix(v,0,4,8,12,m[0],m[1]);mix(v,1,5,9,13,m[2],m[3]);
        mix(v,2,6,10,14,m[4],m[5]);mix(v,3,7,11,15,m[6],m[7]);
        mix(v,0,5,10,15,m[8],m[9]);mix(v,1,6,11,12,m[10],m[11]);
        mix(v,2,7,8,13,m[12],m[13]);mix(v,3,4,9,14,m[14],m[15]);
        for(uint i=0;i<16;i++)next[i]=m[perm[i]];
        for(uint i=0;i<16;i++)m[i]=next[i];
    }
    for(uint i=0;i<8;i++)out[i]=v[i]^v[i+8];
}
__attribute__((reqd_work_group_size(128,1,1)))
__kernel void fixed_tile(__global const char *a,__global const char *bt,uint k,
                         __global const uint *key,__global uint *out) {
    const uint cell=get_local_id(0);
    __local uint cells[128];
    uint acc=0,transcript[16];
    for(uint i=0;i<16;i++)transcript[i]=0;
    // One 8x16 tile only. The host accepts fixed fixture IDs, never live jobs.
    for(uint s=0;s<k/128;s++) {
        for(uint x=s*128;x<(s+1)*128;x++) {
            int av=(int)a[(cell/16)*k+x],bv=(int)bt[(cell%16)*k+x];
            acc+=(uint)(av*bv); // defined modulo-2^32 addition; no signed overflow
        }
        cells[cell]=acc;barrier(CLK_LOCAL_MEM_FENCE);
        if(cell==0) {
            uint folded=0;for(uint i=0;i<128;i++)folded^=cells[i];
            transcript[s%16]=rotate(transcript[s%16],13u)^folded;
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }
    if(cell==0) {
        for(uint i=0;i<16;i++)out[i]=transcript[i];
        keyed_block(key,transcript,out+16);
    }
}
