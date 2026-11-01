# SPDX-License-Identifier: Apache-2.0
"""The pool page's embedded data; no network."""
import json
import unittest

from boris_pearl import pool_stats
from tests_portable.test_core import fixture_address


def page(values):
    return '<html><script type="application/json" data-nuxt-data="x" id="__NUXT_DATA__">'+json.dumps(values)+"</script></html>"


# Same shape as the live page of 2026-11-01: every object field is an index into the flat list.
LIVE=[
    {"reward":1,"paid":3,"unpaid":3},{"week":2,"month":2},None,0,
    {"total":5,"unconfirmed":5,"confirmed":3,"threshold":6,"banners":7},.003902548433689866,1,[8],"ungzippable",
    {"worker":10,"status":11,"valid":12,"stale":3,"invalid":3,"avg_hashrate_24h":13,"avg_hashrate_30m":14},"pearlmine","offline",3,"312749974122.95","0",
]


class PoolStatsTests(unittest.TestCase):
    def test_reads_balance_and_workers_from_the_embedded_data(self):
        stats=pool_stats.parse(page(LIVE))
        self.assertAlmostEqual(stats["pending"],.0039025484);self.assertEqual((stats["confirmed"],stats["paid"],stats["threshold"]),(0,0,1))
        worker=stats["workers"][0];self.assertEqual((worker["name"],worker["online"],worker["valid"],worker["stale"]),("pearlmine",False,3,0))
        self.assertAlmostEqual(worker["th_24h"],.31275,places=4)
        self.assertEqual(pool_stats.credited(stats),"0.003903 PRL")
        text=pool_stats.summary(stats);self.assertIn("подтверждено 0,",text);self.assertIn("в среднем за сутки 0.3 TH/s",text);self.assertIn("принято 3",text)

    def test_changed_page_is_an_error_not_a_zero_balance(self):
        for html in ("<html></html>",page([{"foo":1},2])):
            with self.assertRaises(ValueError):pool_stats.parse(html)
        with self.assertRaises(ValueError):pool_stats.parse('<script id="__NUXT_DATA__">{broken</script>')

    def test_only_a_public_address_on_kryptex_is_looked_up(self):
        kryptex="stratum+ssl://prl-sg.kryptex.network:8048";address=fixture_address(3)
        self.assertTrue(pool_stats.supported(address,kryptex))
        self.assertFalse(pool_stats.supported("myaccount",kryptex))
        self.assertFalse(pool_stats.supported(address,"stratum+ssl://pool.example:443"))
        self.assertFalse(pool_stats.supported(address,"stratum+ssl://kryptex.network.example.com:8048"))
        self.assertFalse(pool_stats.supported("",""))


if __name__=="__main__":
    unittest.main()
