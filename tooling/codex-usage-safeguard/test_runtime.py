"""Public runtime boundaries; every account and native route is a fixture."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
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
    def test_main_receipt_reconciliation_does_not_starve_daybreak_delivery(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            def read(account, **kwargs):
                if account['name'] == 'main': return sample(100)
                value = sample(100); value.update(email=pinned['email'], accountId=pinned['accountId'])
                return value
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch.dict(os.environ, {}, clear=True), patch('controller.time.time', return_value=20):
                run(config, root, once=True, observe_only=True)
                state = json.loads((root / 'main.json').read_text())
                state['episode']['events'][0]['deliveries'] = {'parent': {'status': 'outcome_unknown', 'target': 'parent'}}
                save(root / 'main.json', state)
                with patch('controller.Transport') as transport:
                    health = run(config, root, once=True)
                    bridge = transport.return_value.connection.return_value
                    bridge.send.assert_called_once()
                    self.assertIn('for daybreak account', bridge.send.call_args.args[1])
                    transport.return_value.close.assert_called_once()
                self.assertEqual(health['accounts']['main']['status'], 'degraded')
                self.assertEqual(health['accounts']['daybreak']['status'], 'healthy')

    def test_rearm_cli_accepts_unsent_invalidated_pause_without_false_hold_claim(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            state['episode']['events'][0]['invalidated'] = {'reason': 'daybreak_no_longer_main'}
            save(root / 'daybreak.json', state)
            command = [sys.executable, '-B', str(Path(__file__).with_name('request_rearm.py')),
                       '--state-dir', str(root), '--account', 'daybreak', '--episode', state['episode']['id'],
                       '--evidence-reference', 'fixture-restored-quota-evidence']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads((root / 'release-daybreak.json').read_text())['holdReleased'])
            state['episode']['events'][0]['deliveries'] = {'foreman': {'status': 'attempting'}}
            save(root / 'daybreak.json', state)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_only_quota_read_failure_can_start_observation_fallback(self):
        for failure in ('transport', 'delivery', 'read'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root)
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                pinned = config['accounts'][1]
                value = sample(100); value.update(email=pinned['email'], accountId=pinned['accountId'])
                state, _ = advance({}, value, pinned, now=10, policy=config['policy'])
                save(root / 'daybreak.json', state)
                def read(account, **kwargs):
                    if account['name'] == 'daybreak':
                        if failure == 'read': raise RuntimeError('fixture_quota_read_failed')
                        return value
                    return sample(0)
                with patch('controller.read_account', side_effect=read), \
                     patch('controller.default_identity', return_value={'email': ACCOUNT['email'], 'accountId': ACCOUNT['accountId']}), \
                     patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                     patch('controller.time.time', return_value=20):
                    if failure == 'transport':
                        transport.return_value.connection.side_effect = RuntimeError('fixture_transport_down')
                    elif failure == 'delivery':
                        transport.return_value.connection.return_value.send.side_effect = TimeoutError('fixture_timeout')
                    run(config, root, once=True)
                events = json.loads((root / 'daybreak.json').read_text())['episode']['events']
                fallbacks = [e for e in events if e.get('reason') == 'observation_failed_during_confirmed_exhaustion']
                self.assertEqual(len(fallbacks), 1 if failure == 'read' else 0)

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

    def test_companion_survives_bad_frames_and_initialization_without_publishing(self):
        outgoing = io.StringIO()
        requests = '\nnot-json\n[]\n42\nnull\n' + '\n'.join(json.dumps(r) for r in [
            {'id': 1, 'method': 'initialize'},
            {'id': 2, 'method': 'initialize', 'params': None},
            {'id': 3, 'method': 'ping'},
            {'id': 4, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
            {'id': 5, 'method': 'tools/list'}])
        with patch('companion.publish_candidate') as publish:
            serve({'enabled': True}, '/unused-fixture', io.StringIO(requests), outgoing, {})
            publish.assert_called_once()
        replies = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4, 5])
        self.assertEqual([r['error']['code'] for r in replies[:2]], [-32602, -32602])
        self.assertEqual(replies[-1]['result'], {'tools': []})

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

    def test_main_unknown_applicability_does_not_start_confirmation_wait_budget(self):
        policy = {'usdPerCredit': '0.04', 'pauseAtUsd': '8'}
        value = sample(100); value['rate_limit_reset_credits'] = {}
        state, _ = advance({}, value, ACCOUNT, now=10, policy=policy)
        value['credits']['balance'] = '500'
        state, events = advance(state, value, ACCOUNT, now=20, policy=policy)
        self.assertEqual(events, [])
        self.assertNotIn('waitingSpendUsd', state['episode'])

    def test_reset_arriving_after_submitted_no_reset_pause_requests_confirmation(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            dispatch(state, ACCOUNT, CONFIG, transport, path)
            state, events = advance(state, sample(100, resets=1), ACCOUNT, now=20)
            self.assertEqual([e['kind'] for e in events], ['reset_confirmation_required'])
            dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual([target for target, _ in transport.received], ['foreman', 'parent', 'parent'])

    def test_new_pause_does_not_overwrite_uncertain_confirmation_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'
            policy = {'usdPerCredit': '0.04', 'pauseAtUsd': '8'}
            state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
            with self.assertRaises(RuntimeError):
                dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path, fail='parent'), path)
            old_receipt = copy.deepcopy(state['episode']['events'][0]['deliveries']['parent'])
            state, _ = advance(state, sample(100, balance='800'), ACCOUNT, now=20, policy=policy)
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path), path)
            self.assertEqual(state['episode']['events'][0]['deliveries']['parent'], old_receipt)

    def test_daybreak_no_reset_outage_cannot_start_unapproved_wait(self):
        from safeguard import observation_failed
        state, _ = advance({}, sample(100, resets=0), dict(ACCOUNT, name='daybreak'), now=10)
        _, events = observation_failed(state, {'pauseWhenUnmetered': True})
        self.assertEqual(events, [])

    def test_preflight_config_rejects_missing_state_without_creating_it(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['stateDir'] = str(root / 'missing'); path = root / 'config.json'; save(path, config)
            with self.assertRaises((ValueError, FileNotFoundError)): load_config(path, root / 'missing')
            self.assertFalse((root / 'missing').exists())

    def test_config_rejects_state_anywhere_inside_release_archive(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root)
            config['stateDir'] = str(Path(__file__).resolve().parents[2] / 'private-state')
            path = root / 'config.json'; save(path, config)
            with self.assertRaisesRegex(ValueError, 'outside_release'):
                load_config(path, config['stateDir'])

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
