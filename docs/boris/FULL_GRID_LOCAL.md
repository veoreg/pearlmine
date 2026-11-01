# Artificial full-grid experiment - development only

A separate resident-data mode investigates complete local search at
131072x131072x4096 without opening a pool or accepting a live header.
It is not a production backend and is not selected by GUI or PoolCoordinator.

## Exact bounds and data flow

Native --full-grid-local-test builds its header, target1, initial nonce and
r128 configuration internally. Prepare accepts only size (64,512 or131072),
device index and a strict boolean small-fixture check flag. External header,
target, seed, nonce, resume fields and normal prepare/chunk commands are refused.
One immutable grid per process; native process ceiling1800s. At most one whole
nonce plus min(4096,tiles_per_nonce) next-nonce tiles can execute.
No wallet or network code is present in the native worker.

Unchanged upstream GPU prep constructs exact SplitMix/noised operands.
Full-size resident allocations total1,107,706,920 bytes; admission checks this
plus1GiB free reserve. Driver context overhead is separate. GPU prep and
arithmetic are included in one CUDA-event interval under a durable reservation.
CPU Merkle build, transfers, verification and pacing are not fee active time.

The optimized INT8 kernel uses both halves of dense m16n8k32 accumulators for
two adjacent legal A-strip offsets. Four/eight warps share a16x256 A panel and
cover neighboring B strips. Each hash retains cumulative INT32 dots and folds
after every128 K entries; no floating-point approximation. Paired cursor order
covers every legal strip pair once per nonce. B remains resident across nonce
changes; A is regenerated from the next committed root.

Every GPU transcript/digest enters a deterministic bulk BLAKE3 checksum.
Small fixtures compare every tile with original CPU host regeneration.
Full-size mode independently CPU-checks the first/last result of each4096-tile
batch; it does not CPU-recompute every full-size tile. Boundary proofs at the
first/last tile and next nonce also pass the pinned verifier process under a
separate explicitly easy audit target. They are not hard-target search hits,
never routed for submission. An unexpected hard-target hit stops the test.

## Controls and reproduction

~~~text
python tools/boris-full-grid-test.py --size 512 --seconds 45 --duty 0.1 --check-all
python tools/boris-full-grid-test.py --size 131072 --seconds 1800 --duty 0.25 --max-watts 125 --output docs/boris/evidence/full-size/grid-131072-complete.json
~~~

Existing read-only NVML/AC/UUID guards, independent deadline, latched critical
stop, two CPU worker threads and cancellable pacing apply.125W is an observed
stop threshold, not a hardware cap. No power/clock/system settings are written.
Final pacing interval is paid before normal completion. Elapsed time freezes
at Stop; later NVML teardown does not distort speed. Deadline/partial scans
are reported incomplete.

## Evidence and practical limits

See evidence/full-size/final-summary.json for executed results and exact hashes.
The initial one-warp resident kernel was intentionally stopped after a prefix
because full coverage would exceed the short test window. Its report is
retained as incomplete initial-prefix evidence. Later results have their own
hashes and cannot reuse that prefix as successful full coverage.

The live-shaped tile worker still CPU-checks every tile and remains separate.
Moving this resident path into live job/fee/target/owner handling requires
immutable context, checkpoint/eviction, actual-hit proof and safety/dispatch
qualification. Artificial arithmetic throughput is not pool-accepted hashrate,
credited work or income. Full-size repetition/full-load soak, platform/device
tests and authoritative operator wire confirmation remain separate gates.

Sensor evidence note: the first long run was started with the earlier1300-row
report retention bound. Its saved temperature/power extrema describe that
retained window, not necessarily the whole run. Guards still checked fresh
telemetry throughout. Delivered source retains4000 rows for future1800s tests.
This reporting fix does not alter GPU arithmetic, stops, pacing or accounting.

## Completed physical run

On Windows x64/RTX5090, binary
ac12a272abcd05fa2fa0a145be124ed87e3a8c4b09fac3c7165ee0b3a1cd272d
completed all134,217,728 tiles of one131072x131072x4096 nonce, then4096 next-nonce
tiles:134,221,824 total in1273.188s (21m13s). Measured105,421.84 artificial
tiles/wall-second includes CPU checks, job preparation and software pacing.
CUDA-event sum119.102156286s, whole-work268.457s, measured busy21.0854% under
the configured25% ceiling. No income or accepted-share hashrate is inferred.

All65,538 independent CPU boundary checks matched. The first/last full-grid
tile and first next-nonce tile each produced a102,193-byte proof accepted by
the separate pinned verifier under the explicit easy audit target. First and
next-nonce canonical hashes differ. No hard-target hit, external pool connection
or real submit occurred. GPU memory buffers were1,107,706,920 bytes plus driver
overhead. Completion Stop took26.26ms; worker reaped and absent from fresh NVML
compute-PID data. There were no safety pauses. Retained last1300 sensor rows
show max32C/78.428W; these are window extrema, not full-run maxima.
Detailed counters, exact proof hashes, timestamps and checksum are in
evidence/full-size/grid-131072-complete.json; final-summary.json validates them.

Separate fixed small512x512x4096 tests compare every tile across two nonces
(4096 total) and independently accept four audit proofs. Manual/sensor-loss
tests and a full-size resident deadline without another tick cover teardown.
The full-size artificial pass is not a full-load/OEM soak or a live release.
