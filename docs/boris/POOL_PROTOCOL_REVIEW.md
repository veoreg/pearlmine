# Pool/consensus review - 2026-10-31

No pool is selected in the empty example configuration. Kryptex is the working
research assumption because its adapter/captures already exist; this does not
select a live destination. No mining-endpoint connection, authorization, real
job fetch, TLS probe, share or pool-account operation was performed.

## Official operator materials

The current [Kryptex PRL page](https://pool.kryptex.com/en/prl) advertises TCP7048,
validated-TLS8048, region-specific hostnames and public-wallet/worker login.
It currently displays PPS+3%, SOLO1%, minimum payout1PRL and static difficulty
2,097,152, with custom difficulty via password. These are operator-page facts
observed on this date, not results measured by our client. Pool fees remain
separate from the disclosed Boris developer fee; no value was silently written
to user configuration. Recheck the chosen pool/mode before a live pilot.

The operator's [setup guide](https://pool.kryptex.com/articles/how-to-mine-pearl-en)
also shows wallet.worker syntax for some clients and SSL support.
The operator's [KRig README](https://github.com/kryptex/krig-miner) uses8048 and
wallet/worker. That repository exposes documentation/releases, not an inspectable
Pearl transport implementation; no executable was downloaded or run.
Its [release license](https://github.com/kryptex/krig-miner/blob/master/LICENSE)
was read; no KRig code/binary/dependency was reused.

These official pages do not specify the exact JSON request/reply schema,
the cert_version field contract, target byte order, gzip negotiation/credit,
timeout/stale handling, or all supported proof shapes. They therefore cannot
confirm actual acceptance/credit of our implementation. No undocumented
compression fallback was enabled. No third-party pool's dialect was silently
applied to Kryptex.

## Existing transport and concrete fix

The unchanged upstream docs/protocol/kryptex.md and redacted2026-09-26 fixture
are historical client observations, not a current operator specification.
They describe array authorization, object notifications with cert_version3,
target taken exactly as sent, and an optional explicitly acknowledged v2 gzip
session. The preserved old fee number is not the current operator page's fee.

The new adapter already replays those fixtures without network access. This
review found its Kryptex submit.worker contained just the short worker name,
while authorization used wallet.worker. Both Kryptex paths now emit the exact
same wallet.worker identity in submit. Object dialect retains separate wallet
and worker. Loopback tests assert identity and proof-byte preservation for
plain and v2-gzip replies. Mock acceptance is not real-pool acceptance.

Gzip remains enabled only after an explicit type:v2 acknowledgment; otherwise
raw canonical bincode is used. Validation/dispatch still binds owner/session/
epoch/header/target/certificate, rejects stale/duplicate proofs and does not
resend after uncertain acknowledgment. External transport requires validated
TLS and separate endpoint permission; sending requires another explicit grant.

## Official consensus cross-check

Current [Pearl chain parameters](https://github.com/pearl-research-labs/pearl/blob/master/node/chaincfg/params.go)
select certificateV3 at/after the salted-seed fork (mainnet height99,000).
This supports retaining the strict V3 gate; unknown/missing versions remain
update-required, never guessed. The current
[official verifier implementation](https://raw.githubusercontent.com/pearl-research-labs/pearl/master/zk-pow/src/api/verify.rs)
accepts an explicit pool-share nbits override, rejects MoE, bounds strip values
and recomputes noise/jackpot/rank checks. This agrees with the pinned local
dense salted verification path at the reviewed API level.

The py-pearl-mining README still describes only V1/V2. It is not sufficient
alone to negotiate current certificate rules. Do not rewrite received target
from a displayed difficulty or roll the header timestamp without redoing its
immutable commitment. The compiled verifier remains pinned to3fe2267; browsing
master source is not an upgrade or proof that all current code equals that pin.

## Required before a live test

1. User chooses the exact pool/region/mode and supplies public PRL destinations.
2. Obtain current authoritative JSON wire/encoding requirements from operator
   documentation/support or a separately authorized bounded handshake; verify
   notify.cert_version and actual target convention. No contact was made here.
3. With explicit live/submission consent only, run a short monitored pilot and
   compare accepted canonical proof hashes, owner attribution and actual credited
   records independently. A true reply alone does not establish credited income.
4. Qualify the live full-size worker/load/stop path separately; the resident full
   grid experiment accepts only internally fixed artificial data and is not
   connected to the pool coordinator.

The local Python GitHub metadata fetch returned connection-refused10061; web
reading supplied the official materials above. Network/system settings were
not edited. WSL access was not retried or bypassed.
