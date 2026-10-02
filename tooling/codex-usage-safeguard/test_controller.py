import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from controller import dispatch, validate_config, select_main_identity, run
from safeguard import advance
from test_guard import ACCOUNT, sample

CONFIG = {'foremanThreadId': 'foreman', 'parentThreadId': 'parent'}


class ReceiptTransport:
    """External transport fixture observes the persisted send intent."""
    def __init__(self, path, fail=None):
        self.path = path; self.fail = fail; self.received = []

    def send(self, target, prompt):
        state = json.loads(self.path.read_text())
        attempts = [d for e in state['episode']['events'] for d in e.get('deliveries', {}).values()]
        if not any(d['target'] == target and d['status'] == 'attempting' for d in attempts):
            raise AssertionError('Send intent not durable before transport use')
        self.received.append((target, prompt))
        if target == self.fail:
            raise TimeoutError('External send outcome unavailable')


class DeliveryTests(unittest.TestCase):
    def test_submitted_daybreak_pause_is_invalidated_on_switch_and_new_condition_gets_new_id(self):
        account = dict(ACCOUNT, name='daybreak', alsoCurrentMain=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daybreak.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100, resets=0), account, now=10)
            with patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('accountId', 'email')}):
                dispatch(state, account, CONFIG, transport, path)
            old_receipts = copy.deepcopy(state['episode']['events'][0]['deliveries'])
            with patch('controller.default_identity', return_value={}):
                dispatch(state, account, CONFIG, transport, path)
            old = state['episode']['events'][0]
            self.assertTrue(old['invalidated'])
            self.assertEqual(old['deliveries'], old_receipts)
            self.assertEqual(len(transport.received), 2)
            state, events = advance(state, sample(100, resets=0), account, now=20)
            self.assertNotEqual(events[0]['id'], old['id'])

    def test_identical_destinations_cannot_activate(self):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        config['parentThreadId'] = config['foremanThreadId']
        with self.assertRaisesRegex(ValueError, 'distinct_notification_destinations_required'):
            validate_config(config)

    def test_combined_timeout_preserves_uncertainty_without_second_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.json'; transport = ReceiptTransport(path, fail='parent')
            state, _ = advance({}, sample(100), ACCOUNT, now=10, policy={'pauseWhenUnmetered': True})
            with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
                dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual([t for t, _ in transport.received], ['foreman', 'parent'])
            recovered = json.loads(path.read_text())
            confirmation, pause = recovered['episode']['events']
            receipt = confirmation['deliveries']['parent']
            self.assertEqual(receipt['status'], 'outcome_unknown')
            self.assertEqual(receipt['coalescedInto'], pause['id'])
            with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
                dispatch(recovered, ACCOUNT, CONFIG, transport, path)
            self.assertEqual(len(transport.received), 2)

    def test_combined_attempting_receipt_survives_restart_and_destination_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100), ACCOUNT, now=10, policy={'pauseWhenUnmetered': True})
            confirmation, pause = state['episode']['events']
            pause['deliveries'] = {
                'foreman': {'status': 'submitted', 'target': 'foreman'},
                'parent': {'status': 'attempting', 'target': 'old-parent', 'includesEvent': confirmation['id']}}
            with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
                dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual(transport.received, [])
            self.assertEqual(confirmation['deliveries']['parent']['status'], 'attempting')
            self.assertEqual(confirmation['deliveries']['parent']['target'], 'old-parent')

    def test_stale_daybreak_pause_is_invalidated_and_never_replayed(self):
        account = dict(ACCOUNT, name='daybreak', alsoCurrentMain=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daybreak.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100, resets=0), account, now=10)
            old = state['episode']['events'][0]
            with patch('controller.default_identity', return_value={'accountId': 'different', 'email': 'different'}):
                dispatch(state, account, CONFIG, transport, path)
            self.assertEqual(old['invalidated']['reason'], 'daybreak_no_longer_main')
            recovered = json.loads(path.read_text())
            with patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('accountId', 'email')}):
                dispatch(recovered, account, CONFIG, transport, path)
            self.assertEqual(transport.received, [])
            # A fresh waiting-budget condition remains effective and uses a new ID.
            state, _ = advance(recovered, sample(100), dict(account, alsoCurrentMain=False), now=20,
                               policy={'usdPerCredit': '0.04'})
            state, events = advance(state, sample(100, balance='800'), dict(account, alsoCurrentMain=False),
                                    now=30, policy={'usdPerCredit': '0.04'})
            self.assertEqual(events[0]['reason'], 'waiting_spend_threshold')
            self.assertNotEqual(events[0]['id'], old['id'])

    def test_daybreak_switch_between_recipients_preserves_submitted_receipt(self):
        account = dict(ACCOUNT, name='daybreak', alsoCurrentMain=True)
        identity = {k: ACCOUNT[k] for k in ('accountId', 'email')}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daybreak.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100, resets=0), account, now=10)
            with patch('controller.default_identity', side_effect=[identity, {}]):
                dispatch(state, account, CONFIG, transport, path)
            self.assertEqual([t for t, _ in transport.received], ['foreman'])
            pause = state['episode']['events'][0]
            self.assertEqual(pause['deliveries']['foreman']['status'], 'submitted')
            self.assertNotIn('parent', pause['deliveries'])
            self.assertIn('invalidated', pause)

    def test_main_on_daybreak_without_reset_keeps_main_pause_rule_once(self):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        pinned = config['accounts'][1]
        reading = sample(100, resets=0)
        identity = {'email': pinned['email'], 'accountId': pinned['accountId']}
        reading.update(identity)
        with tempfile.TemporaryDirectory() as directory, patch('controller.read_account', return_value=reading), \
             patch('controller.default_identity', return_value=identity):
            root = Path(directory)
            health = run(config, root, once=True, observe_only=True)
            self.assertEqual(health['accounts']['main']['status'], 'covered_by_daybreak')
            events = json.loads((root / 'daybreak.json').read_text())['episode']['events']
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['reason'], 'no_applicable_reset')

    def test_main_on_daybreak_uses_one_canonical_episode(self):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        pinned = next(a for a in config['accounts'] if a['name'] == 'daybreak')
        reading = sample(100); reading.update(email=pinned['email'], accountId=pinned['accountId'])
        with tempfile.TemporaryDirectory() as directory, patch('controller.read_account', return_value=reading):
            root = Path(directory)
            health = run(config, root, once=True, observe_only=True)
            self.assertEqual(health['accounts']['main']['status'], 'covered_by_daybreak')
            self.assertNotIn('episode', json.loads((root / 'main.json').read_text()))
            self.assertTrue(json.loads((root / 'daybreak.json').read_text())['episode']['active'])

    def test_main_transition_isolates_episodes_approvals_and_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, _ = advance({}, sample(100), ACCOUNT, now=10,
                             policy={'usdPerCredit': '0.04'})
            old['identityGeneration'] = 'old-generation'
            from controller import save
            save(root / 'main.json', old)
            save(root / 'main-selection.json', {'accountId': ACCOUNT['accountId'], 'email': ACCOUNT['email'],
                                               'generation': 'old-generation'})
            new_sample = sample(0); new_sample.update(email='second@example.test', accountId='second-id')
            account, state = select_main_identity(root, {'name': 'main', 'dynamicDefault': True}, new_sample)
            self.assertEqual(account['accountId'], 'second-id')
            self.assertNotIn('episode', state)
            self.assertNotIn('lastSample', state)
            self.assertNotEqual(state['identityGeneration'], 'old-generation')
            archived = list((root / 'account-history').glob('*.json'))
            self.assertEqual(len(archived), 1)
            saved = json.loads(archived[0].read_text())
            self.assertEqual(saved['episode']['resetIdempotencyKey'], old['episode']['resetIdempotencyKey'])
            # Switching back must not revive the prior pending approval/episode.
            again_account, again = select_main_identity(root, {'name': 'main', 'dynamicDefault': True}, sample(0))
            self.assertNotIn('episode', again)
            self.assertNotEqual(again['identityGeneration'], 'old-generation')

    def test_foreman_owns_pause_and_restart_does_not_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual([t for t, _ in transport.received], ['foreman', 'parent'])
            self.assertIn('Steer', transport.received[0][1])
            self.assertIn('sole pause coordinator', transport.received[1][1])
            recovered = json.loads(path.read_text())
            dispatch(recovered, ACCOUNT, CONFIG, transport, path)
            self.assertEqual(len(transport.received), 2)

    def test_uncertain_foreman_delivery_notifies_parent_but_never_retries_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.json'; transport = ReceiptTransport(path, fail='foreman')
            state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
            with self.assertRaises(RuntimeError):
                dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual([t for t, _ in transport.received], ['foreman', 'parent'])
            recovered = json.loads(path.read_text())
            with self.assertRaises(RuntimeError):
                dispatch(recovered, ACCOUNT, CONFIG, transport, path)
            self.assertEqual(len(transport.received), 2)

    def test_confirmations_only_go_to_parent_and_never_redeem(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daybreak.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100), ACCOUNT, now=10)
            dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual([t for t, _ in transport.received], ['parent'])
            self.assertIn('explicit human confirmation', transport.received[0][1])
            self.assertIn(state['episode']['resetIdempotencyKey'], transport.received[0][1])

    def test_pending_policy_cannot_activate(self):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        config['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'activation_policy_not_approved'):
            validate_config(config, activate=True)
        validate_config(config, activate=False)

    def test_verified_meter_can_activate_without_unapproved_outage_fallback(self):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        config['enabled'] = True
        config['policy'] = {'pauseWhenUnmetered': False, 'usdPerCredit': '0.04',
                            'pauseAtUsd': '8', 'usdRateEvidence': 'official-rate-reference'}
        validate_config(config, activate=True)
        config['policy']['pauseWhenUnmetered'] = True
        with self.assertRaisesRegex(ValueError, 'unmetered_fallback_not_approved'):
            validate_config(config, activate=True)

    def test_simultaneous_pause_and_confirmation_notify_parent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.json'; transport = ReceiptTransport(path)
            state, _ = advance({}, sample(100), ACCOUNT, now=10, policy={'pauseWhenUnmetered': True})
            dispatch(state, ACCOUNT, CONFIG, transport, path)
            self.assertEqual([t for t, _ in transport.received], ['foreman', 'parent'])

if __name__ == '__main__':
    unittest.main()
