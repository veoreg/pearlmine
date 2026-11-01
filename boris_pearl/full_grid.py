"""Experimental resident grid for fixed artificial data only. No pool or live consent."""
from hashlib import sha256
import json
import os
import time

from .core import Job
from .cuda_worker import CudaWorker,ChunkResult,FIXED_NONCE,compact_target
from .ipc import ProcessWorker,WorkerError,WorkerInterrupted
from .official_bridge import verify


class FullGridWorker(CudaWorker):
    def __init__(self,binary,expected_sha256,verifier,*,size,check_all=False,expected_device_uuid=None):
        super().__init__(binary,expected_sha256,verifier,expected_device_uuid=expected_device_uuid)
        if type(size) is not int or size not in (64,512,131072) or type(check_all) is not bool or (check_all and size>512):
            raise ValueError("Fixed local grid size/check limits")
        self.size,self.check_all=size,check_all
        self.transport=ProcessWorker([str(self.binary.resolve()),"--full-grid-local-test"],
            expected_mode="cuda-baseline",env=dict(os.environ,RAYON_NUM_THREADS="2"))
        self.executed=0
        self.audits=[]

    def bind_job(self,job,spec):
        from .local_search import HEADER
        if (spec.header76!=HEADER or int(spec.target,16)!=1 or spec.nonce!=FIXED_NONCE or
                (spec.m,spec.n,spec.k)!=(self.size,self.size,4096) or spec.backend!="mma-int8"):
            raise ValueError("Full grid accepts only the fixed internal artificial fixture")
        super().bind_job(job,spec)

    def execute(self,key,budget_ns,*,timeout=10,permit=None):
        def allowed():return permit() if permit else None
        reason=allowed()
        if reason:self.close();raise WorkerInterrupted(reason)
        job,spec,binding=self.bindings[key]
        if budget_ns<self.minimum_slice_ns:return ChunkResult(0,[])
        if self.active_binding is None:
            ready=self.transport.request("full-grid-prepare",dict(size=self.size,check_all=self.check_all,
                device_index=spec.device_index),timeout=30,permit=allowed)
            if (ready.get("mode")!="full-grid-local-test" or ready.get("header76")!=spec.header76 or
                ready.get("shape")!=[self.size,self.size,4096] or
                ready.get("tiles_per_nonce")!=self.size*self.size//128 or
                (self.expected_device_uuid and ready.get("device_uuid")!=self.expected_device_uuid)):
                raise WorkerError("Artificial grid preparation/device identity mismatch")
            self.prepare_seconds=ready["prepare_wall_seconds"]
            self.active_binding=binding
        elif self.active_binding!=binding:
            raise WorkerError("Artificial grid cannot change job")
        result=self.transport.request("full-grid-chunk",dict(max_tiles=4096,budget_ns=budget_ns),timeout=timeout,permit=allowed)
        total,count,active=(result.get(k) for k in ("executed_tiles","tiles_this_chunk","active_ns"))
        tiles=self.size*self.size//128
        if (result.get("mode")!="full-grid-local-test" or any(type(x) is not int for x in (total,count,active)) or
            not 1<=count<=4096 or total!=self.executed+count or not 0<=active<=budget_ns or
            result.get("nonce")!=FIXED_NONCE+(total-1)//tiles or
            result.get("next_cursor")!=(total-1)%tiles+1 or result.get("tiles_per_nonce")!=tiles or
            result.get("chunk_start")!=(self.executed%tiles)):
            raise WorkerError("Artificial grid continuity/accounting mismatch")
        for candidate in result.pop("audits",[]):
            proof=candidate.get("proof",{})
            if (len(self.audits)>=4 or candidate.get("header76")!=spec.header76 or
                    candidate.get("certificate_version")!=3 or
                    candidate.get("nbits_override")!=compact_target(f"{((1<<256)-1)>>19:064x}") or
                    (proof.get("m"),proof.get("n"),proof.get("k"),proof.get("noise_rank"))!=(self.size,self.size,4096,128)):
                raise WorkerError("Artificial audit limit/header mismatch")
            checked=verify(candidate,self.verifier.executable,self.verifier.expected_sha256)
            self.audits.append(dict(wire_bytes=len(checked.wire),wire_sha256=sha256(checked.wire).hexdigest(),
                gpu_digest=checked.digest,nbits_override=candidate["nbits_override"],
                scope="separate easy-target audit only; not a hit against hard search target"))
        self.executed=total
        result["host_prepare_wall_seconds"]=self.prepare_seconds
        result["kernel_variant"]=32
        result["official_audits"]=len(self.audits)
        result["nonce_changes"]=(total-1)//tiles
        self.minimum_slice_ns=min(1_000_000_000,max(self.minimum_slice_ns,active*2))
        return ChunkResult(active,[],result)
