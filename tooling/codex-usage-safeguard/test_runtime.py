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
from activation import configuration_binding
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
                    transport.return_value.connection.return_value.for_recipient.return_value = transport.return_value.connection.return_value
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
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
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
                    transport.return_value.connection.return_value.for_recipient.return_value = transport.return_value.connection.return_value
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
        save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
        return config

    def fixture_poll(self, root, config, read, bridge, *, now=30, observe_only=False):
        with patch('controller.read_account', side_effect=read), \
             patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
             patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
             patch('controller.time.time', return_value=now):
            transport.return_value.connection.return_value = bridge
            return run(config, root, once=True, observe_only=observe_only)

    def fixture_restored_preflight(self, root, config):
        pinned = config['accounts'][1]
        def restored(account, **kwargs):
            value = sample(50)
            if account['name'] == 'daybreak':
                value.update({k: pinned[k] for k in ('email', 'accountId')})
            return value
        with patch('preflight.read_account', side_effect=restored), \
             patch('preflight.socket_identity'), patch('preflight.NativeBridge') as native, \
             patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': '/fixture/route.sock'}, clear=True):
            native.return_value.call.side_effect = lambda tool, arguments: {
                'thread': {'id': arguments['threadId'], 'title': 'Codex Foreman'}}
            return preflight(config, root, check_live=True)

    def test_failed_read_fallback_does_not_send_unrelated_pending_confirmation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); config = self.config(root); pinned = config['accounts'][1]
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
            unknown = sample(100); unknown.update(email=pinned['email'], accountId=pinned['accountId'])
            unknown['rate_limit_reset_credits'] = {}
            state, _ = advance({}, unknown, pinned, now=10, policy=config['policy'])
            zero = sample(100); zero.update(email=pinned['email'], accountId=pinned['accountId'])
            state, _ = advance(state, zero, pinned, now=20, policy=config['policy'])
            episode = copy.deepcopy(state['episode'])
            save(root / 'daybreak.json', state)
            bridge = ReceiptTransport(root / 'daybreak.json')
            def read(account, **kwargs):
                if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True), \
                 patch('controller.time.time', return_value=30):
                transport.return_value.connection.return_value = bridge
                run(config, root, once=True)
            self.assertEqual([target for target, _ in bridge.received],
                             [config['foremanThreadId'], config['parentThreadId']])
            retained = json.loads((root / 'daybreak.json').read_text())
            unknown_event, confirmation, pause = retained['episode']['events']
            self.assertEqual(pause['reason'], 'observation_failed_during_confirmed_exhaustion')
            self.assertEqual(unknown_event['deliveries']['parent']['coalescedInto'], pause['id'])
            self.assertFalse(confirmation.get('deliveries'))
            self.assertEqual(retained['lastSample'], zero)
            for key in ('id', 'resetIdempotencyKey', 'accountId', 'email', 'resetCount'):
                self.assertEqual(retained['episode'][key], episode[key])

    def test_failed_poll_without_fallback_preserves_backlog_after_restored_preflight(self):
        for check_preflight in (False, True):
            with self.subTest(check_preflight=check_preflight), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                save(root / 'daybreak.json', state)
                before = (root / 'daybreak.json').read_bytes()
                if check_preflight:
                    report = self.fixture_restored_preflight(root, config)
                    self.assertEqual(report['liveChecks'], 'both_accounts_and_exact_destinations_verified')
                    self.assertEqual((root / 'daybreak.json').read_bytes(), before)
                def failed(account, **kwargs):
                    if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                    return sample(0)
                bridge = ReceiptTransport(root / 'daybreak.json')
                for _ in range(2):
                    health = self.fixture_poll(root, config, failed, bridge)
                    self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'unavailable')
                    self.assertEqual((root / 'daybreak.json').read_bytes(), before)
                self.assertEqual(bridge.received, [])

    def test_restored_or_unknown_current_poll_blocks_pending_pause_and_confirmation(self):
        for used, budget in ((50, 'allowance_restored_pending_rearm'), (None, 'quota_unknown_wait_blocked')):
            with self.subTest(used=used), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
                state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
                spent = copy.deepcopy(zero); spent['credits']['balance'] = '790'
                state, _ = advance(state, spent, pinned, now=20, policy=config['policy'])
                episode = copy.deepcopy(state['episode'])
                self.assertEqual([event['kind'] for event in episode['events']],
                                 ['reset_confirmation_required', 'foreman_pause_required'])
                save(root / 'daybreak.json', state)
                current = sample(used); current.update({k: pinned[k] for k in ('email', 'accountId')})
                def read(account, **kwargs):
                    return current if account['name'] == 'daybreak' else sample(0)
                bridge = ReceiptTransport(root / 'daybreak.json')
                health = self.fixture_poll(root, config, read, bridge)
                self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'healthy')
                self.assertIn('current_zero_required_before_delivery', health['accounts']['daybreak']['reason'])
                self.assertEqual(bridge.received, [])
                retained = json.loads((root / 'daybreak.json').read_text())
                self.assertEqual(retained['lastSample'], current)
                self.assertEqual(retained['episode']['budgetStatus'], budget)
                self.assertEqual(retained['episode']['events'], episode['events'])
                self.assertTrue(retained['episode']['active'])
                for key in ('id', 'resetIdempotencyKey', 'accountId', 'email', 'resetCount'):
                    self.assertEqual(retained['episode'][key], episode[key])

    def test_failed_poll_fallback_keeps_recipient_outages_independent_without_backlog_sends(self):
        for unavailable in ('foreman', 'parent'):
            with self.subTest(unavailable=unavailable), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
                unknown = sample(100); unknown.update({k: pinned[k] for k in ('email', 'accountId')})
                unknown['rate_limit_reset_credits'] = {}
                state, _ = advance({}, unknown, pinned, now=10, policy=config['policy'])
                zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
                state, _ = advance(state, zero, pinned, now=20, policy=config['policy'])
                save(root / 'daybreak.json', state)
                bridge = ReceiptTransport(root / 'daybreak.json')
                bridge.for_recipient = lambda key: None if key == unavailable else bridge
                def failed(account, **kwargs):
                    if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                    return sample(0)
                before = (root / 'daybreak.json').read_bytes()
                report = self.fixture_restored_preflight(root, config)
                self.assertEqual(report['liveChecks'], 'both_accounts_and_exact_destinations_verified')
                self.assertEqual((root / 'daybreak.json').read_bytes(), before)
                for _ in range(2):
                    self.fixture_poll(root, config, failed, bridge)
                available = 'parent' if unavailable == 'foreman' else 'foreman'
                self.assertEqual([target for target, _ in bridge.received], [config[available + 'ThreadId']])
                retained = json.loads((root / 'daybreak.json').read_text())
                unknown_event, confirmation, pause = retained['episode']['events']
                self.assertEqual(pause['reason'], 'observation_failed_during_confirmed_exhaustion')
                self.assertEqual(set(pause['deliveries']), {available})
                self.assertEqual(pause['deliveries'][available]['status'], 'submitted')
                self.assertFalse(confirmation.get('deliveries'))
                if available == 'parent':
                    self.assertEqual(unknown_event['deliveries']['parent']['coalescedInto'], pause['id'])
                else:
                    self.assertFalse(unknown_event.get('deliveries'))
                self.assertEqual(retained['lastSample'], zero)

    def test_failed_poll_fallback_preserves_main_receipts_and_generation_across_restart(self):
        for receipt_status in ('submitted', 'attempting', 'outcome_unknown'):
            with self.subTest(receipt_status=receipt_status), tempfile.TemporaryDirectory() as name:
                root = Path(name); config = self.config(root); pinned = config['accounts'][1]
                config['policy'].update(pauseWhenUnmetered=True,
                    fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
                save(root / 'activation.json', {'mode': 'active', 'binding': configuration_binding(config), 'reference': 'fixture-approval'})
                def exhausted_main(account, **kwargs):
                    if account['name'] == 'main': return sample(100)
                    value = sample(0); value.update({k: pinned[k] for k in ('email', 'accountId')})
                    return value
                bridge = ReceiptTransport(root / 'main.json')
                self.fixture_poll(root, config, exhausted_main, bridge, now=10, observe_only=True)
                state = json.loads((root / 'main.json').read_text())
                receipt = {'status': receipt_status, 'target': config['parentThreadId'], 'at': 11}
                state['episode']['events'][0]['deliveries'] = {'parent': receipt}
                save(root / 'main.json', state)
                episode = copy.deepcopy(state['episode'])
                generation = state['identityGeneration']
                selection = (root / 'main-selection.json').read_bytes()
                def failed_main(account, **kwargs):
                    if account['name'] == 'main': raise RuntimeError('fixture_quota_read_failed')
                    return exhausted_main(account, **kwargs)
                for _ in range(2):
                    self.fixture_poll(root, config, failed_main, bridge, now=20)
                self.fixture_poll(root, config, exhausted_main, bridge, now=30)
                self.assertEqual([target for target, _ in bridge.received],
                                 [config['foremanThreadId'], config['parentThreadId']])
                retained = json.loads((root / 'main.json').read_text())
                confirmation, pause = retained['episode']['events']
                self.assertEqual(confirmation['deliveries']['parent'], receipt)
                self.assertEqual(pause['reason'], 'observation_failed_during_confirmed_exhaustion')
                self.assertEqual({key: delivery['status'] for key, delivery in pause['deliveries'].items()},
                                 {'foreman': 'submitted', 'parent': 'submitted'})
                self.assertEqual(retained['lastSample'], sample(100))
                self.assertEqual(retained['identityGeneration'], generation)
                self.assertEqual((root / 'main-selection.json').read_bytes(), selection)
                for key in ('id', 'resetIdempotencyKey', 'accountId', 'email', 'resetCount'):
                    self.assertEqual(retained['episode'][key], episode[key])

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
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, failing, path, current_observation=sample(100))
            replacement = ReceiptTransport(path)
            with self.assertRaises(RuntimeError):
                dispatch(json.loads(path.read_text()), ACCOUNT, CONFIG, replacement, path, current_observation=sample(100))
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
            dispatch(state, ACCOUNT, CONFIG, transport, path, current_observation=sample(100))
            state, events = advance(state, sample(100, resets=1), ACCOUNT, now=20)
            self.assertEqual([e['kind'] for e in events], ['reset_confirmation_required'])
            dispatch(state, ACCOUNT, CONFIG, transport, path, current_observation=sample(100))
            self.assertEqual([target for target, _ in transport.received], ['foreman', 'parent', 'parent'])

    def test_new_pause_does_not_overwrite_uncertain_confirmation_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'main.json'
            policy = {'usdPerCredit': '0.04', 'pauseAtUsd': '8'}
            state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
            with self.assertRaises(RuntimeError):
                dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path, fail='parent'), path, current_observation=sample(100))
            old_receipt = copy.deepcopy(state['episode']['events'][0]['deliveries']['parent'])
            state, _ = advance(state, sample(100, balance='800'), ACCOUNT, now=20, policy=policy)
            with self.assertRaises(RuntimeError): dispatch(state, ACCOUNT, CONFIG, ReceiptTransport(path), path, current_observation=sample(100))
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
