"""Read-only: what the pool has credited to a public payout address, from its public stats page.

Only the address goes out, and it is public by design. A Kryptex account name has no public page.
"""
import json
import re
import urllib.request

from .config import is_kryptex, valid_address

PAGE="https://pool.kryptex.com/prl/miner/stats/{}"
LIMIT=4<<20
DATA=re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>',re.S)


def supported(wallet,pool):
    return bool(wallet and pool) and valid_address(wallet) and is_kryptex(pool)


def page_url(wallet):
    return PAGE.format(wallet)


def parse(html):
    """The page embeds its data as one flat list where every object field is an index into that list."""
    found=DATA.search(html)
    if not found:raise ValueError("Pool page has no data block")
    values=json.loads(found.group(1))
    def field(row,key):
        index=row.get(key)
        return values[index] if isinstance(index,int) and 0<=index<len(values) else None
    rows=[row for row in values if isinstance(row,dict)]
    balance=next((r for r in rows if {"unconfirmed","confirmed","threshold"}<=set(r)),None)
    totals=next((r for r in rows if {"reward","paid"}<=set(r)),None)
    if balance is None or totals is None:raise ValueError("Pool page has no balance")
    workers=[dict(name=field(r,"worker"),online=field(r,"status")=="online",valid=field(r,"valid") or 0,stale=field(r,"stale") or 0,invalid=field(r,"invalid") or 0,
                  th_30m=float(field(r,"avg_hashrate_30m") or 0)/1e12,th_24h=float(field(r,"avg_hashrate_24h") or 0)/1e12)
             for r in rows if {"worker","avg_hashrate_24h"}<=set(r)]
    return dict(pending=float(field(balance,"unconfirmed") or 0),confirmed=float(field(balance,"confirmed") or 0),threshold=float(field(balance,"threshold") or 0),
                paid=float(field(totals,"paid") or 0),workers=workers)


def fetch(wallet,timeout=20,attempts=3):
    """The page often stalls halfway through, so one slow read is not yet a failure."""
    request=urllib.request.Request(page_url(wallet),headers={"User-Agent":"Mozilla/5.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request,timeout=timeout) as reply:
                return parse(reply.read(LIMIT).decode("utf-8","replace"))
        except OSError:
            if attempt==attempts-1:raise


def amount(value):
    return "0" if not value else f"{value:.6f}"


def credited(stats):
    return f"{amount(stats['pending']+stats['confirmed'])} PRL"


def summary(stats):
    workers=stats["workers"]
    return (f"На пуле: ждёт подтверждения {amount(stats['pending'])} PRL, подтверждено {amount(stats['confirmed'])}, выплачено {amount(stats['paid'])}. "
            f"Выплата в кошелёк от {stats['threshold']:g} PRL. "
            f"Пул видит: сейчас {sum(w['th_30m'] for w in workers):.0f} TH/s, в среднем за сутки {sum(w['th_24h'] for w in workers):.1f} TH/s; "
            f"принято {sum(w['valid'] for w in workers)}, устаревших {sum(w['stale'] for w in workers)}, отклонено {sum(w['invalid'] for w in workers)}.")
