"""Local configuration and PRL P2TR checksum validation; no secrets are accepted."""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from urllib.parse import urlsplit

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def polymod(values):
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, generator in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            if (top >> i) & 1:
                chk ^= generator
    return chk


def valid_address(address):
    if not isinstance(address, str) or len(address) != 63:
        return False
    if address != address.lower() and address != address.upper():
        return False
    address = address.lower()
    if not address.startswith("prl1p"):
        return False
    try:
        data = [CHARSET.index(c) for c in address[4:]]
    except ValueError:
        return False
    hrp = [ord(c) >> 5 for c in "prl"] + [0] + [ord(c) & 31 for c in "prl"]
    if polymod(hrp + data) != 0x2BC830A3 or data[0] != 1:
        return False
    # 32-byte witness program, with canonical zero padding from 8 to 5 bits.
    payload = data[1:-6]
    return len(payload) == 52 and payload[-1] & 15 == 0


def is_kryptex(url):
    host = urlsplit(url).hostname or "" if isinstance(url, str) else ""
    return host == "kryptex.network" or host.endswith(".kryptex.network")


def valid_payout(wallet, pool):
    """A public PRL address, or a Kryptex account name on a Kryptex pool (it pays out in BTC/USDT)."""
    if valid_address(wallet):
        return True
    return is_kryptex(pool) and re.fullmatch(r"[A-Za-z0-9_-]{3,40}", wallet) is not None


def pool_url(url):
    if not isinstance(url, str):
        raise ValueError("Pool URL must be text")
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError as exc:
        raise ValueError("Invalid pool port") from exc
    if (p.scheme not in ("stratum+ssl", "stratum+tcp") or not p.hostname or
            port is None or not 1 <= port <= 65535 or p.username or p.password or
            p.path or p.query or p.fragment or any(c.isspace() for c in url)):
        raise ValueError("Use stratum+ssl://host:port or stratum+tcp://host:port without credentials")
    return url


# Public payout of the disclosed 2% developer fee; the user can change or turn it off in the panel.
DEFAULT_DEVELOPER_WALLET = "prl1pg5unjt00nwfkevzc2uldgdgv4qykr0h98ph630p480fswtnarwssdm3sfa"
DEFAULT_DEVELOPER_POOL = "stratum+ssl://prl-sg.kryptex.network:8048"


@dataclass(frozen=True)
class Config:
    user_wallet: str = ""
    user_pool: str = ""
    fee_bps: int = 0
    developer_wallet: str = ""
    developer_pool: str = ""
    pool_fee_percent: float | None = None

    def __post_init__(self):
        for value in (self.user_wallet, self.user_pool, self.developer_wallet, self.developer_pool):
            if not isinstance(value, str):
                raise ValueError("Wallets and pool endpoints must be text")
        if type(self.fee_bps) is not int or self.fee_bps not in (0, 150, 200):
            raise ValueError("Developer fee must be 0, 150 or 200 basis points")
        for wallet, pool in ((self.user_wallet, self.user_pool), (self.developer_wallet, self.developer_pool)):
            if wallet and not valid_payout(wallet, pool):
                raise ValueError("Expected a public PRL mainnet P2TR address with a valid checksum")
        for url in (self.user_pool, self.developer_pool):
            if url:
                pool_url(url)
        if self.fee_bps and (not self.developer_wallet or not self.developer_pool):
            raise ValueError("A nonzero fee requires the developer's public address and pool")
        if self.pool_fee_percent is not None and (
                type(self.pool_fee_percent) not in (int, float) or
                not math.isfinite(self.pool_fee_percent) or not 0 <= self.pool_fee_percent <= 100):
            raise ValueError("Pool fee must be unknown (null) or a percentage from 0 to 100")

    @property
    def effective_bps(self):
        if self.user_wallet and self.user_wallet.lower() == self.developer_wallet.lower():
            return 0
        return self.fee_bps

    @property
    def identity(self):
        payload = (self.user_wallet.lower(), self.user_pool, self.effective_bps,
                   self.developer_wallet.lower(), self.developer_pool)
        return sha256(json.dumps(payload).encode()).hexdigest()

    def disclosure(self):
        pool_fee = "unknown; confirm with the pool" if self.pool_fee_percent is None else f"{self.pool_fee_percent:g}% (user supplied)"
        return (f"Developer fee: {self.effective_bps / 100:.2f}% of active GPU time\n"
                f"Developer payout: {self.developer_wallet or 'not configured'}\n"
                f"Developer pool: {self.developer_pool or 'not configured'}\n"
                f"User payout: {self.user_wallet or 'not configured'}\n"
                f"User pool: {self.user_pool or 'not configured'}\n"
                f"Pool fee, separate: {pool_fee}\n"
                "A time percentage does not guarantee the same percentage of revenue.\n"
                "Live mining is disabled in this prototype.")


def load_config(path):
    with open(path, encoding="utf-8") as stream:
        return Config(**json.load(stream))
