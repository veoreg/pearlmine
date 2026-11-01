# SPDX-License-Identifier: Apache-2.0
"""Hidden Tk windows with synthetic services; no GPU, network or real settings file."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from boris_pearl.config import Config
from boris_pearl.remote_control import RemoteClient

try:
    import tkinter as tk
    from boris_pearl.dashboard import Dashboard
    from boris_pearl.agent_ui import AgentWindow,local_addresses
except ImportError:
    tk=None
from boris_pearl.control_center import DeviceService
from tests_portable.test_control_center import devices,FakeSession
from tests_portable.test_core import fixture_address
from tests_portable.test_devices import U1

WALLET_BAD="prl1pnotvalid"


class PassiveFleet:
    def __init__(self):self.stops=[];self.removed=[];self.closed=False;self.rows={}
    def connect(self,pairing):return pairing.fingerprint
    def stop(self,key=None):self.stops.append(key)
    def remove(self,key):self.removed.append(key)
    def snapshot(self):return self.rows
    def close(self):self.closed=True
    def alive(self):return False


@unittest.skipIf(tk is None,"Tkinter is not available")
class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="boris-ui-test-");self.path=Path(self.tmp.name)/"settings.json"
        self.addCleanup(self.tmp.cleanup);self.open()
    def open(self):
        self.root=tk.Tk();self.root.withdraw();self.sessions=[]
        def factory(**kwargs):
            session=FakeSession(**kwargs);self.sessions.append(session);return session
        self.service=DeviceService(inventory_provider=devices,session_factory=factory,probe_sensors=False)
        self.fleet=PassiveFleet()
        self.ui=Dashboard(self.root,service=self.service,fleet=self.fleet,auto_discover=False,settings_path=self.path)
        self.addCleanup(self.shut)
    def shut(self):
        if self.root is None:return
        self.ui.close();deadline=time.monotonic()+5
        while self.root is not None and time.monotonic()<deadline:
            try:self.root.update();time.sleep(.01);self.root.winfo_exists()
            except tk.TclError:self.root=None
        if self.root is not None:self.fail("Window did not close after its work ended")
    def pump(self,condition=lambda:True,timeout=2):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.root.update()
            if condition():return
            time.sleep(.01)
        self.fail("Expected UI state was not reached")
    def test_opening_starts_nothing_and_settings_survive_reopen(self):
        self.service.refresh();self.pump(lambda:not self.service.snapshot()["refreshing"])
        self.assertEqual(self.sessions,[]);self.assertEqual(self.ui.metrics["active"].get(),"0")
        self.ui.config_vars["user_pool"].set("stratum+ssl://pool.example:443");self.ui.config_vars["pool_fee_percent"].set("1,5")
        self.ui.profile.set("Ноутбук");self.ui.duration.set("3 мин");self.ui.save_settings()
        self.assertIn("сохранены",self.ui.settings_message.get())
        self.shut();self.open()
        self.assertEqual(self.ui.config_vars["user_pool"].get(),"stratum+ssl://pool.example:443")
        self.assertEqual(self.ui.config_vars["pool_fee_percent"].get(),"1.5")
        self.assertEqual(self.ui.profile.get(),"Ноутбук");self.assertEqual(self.ui.duration.get(),"3 мин")
    def test_credited_balance_shows_for_the_saved_address_and_survives_a_failed_read(self):
        wallet=fixture_address(3);self.assertEqual(self.ui.metrics["credited"].get(),"—")
        calls=[]
        def reader(address):
            calls.append(address)
            if len(calls)>1:raise OSError("pool down")
            return dict(pending=.003903,confirmed=0.0,threshold=1.0,paid=0.0,workers=[dict(name="pearlmine",online=False,valid=3,stale=0,invalid=0,th_30m=0.0,th_24h=.3)])
        self.ui.pool_reader=reader;self.ui.saved_config=Config(wallet,"stratum+ssl://prl-sg.kryptex.network:8048")
        self.pump(lambda:self.ui.metrics["credited"].get()=="0.003903 PRL")
        self.assertEqual(calls,[wallet]);self.assertIn("принято 3",self.ui.pool_line.get());self.assertIn("от 1 PRL",self.ui.pool_line.get())
        self.ui.pool_next=0;self.pump(lambda:"прошлые данные" in self.ui.pool_line.get())
        self.assertEqual(self.ui.metrics["credited"].get(),"0.003903 PRL")
        self.ui.saved_config=Config(wallet,"stratum+ssl://pool.example:443");self.pump(lambda:self.ui.metrics["credited"].get()=="—")
        self.assertEqual(self.ui.pool_line.get(),"");self.assertEqual(len(calls),2)
    def test_remote_mining_is_called_mining_not_a_test(self):
        status=dict(devices={"GPU-1":dict(name="RTX 5090",state="running",eligible=False,temperature=70,watts=570,note="Майнинг 275 TH/s · принято 16, отклонено 0")},active=1,miner=dict(state="running"),miner_ready=True)
        self.fleet.rows={"ssh:box":dict(name="Бокс",status=status,error=None,notice=None,updated_at=time.time())}
        self.pump(lambda:"Идёт майнинг: здесь 0, на других компьютерах 1" in self.ui.activity.get())
        self.assertEqual(self.ui.table.item("remote:ssh:box:GPU-1","values")[2],"Майнинг")
    def test_mined_columns_stay_after_stop_and_estimate_from_the_address_total(self):
        status=dict(devices={"GPU-1":dict(name="GB10",state="viewing",eligible=False,note="Майнинг выключен, можно включить")},active=0,miner=dict(state="stopped",accepted=4,all_time=30),miner_ready=True)
        self.fleet.rows={"ssh:spark":dict(name="Spark",status=status,error=None,notice=None,updated_at=time.time())}
        self.service.tally[U1]=dict(total=10,last=2)
        self.ui.pool_stats=dict(pending=.04,confirmed=0.0,paid=0.0,threshold=1.0,workers=[]);self.ui.pool_reader=lambda w:self.ui.pool_stats
        self.ui.saved_config=Config(fixture_address(3),"stratum+ssl://prl-sg.kryptex.network:8048")
        self.service.refresh();self.pump(lambda:self.ui.table.exists("remote:ssh:spark:GPU-1") and self.ui.table.exists(U1))
        self.pump(lambda:"≈" in self.ui.table.item("remote:ssh:spark:GPU-1","values")[6])
        row=self.ui.table.item("remote:ssh:spark:GPU-1","values")
        self.assertEqual((str(row[6]),str(row[7])),("4 · ≈0.0040","30 · ≈0.0300"))
        mine=self.ui.table.item(U1,"values");self.assertEqual((str(mine[6]),str(mine[7])),("2 · ≈0.0020","10 · ≈0.0100"))
        self.assertEqual(json.loads((self.path.parent/"mined_known.json").read_text(encoding="utf-8")),{U1:10,"ssh:spark":30})
    def test_card_share_count_survives_reopen(self):
        class Miner:shares=dict(accepted=7)
        self.service.use_tally(self.path.parent/"mined.json");self.service.tally["GPU-a"]=dict(total=5,last=5)
        self.assertEqual(self.service._count_shares("GPU-a",Miner,5,0),7)
        self.shut();self.open()
        self.assertEqual(self.service.tallied()["GPU-a"],dict(total=12,last=7))
    def test_bad_form_gives_russian_error_and_does_not_block_remembering_a_computer(self):
        self.ui.config_vars["user_wallet"].set(WALLET_BAD);self.ui.save_settings()
        self.assertIn("prl1p",self.ui.settings_message.get());self.assertFalse(self.path.exists())
        self.ui.saved_peers.append(dict(name="x",url="https://127.0.0.1:1",fingerprint="f"*64,protected_token="AA=="));self.ui._save()
        saved=json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["config"]["user_wallet"],"");self.assertEqual(len(saved["paired"]),1)
    def test_nonzero_fee_without_developer_address_is_explained(self):
        self.ui.fee.set("2%");self.ui.check_settings()
        self.assertIn("разработчика",self.ui.settings_message.get());self.assertTrue(self.ui.settings_message.get().startswith("Проверьте"))
    def test_corrupt_settings_are_kept_aside_not_overwritten(self):
        self.shut();self.path.write_text("{broken",encoding="utf-8");self.open()
        self.assertEqual(self.path.with_name("settings.json.bad").read_text(encoding="utf-8"),"{broken")
    def test_row_click_selects_runs_and_stop_all_covers_remote(self):
        self.service.refresh();self.pump(lambda:not self.service.snapshot()["refreshing"] and bool(self.ui.table.get_children()))
        self.ui._toggle(U1);self.ui.duration.set("30 сек");self.ui.start()
        self.pump(lambda:bool(self.sessions) and self.ui.metrics["active"].get()=="1")
        self.ui.stop_all();self.assertEqual(self.fleet.stops,[None])
        self.pump(lambda:all(s.closed for s in self.sessions) and self.ui.metrics["active"].get()=="0")
    def test_remove_peer_forgets_saved_record(self):
        key="e"*64;self.ui.saved_peers=[dict(fingerprint=key,name="x",url="u",protected_token="AA==")];self.ui.current_peer=key
        self.ui.remove_peer()
        self.assertEqual(self.fleet.removed,[key]);self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["paired"],[])
    def test_remote_power_counts_only_while_connected(self):
        status=dict(active=1,devices={"GPU-x":dict(name="Spark",state="running",watts=100.0,temperature=50,note="")})
        self.fleet.rows={"a":dict(name="Spark",url="u",status=status,error=None,notice=None,updated_at=time.time())}
        self.pump(lambda:self.ui.metrics["power"].get()=="100 Вт" and self.ui.metrics["active"].get()=="1")
        self.fleet.rows["a"]["updated_at"]=time.time()-60
        self.pump(lambda:self.ui.metrics["power"].get()=="—" and self.ui.metrics["active"].get()=="0")


@unittest.skipIf(tk is None,"Tkinter is not available")
class AgentWindowTests(unittest.TestCase):
    def run_until(self,root,condition,timeout=8):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                root.update()
                root.winfo_exists()
            except tk.TclError:return True
            if condition():return True
            time.sleep(.01)
        self.fail("Agent window did not reach expected state")
    def test_buttons_serve_real_https_read_only_and_stop_even_during_start(self):
        with tempfile.TemporaryDirectory(prefix="boris-agent-ui-") as tmp,mock.patch("boris_pearl.agent_ui.ROOT",Path(tmp)):
            root=tk.Tk();root.withdraw();window=AgentWindow(root)
            window.host.set("127.0.0.1");window.start()
            self.run_until(root,lambda:window.pairing is not None and window.serving.is_set())
            status=RemoteClient(window.pairing,timeout=2).request("status")
            self.assertFalse(status["allow_tests"]);self.assertFalse(status["mining_enabled"])
            window.stop();self.run_until(root,lambda:not window.alive())
            # Stop immediately after start: the agent must not keep serving behind a disabled button.
            window.start();window.stop();self.run_until(root,lambda:not window.alive());self.assertFalse(window.serving.is_set())
            window.close();self.run_until(root,lambda:False)
    def test_addresses_end_with_loopback_and_public_input_is_refused(self):
        self.assertEqual(local_addresses()[-1],"127.0.0.1")
        root=tk.Tk();root.withdraw();window=AgentWindow(root)
        for bad in ("8.8.8.8","0.0.0.0","not-an-ip"):
            window.host.set(bad);window.start();self.assertIsNone(window.thread,bad)
        window.close();deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            try:root.update();time.sleep(.01);root.winfo_exists()
            except tk.TclError:return
        self.fail("Agent window did not close")
