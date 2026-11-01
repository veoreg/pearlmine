# SPDX-License-Identifier: Apache-2.0
"""Fixed internal arithmetic/official golden fixtures only; no job/nonce search."""
import struct
from .proof_lab import digest,fill,fixture,MASK


def arithmetic_case(case_id):
    if type(case_id) is not int or not 0<=case_id<15:raise ValueError("Fixed arithmetic case ID must be0..14")
    k=(2048,2176,4096)[case_id//5];pattern=case_id%5
    def random_bytes(size,seed):
        out=bytearray();mask=(1<<64)-1
        for _ in range(size):
            seed=(seed+0x9e3779b97f4a7c15)&mask;x=seed
            x=((x^(x>>30))*0xbf58476d1ce4e5b9)&mask
            x=((x^(x>>27))*0x94d049bb133111eb)&mask
            out.append((x^(x>>31))&255)
        return out
    a=random_bytes(8*k,0x122334455667788^pattern)
    b=random_bytes(16*k,0x887766554433221^pattern)
    if pattern==0:a[:]=bytes(len(a));b[:]=bytes(len(b))
    if pattern in (1,2):
        a[::3 if pattern==1 else 7]=bytes([128 if pattern==1 else 127])*len(a[::3 if pattern==1 else 7])
        offset=1 if pattern==1 else 0;step=5 if pattern==1 else 3
        b[offset::step]=bytes([127 if pattern==1 else 128])*len(b[offset::step])
    if pattern==3:
        a[:]=bytes(len(a));b[:]=bytes(len(b))
        for row in range(8):a[row*k+(row*31+15)%k]=128;a[row*k+(row*31+16)%k]=127
        for col in range(16):b[col*k+(col*17+31)%k]=129;b[col*k+(col*17+32)%k]=126
    key=bytes((i*13+pattern)&255 for i in range(32))
    return bytes(a),bytes(b),k,key


def golden_rows(seed,tile_index=0):
    f=fixture(seed)
    if type(tile_index) is not int or not 0<=tile_index<8:raise ValueError("Golden tile index must be0..7")
    k=f["k"];tile=f["first_tiles"][tile_index]
    rows=[tile["t_rows"]+8*i for i in range(8)]
    cols=[tile["t_cols"]+8*(i//2)+i%2 for i in range(16)]
    def noised(domain,count,indices,seed_hex,label):
        raw=fill(seed,domain,count*k);noise_seed=bytes.fromhex(seed_hex)
        def nh(index,slot):
            msg=bytearray(64);msg[slot*4:slot*4+4]=struct.pack("<I",index+1)
            msg[32:32+len(label)]=label
            return digest(bytes(msg),noise_seed)
        pairs=[]
        for group in range(k//8):
            for x in struct.unpack("<8I",nh(group,1)):
                i=x&127;pairs.append((i,i^(1+((127*x)>>32))))
        result=bytearray()
        for row in indices:
            factors=[(x&63)-32 for block in range(4) for x in nh(4*row+block,0)]
            for x,(a,b) in zip(raw[row*k:(row+1)*k],pairs):
                y=(x if x<128 else x-256)+factors[a]-factors[b]
                if not -128<=y<=127:raise ValueError("Noised byte exceeds signed INT8")
                result.append(y&255)
        return bytes(result)
    a=noised(0x73706D2D612D3031,f["m"],rows,f["a_noise_seed"],b"A_tensor")
    b=noised(0x73706D2D622D3031,f["n"],cols,f["b_noise_seed"],b"B_tensor")
    return a,b,k,bytes.fromhex(f["a_noise_seed"])


def reference_tile(a,b,k,key):
    if type(k) is not int or k not in (2048,2176,4096) or len(a)!=8*k or len(b)!=16*k or len(key)!=32:
        raise ValueError("Fixed INT8 tile dimensions/key required")
    a=[x if x<128 else x-256 for x in a];b=[x if x<128 else x-256 for x in b]
    acc=[0]*128;transcript=[0]*16
    for start in range(0,k,128):
        folded=0
        for row in range(8):
            ar=a[row*k+start:row*k+start+128]
            for col in range(16):
                cell=row*16+col
                acc[cell]=(acc[cell]+sum(x*y for x,y in zip(ar,b[col*k+start:col*k+start+128])))&MASK
                folded^=acc[cell]
        word=transcript[(start//128)%16]
        transcript[(start//128)%16]=(((word<<13)|(word>>19))^folded)&MASK
    return dict(transcript=transcript,digest=digest(struct.pack("<16I",*transcript),key).hex())
