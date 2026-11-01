# SPDX-License-Identifier: Apache-2.0
"""No network: the answer of the releases page is faked."""
import io
import json
import unittest
from unittest import mock

from boris_pearl import updates


class UpdateTests(unittest.TestCase):
    def reply(self,payload):
        answer=mock.MagicMock();answer.__enter__.return_value=io.BytesIO(json.dumps(payload).encode());return answer
    def test_only_a_really_newer_tag_is_news(self):
        self.assertTrue(updates.newer_than_this("v0.10.0","0.5.0"));self.assertFalse(updates.newer_than_this("v0.5.0","0.5.0"));self.assertFalse(updates.newer_than_this("v0.4.9","0.5.0"))
        with mock.patch("urllib.request.urlopen",return_value=self.reply(dict(tag_name="v99.0.0"))):self.assertEqual(updates.check(),"v99.0.0")
        with mock.patch("urllib.request.urlopen",return_value=self.reply(dict(tag_name="v0.0.1"))):self.assertIsNone(updates.check())
        with mock.patch("urllib.request.urlopen",return_value=self.reply(dict(message="Not Found"))):self.assertIsNone(updates.check())
    def test_no_network_is_no_news(self):
        with mock.patch("urllib.request.urlopen",side_effect=OSError("down")):self.assertIsNone(updates.check())


if __name__=="__main__":
    unittest.main()
