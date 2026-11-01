# SPDX-License-Identifier: Apache-2.0
"""Read-only check for a newer public release. It only tells the user; nothing is downloaded or installed."""
import json
import re
import urllib.request

from . import __version__

LATEST="https://api.github.com/repos/veoreg/pearlmine/releases/latest"
RELEASES="https://github.com/veoreg/pearlmine/releases/latest"


def numbers(version):
    return tuple(int(part) for part in re.findall(r"\d+",version.split("-")[0])[:3])


def newer_than_this(tag,current=__version__):
    return numbers(tag)>numbers(current)


def check(timeout=15):
    """The newer version's tag, or None; a failed check is simply no news."""
    request=urllib.request.Request(LATEST,headers={"User-Agent":"PearlMine","Accept":"application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as reply:
            tag=json.load(reply).get("tag_name") or ""
    except (OSError,ValueError):
        return None
    return tag if numbers(tag) and newer_than_this(tag) else None
