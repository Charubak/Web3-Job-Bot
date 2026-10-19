import json
import unittest
from unittest.mock import MagicMock, patch
import deployment_readiness as readiness


class ReadinessTests(unittest.TestCase):
    def inspect(self, payload, status=200):
        response = MagicMock()
        response.status = status
        response.read.return_value = json.dumps(payload).encode()
        with patch.object(readiness, "urlopen") as request:
            request.return_value.__enter__.return_value = response
            result = readiness.latest_delivery_cache()
            headers = dict(request.call_args.args[0].header_items())
            self.assertNotIn("Authorization", headers)
            return result

    def test_confirmed_empty_inventory_permits_initialization(self):
        self.assertIsNone(self.inspect({"total_count": 0, "actions_caches": []}))

    def test_existing_history_uses_exact_newest_key(self):
        key = "job-alert-state-v1-123-1-prepared"
        self.assertEqual(key, self.inspect({"total_count": 3, "actions_caches": [{"key": key, "ref": readiness.REF}]}))

    def test_service_and_network_failures_do_not_mean_empty(self):
        with self.assertRaises(RuntimeError):
            self.inspect({"total_count": 0, "actions_caches": []}, status=503)
        with patch.object(readiness, "urlopen", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                readiness.latest_delivery_cache()

    def test_malformed_or_conflicting_inventory_is_refused(self):
        for payload in [{}, {"total_count": 1, "actions_caches": []},
                        {"total_count": False, "actions_caches": []},
                        {"total_count": 0, "actions_caches": [{}]},
                        {"total_count": 1, "actions_caches": [{"key": "other", "ref": readiness.REF}]},
                        {"total_count": 1, "actions_caches": [{"key": readiness.PREFIX + "x", "ref": "refs/heads/other"}]}]:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.inspect(payload)

    def test_oversized_response_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.inspect({"padding": "x" * 65_536})

    def test_output_injection_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.inspect({"total_count": 1, "actions_caches": [{"key": readiness.PREFIX + "x\nexists=false", "ref": readiness.REF}]})
