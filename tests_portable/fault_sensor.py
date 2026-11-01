"""Telemetry protocol fault fixture. No NVML/GPU/network access."""
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from boris_pearl.ipc import read_frame,write_frame

mode=sys.argv[1]
write_frame(sys.stdout.buffer,dict(type="ready",protocol=1,mode="telemetry-nvml"))
count=0
while True:
    try:
        r=read_frame(sys.stdin.buffer)
    except EOFError:
        break
    if mode=="hang":
        time.sleep(10)
        break
    if mode=="crash":
        raise SystemExit(7)
    count+=1
    sample=dict(observed_at=time.time(),gpu_c=40,watts=30,free_bytes=1<<30)
    result=dict(sample=sample,device=dict(index=0,name="test-only",uuid="test-identity"),errors=[])
    if mode=="identity-change" and count>1:
        result["device"]["uuid"]="changed"
    if mode=="malformed":
        result["sample"]={"unknown":5}
    write_frame(sys.stdout.buffer,dict(type="reply",mode="telemetry-nvml",id=r["id"],result=result))
