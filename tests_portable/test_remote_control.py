# SPDX-License-Identifier: Apache-2.0
"""Loopback HTTPS, synthetic services/keys only; no real remote device or GPU."""
from dataclasses import replace
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from boris_pearl.remote_control import Pairing,RemoteClient,ControlState,initialize,make_server
from boris_pearl.fleet import Fleet
from boris_pearl.pairing_store import seal,unseal
from tests_portable.test_devices import U1


class FakeService:
    def __init__(self):self.starts=0;self.stops=0;self.active=0;self.commands=0;self.logs=[]
    def snapshot(self):self.commands+=1;return dict(devices={},active=self.active,mining_enabled=False)
    def refresh(self):return True
    def start(self,devices,**kwargs):self.starts+=1;self.active=1
    def stop(self):self.stops+=1;self.active=0
    def log(self,message):self.logs.append(message)
    def close(self):self.stop()


class RemoteControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="boris-agent-test-")
        self.folder=Path(self.tmp.name)
        self.pairing=initialize(self.folder,"loopback fixture","https://127.0.0.1:8431")
        self.service=FakeService();self.state=ControlState(self.service,self.pairing.token,allow_tests=True,lease_seconds=.25)
        self.server=make_server(("127.0.0.1",0),self.folder/"agent-cert.pem",self.folder/"agent-key.pem",self.state)
        self.pairing=replace(self.pairing,url=f"https://127.0.0.1:{self.server.server_port}")
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs=dict(poll_interval=.02),daemon=False);self.thread.start()
        self.client=RemoteClient(self.pairing,timeout=1)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.state.close();self.tmp.cleanup()
    def body(self):return dict(request_id="a"*32,sequence=1,devices=[U1],seconds=5,duty=.2,profile="standard",backend="auto")
    def test_tls_status_and_token_not_exposed(self):
        reply=self.client.request("status");self.assertFalse(reply["mining_enabled"])
        self.assertNotIn(self.pairing.token,json.dumps(reply))
    def test_wrong_pin_refuses_before_http_credentials_are_sent(self):
        pin=("0" if self.pairing.fingerprint[0]!="0" else "1")+self.pairing.fingerprint[1:]
        with self.assertRaisesRegex(ValueError,"Сертификат"):RemoteClient(replace(self.pairing,fingerprint=pin),timeout=1).request("status")
        self.assertEqual(self.service.commands,0)
    def test_wrong_token_denied(self):
        with self.assertRaisesRegex(ValueError,"код"):RemoteClient(replace(self.pairing,token="b"*64),timeout=1).request("status")
        self.assertEqual(self.service.commands,0)
    def test_replay_and_changed_request_cannot_restart_or_change_work(self):
        body=self.body();self.assertTrue(self.client.request("start",body)["started"])
        self.assertTrue(self.client.request("start",body)["replayed"]);self.assertEqual(self.service.starts,1)
        changed=dict(body,seconds=6)
        with self.assertRaises(ValueError):self.client.request("start",changed)
        self.assertEqual(self.service.starts,1)
        self.client.request("stop",dict(sequence=2));self.client.request("start",body)
        self.assertEqual(self.service.starts,1);self.assertEqual(self.service.active,0)
    def test_delayed_start_cannot_override_a_later_stop(self):
        self.client.request("stop",dict(sequence=2))
        with self.assertRaisesRegex(ValueError,"Stale"):self.client.request("start",self.body())
        self.assertEqual(self.service.starts,0)
        self.client.request("start",dict(self.body(),sequence=3,request_id="c"*32))
        self.client.request("stop",dict(sequence=2))
        self.assertEqual(self.service.active,1)
        self.client.request("stop",dict(sequence=4))
        self.assertEqual(self.service.active,0)
    def test_disconnect_lease_stops_without_another_request(self):
        self.client.request("start",self.body())
        deadline=time.monotonic()+1
        while self.service.active and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.service.active,0);self.assertGreater(self.service.stops,0)
    def test_finished_test_is_not_reported_as_lost_connection(self):
        self.client.request("start",self.body());self.service.active=0;stops=self.service.stops
        time.sleep(.4)
        self.assertEqual(self.service.stops,stops);self.assertEqual(self.service.logs,[])
    def test_refresh_during_a_test_is_not_an_error_and_lease_keeps_it_alive(self):
        self.client.request("start",self.body())
        self.assertFalse(self.client.request("refresh",{})["queued"])
        deadline=time.monotonic()+.6
        while time.monotonic()<deadline:self.client.request("lease",{});time.sleep(.05)
        self.assertEqual(self.service.active,1)
    def peer(self):
        fleet=Fleet();self.addCleanup(lambda:self.settle(fleet));key=fleet.connect(self.pairing)
        self.wait(lambda:fleet.snapshot()[key]["status"] is not None);return fleet,key
    def wait(self,condition,timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if condition():return
            time.sleep(.01)
        self.fail("Expected state was not reached")
    def settle(self,fleet):
        fleet.close();self.wait(lambda:not fleet.alive(),8)
    def test_fleet_stop_wins_over_another_panels_counter(self):
        fleet,key=self.peer();self.state.sequence=100;self.service.active=1
        fleet.stop(key)
        self.wait(lambda:self.service.active==0);self.assertGreater(self.state.sequence,100)
    def test_fleet_stop_is_not_dropped_while_the_peer_is_busy(self):
        fleet,key=self.peer();row=fleet.peers[key];release=threading.Event();sent=[]
        class Slow:
            def request(_,path,body=None):
                if path in ("refresh",):release.wait(3)
                if path=="stop":sent.append(body)
                return dict(active=0,sequence=0)
        row["client"]=Slow();row["send_lock"]=threading.Lock()
        fleet.command(key,"refresh",{});fleet.command(key,"refresh",{})
        with self.assertRaises(ValueError):fleet.command(key,"refresh",{})
        fleet.stop(key);release.set()
        self.wait(lambda:bool(sent));self.assertIsNone(fleet.snapshot()[key]["notice"])
    def test_fleet_remove_and_close_send_a_final_stop(self):
        fleet,key=self.peer();fleet.start(key,[U1],seconds=5,duty=.2,profile="standard")
        self.wait(lambda:self.service.active==1)
        fleet.remove(key);self.assertEqual(fleet.snapshot(),{})
        self.wait(lambda:self.service.active==0)
        self.wait(lambda:not fleet.alive(),8)
    def test_stale_panel_start_after_stop_shows_a_human_notice(self):
        fleet,key=self.peer();self.state.sequence=100;fleet.peers[key]["sequence"]=1
        fleet.command(key,"start",dict(request_id="d"*32,devices=[U1],seconds=5,duty=.2,profile="standard",backend="auto"))
        self.wait(lambda:bool(fleet.snapshot()[key]["notice"]))
        self.assertIn("отменён",fleet.snapshot()[key]["notice"]);self.assertEqual(self.service.starts,0)
    def test_token_never_appears_in_repr_or_bad_code_message(self):
        self.assertNotIn(self.pairing.token,repr(self.pairing))
        for text in ("не json","[1]",'{"name":"x"}'):
            with self.assertRaisesRegex(ValueError,"код"):Pairing.parse(text)
    def test_default_readonly_policy_and_unsafe_inputs_refused(self):
        self.state.allow_tests=False
        with self.assertRaises(ValueError):self.client.request("start",self.body())
        self.state.allow_tests=True
        for change in (dict(seconds=3600),dict(duty=1),dict(profile="laptop",duty=.2),dict(devices=[U1,U1]),dict(devices=["0"]),dict(backend="live")):
            with self.assertRaises(ValueError):self.client.request("start",dict(self.body(),**change))
        self.assertEqual(self.service.starts,0)
    def test_pairing_validation_and_existing_keys_are_not_overwritten(self):
        with self.assertRaises(ValueError):replace(self.pairing,url="http://127.0.0.1")
        with self.assertRaises(ValueError):replace(self.pairing,url="https://name:password@host/")
        with self.assertRaises(ValueError):initialize(self.folder,"fixture","https://127.0.0.1:8431")
    @unittest.skipUnless(sys.platform=="win32","DPAPI is a Windows-only optional store")
    def test_windows_remembered_token_is_user_protected(self):
        record=seal(self.pairing);self.assertNotIn(self.pairing.token,json.dumps(record))
        self.assertEqual(unseal(record),self.pairing)
