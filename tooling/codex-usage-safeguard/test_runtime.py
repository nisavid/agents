"""Public runtime boundaries; every account and native route is a fixture."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion import serve
from configuration import load_config
from controller import run, dispatch
from safeguard import advance
from status import status
from preflight import preflight
from storage import save
from test_guard import ACCOUNT, sample
from test_controller import CONFIG, ReceiptTransport


class RuntimeTests(unittest.TestCase):
    def config(self, root):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        config.update(stateDir=str(root), sharedLock=str(root / 'legacy.lock'), enabled=True)
        (root / 'legacy.lock').touch()
        return config

    def test_companion_has_no_action_tools_and_never_fabricates_a_route(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            requests = [{'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
                        {'id': 2, 'method': 'tools/list'},
                        {'id': 3, 'method': 'tools/call', 'params': {'name': 'send_message_to_thread'}}]
            incoming = io.StringIO(''.join(json.dumps(r) + '\n' for r in requests)); outgoing = io.StringIO()
            serve(config, root, incoming, outgoing, {})
            output = [json.loads(line) for line in outgoing.getvalue().splitlines()]
            self.assertEqual(output[1]['result']['tools'], [])
            self.assertIn('error', output[2])
            self.assertFalse((root / 'transport').exists())

    def test_config_binds_existing_paths_and_rejects_unprotected_registration(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); path = root / 'config.json'
            save(path, config)
            self.assertEqual(load_config(path, root)['nativeContext'], config['nativeContext'])
            with self.assertRaisesRegex(ValueError, 'directory_mismatch'): load_config(path, root / 'new')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private_owned_file'): load_config(path, root)

    def test_transport_outage_still_records_zero_and_budget_without_fake_receipts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            pinned = config['accounts'][1]
            def read(account, **kwargs):
                value = sample(100, resets=1)
                if account['name'] == 'daybreak': value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                transport.return_value.connection.side_effect = RuntimeError('fixture_transport_down')
                health = run(config, root, once=True)
                self.assertEqual(health['transport'], 'unavailable')
                for account in ('main', 'daybreak'):
                    episode = json.loads((root / f'{account}.json').read_text())['episode']
                    self.assertEqual(episode['budgetStatus'], 'estimated_from_balance_debits')
                    self.assertNotIn('deliveries', episode['events'][0])

    def test_reconnected_transport_never_replays_unknown_delivery(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            failing = ReceiptTransport(path, fail='foreman')
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, failing, path)
            replacement = ReceiptTransport(path)
            with self.assertRaises(RuntimeError):
                dispatch(json.loads(path.read_text()), ACCOUNT, CONFIG, replacement, path)
            self.assertEqual(replacement.received, [])

    def test_total_reset_inventory_is_not_applicability_evidence(self):
        value = sample(100)
        value['rate_limit_reset_credits'] = {'available_count': 2}
        state, events = advance({}, value, ACCOUNT, now=10)
        self.assertIsNone(state['episode']['resetCount'])
        self.assertEqual(events[0]['kind'], 'reset_availability_unknown')

    def test_status_is_read_only_and_distinguishes_stale_health(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); path = root / 'config.json'; save(path, config)
            save(root / 'health.json', {'observedAt': 'fixture', 'accounts': {}})
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            self.assertFalse(status(path, root, now=(root / 'health.json').stat().st_mtime + 121)['observerFresh'])
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_release_preflight_preserves_active_ledger_and_unknown_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            state['episode']['events'][0]['deliveries'] = {'foreman': {'status': 'outcome_unknown'}}
            save(root / 'main.json', state)
            (root / 'unrelated-observation.json').write_text('{}')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            report = preflight(config, root)
            self.assertEqual(report['liveChecks'], 'not_requested')
            self.assertEqual(set(report['preservedLedgerHashes']), {'main.json'})
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)


if __name__ == '__main__': unittest.main()
