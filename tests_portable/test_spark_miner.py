# SPDX-License-Identifier: Apache-2.0
"""Pure service and panel-row logic: no SSH, GPU, lock or systemd."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from boris_pearl import spark_miner
from boris_pearl.fleet import Fleet,SshPeer,miner_row,parse_ssh_line,QUERY
from tests_portable.test_core import fixture_address


class SparkMinerTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);run=Path(tmp.name)
        names=dict(RUN=run,WANT=run/"want.json",STOP=run/"stop",STATUS=run/"status.json",TOTALS=run/"totals.json",
            CLIENTS=run/"clients.json",INCOMING=run/"incoming")
        for name,value in names.items():
            patcher=mock.patch.object(spark_miner,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.run_dir=run
    def test_client_work_always_wins(self):
        self.assertFalse(spark_miner.client_work_waiting())
        spark_miner.CLIENTS.write_text(json.dumps(dict(comfy_running=[],comfy_pending=2)))
        self.assertTrue(spark_miner.client_work_waiting())
        spark_miner.CLIENTS.write_text(json.dumps(dict(comfy_running=["x"],comfy_pending=0)))
        self.assertTrue(spark_miner.client_work_waiting())
        spark_miner.CLIENTS.write_text(json.dumps(dict(comfy_running=[],comfy_pending=0)))
        spark_miner.INCOMING.mkdir();(spark_miner.INCOMING/"job").touch()
        self.assertTrue(spark_miner.client_work_waiting())
    def test_stop_forgets_the_request_and_supervisor_does_not_start_again(self):
        spark_miner.WANT.write_text(json.dumps(dict(wallet="w",pool="p",until=9e12)))
        with mock.patch("subprocess.run"):spark_miner.stop(None)
        self.assertFalse(spark_miner.WANT.exists());self.assertTrue(spark_miner.STOP.exists())
        with mock.patch("subprocess.run") as launched:spark_miner.supervise(None)
        launched.assert_not_called()
    def test_start_rejects_bad_destination_and_deadline_before_launching(self):
        with mock.patch("subprocess.run") as launched:
            with self.assertRaises(ValueError):spark_miner.start(mock.Mock(wallet="bad;rm -rf /",pool="stratum+ssl://p.example:1",seconds=600))
            with self.assertRaises(SystemExit):spark_miner.start(mock.Mock(wallet=fixture_address(3),pool="stratum+ssl://p.example:1",seconds=8*86400))
        launched.assert_not_called();self.assertFalse(spark_miner.WANT.exists())
    def test_supervisor_waits_for_clients_and_does_not_take_the_lock(self):
        spark_miner.WANT.write_text(json.dumps(dict(wallet="w",pool="p",until=9e12)))
        spark_miner.CLIENTS.write_text(json.dumps(dict(comfy_pending=1)))
        def after_one_wait(_seconds):spark_miner.STOP.touch()
        with mock.patch("subprocess.run") as launched,mock.patch("time.sleep",after_one_wait):spark_miner.supervise(None)
        launched.assert_not_called()
        self.assertIn("заказам",json.loads(spark_miner.STATUS.read_text(encoding="utf-8"))["reason"])
    def test_panel_row_shows_real_state(self):
        self.assertIn("74 TH/s",miner_row(dict(state="running",th=74,accepted=2,rejected=0))["note"])
        self.assertEqual(miner_row(dict(state="running",th=74))["state"],"running")
        self.assertIn("заказам",miner_row(dict(state="waiting",reason="Уступает заказам"))["note"])
    def test_box_style_peer_has_no_miner_commands(self):
        fleet=Fleet();row=dict(pairing=SshPeer("Бокс","root@box"),client=None,status=dict(devices={},miner_ready=False),notice=None,stop=mock.Mock(),threads=[])
        fleet.peers["ssh:root@box"]=row
        with self.assertRaisesRegex(ValueError,"нет службы"):fleet.miner("ssh:root@box","start","w","p",600)
    def test_query_is_read_only_shell_and_parses(self):
        self.assertNotIn("rm ",QUERY);self.assertEqual(parse_ssh_line("GPU-1, NVIDIA GB10, 50, 13.5, 0")[1]["name"],"NVIDIA GB10")
