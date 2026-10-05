"""Regression scenarios for the missed-pause incident; all accounts/IPC are fixtures."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from activation import configuration_binding, delivery_status, set_mode
from controller import dispatch, run
from native_bridge import NativeBridgeError
from safeguard import advance
from storage import save
from test_controller import ReceiptTransport
from test_guard import ACCOUNT, sample
from test_transport import CONFIG, TransportFixture


class RecipientRecoveryTests(TransportFixture):
    def test_parent_outage_cannot_block_pause_and_recovery_does_not_repeat_foreman(self):
        pipe = self.route('app.sock', {'fixture-parent': NativeBridgeError('native_tool_rejected', stage='read_thread')})
        self.publish(pipe); manager = self.manager(); router = manager.connection()
        self.assertIsNotNone(router.for_recipient('foreman'))
        self.assertIsNone(router.for_recipient('parent'))
        state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
        path = self.root / 'main.json'
        with self.assertRaisesRegex(RuntimeError, 'parent_destination_unavailable'):
            dispatch(state, ACCOUNT, CONFIG, router, path)
        receipts = json.loads(path.read_text())['episode']['events'][0]['deliveries']
        self.assertEqual(set(receipts), {'foreman'})
        self.assertEqual(receipts['foreman']['status'], 'submitted')
        self.assertEqual([call[2] for call in self.calls if call[1] == 'send'], ['fixture-foreman'])
        self.assertFalse(manager.health['healthy'])
        self.assertTrue(manager.health['recipients']['foreman']['healthy'])
        self.registry[pipe] = 'valid'; self.now += 301
        router = manager.connection()
        dispatch(json.loads(path.read_text()), ACCOUNT, CONFIG, router, path)
        self.assertEqual([call[2] for call in self.calls if call[1] == 'send'], ['fixture-foreman', 'fixture-parent'])
        self.assertTrue(manager.health['healthy'])
        manager.close()

    def test_foreman_outage_allows_notification_without_a_pause_delivery_claim(self):
        pipe = self.route('app.sock', {'fixture-foreman': 'deny'}); self.publish(pipe)
        manager = self.manager(); router = manager.connection()
        state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
        path = self.root / 'main.json'
        with self.assertRaisesRegex(RuntimeError, 'foreman_destination_unavailable'):
            dispatch(state, ACCOUNT, CONFIG, router, path)
        receipts = json.loads(path.read_text())['episode']['events'][0]['deliveries']
        self.assertEqual(set(receipts), {'parent'})
        self.assertEqual([call[2] for call in self.calls if call[1] == 'send'], ['fixture-parent'])
        manager.close()

    def test_changed_parent_reuses_caller_route_but_reads_the_new_exact_target(self):
        pipe = self.route('app.sock'); self.publish(pipe)
        manager = self.manager(); manager.connection(); manager.close()
        changed = copy.deepcopy(CONFIG); changed['parentThreadId'] = 'new-parent'
        new = self.manager(changed); new.connection()
        self.assertIsNotNone(new.for_recipient('foreman'))
        self.assertIn((pipe, 'read_thread', 'new-parent'), self.calls)
        self.assertFalse(any(call[1] == 'send' for call in self.calls))
        new.close()

    def test_hourly_reminders_survive_restart_without_fast_restart_spam(self):
        self.publish(self.route('app.sock', 'deny'))
        for elapsed in (0, 30, 3599, 3600, 3610, 7200):
            self.now = 1000 + elapsed
            manager = self.manager()
            with self.assertRaises(RuntimeError): manager.connection()
            self.assertEqual(len(self.alerts), 1 + elapsed // 3600)
        health = json.loads((self.root / 'transport/health.json').read_text())
        self.assertEqual(health['notice']['attempts'], 3)
        self.assertEqual(health['notice']['status'], 'submitted')
        self.assertFalse(any(call[1] == 'send' for call in self.calls))

    def test_old_submitted_alert_and_uncertain_attempt_have_bounded_retry(self):
        manager = self.manager()
        save(self.root / 'transport/health.json', {'incident': 'old', 'startedAt': -4000,
                                                  'notice': {'status': 'submitted'}})
        with self.assertRaises(RuntimeError): manager.connection()
        self.assertEqual(len(self.alerts), 1)
        record = json.loads((self.root / 'transport/health.json').read_text())
        self.assertEqual(record['incident'], 'old')
        record['notice']['status'] = 'attempting'; save(self.root / 'transport/health.json', record)
        self.now += 100
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)
        self.now += 3500
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 2)


class MaintenanceTests(unittest.TestCase):
    def config(self, root):
        config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        config.update(stateDir=str(root), sharedLock=str(root / 'legacy.lock'), enabled=True)
        (root / 'legacy.lock').touch()
        return config

    def test_missing_activation_observes_zero_but_never_dispatches_after_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root)
            def read(account, **kwargs):
                value = sample(100, resets=0)
                if account['name'] == 'daybreak': value.update({k: account[k] for k in ('email', 'accountId')})
                return value
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email','accountId')}), \
                 patch('controller.Transport') as transport:
                for _ in range(2):
                    health = run(config, root, once=True)
                    self.assertEqual(health['delivery']['mode'], 'maintenance')
                    self.assertEqual(health['accounts']['main']['observationStatus'], 'healthy')
                transport.assert_not_called()
            self.assertFalse(json.loads((root / 'main.json').read_text())['episode']['events'][0].get('deliveries'))

    def test_activation_and_persistent_hold_preserve_ledger_and_require_live_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root)
            save(root / 'main.json', {'episode': {'id':'same', 'resetIdempotencyKey':'same-key',
                'events':[{'deliveries':{'foreman':{'status':'outcome_unknown'}}}]}})
            before = (root / 'main.json').read_bytes()
            calls=[]
            result=set_mode(config, root, 'active', 'verified-human-reference',
                            live_preflight=lambda *a, **kw:calls.append(kw))
            self.assertEqual(result['mode'], 'active'); self.assertEqual(calls,[{'check_live':True}])
            set_mode(config, root, 'maintenance', 'operator-maintenance')
            self.assertEqual(delivery_status(config, root)['mode'], 'maintenance')
            def failed(*a, **kw): raise RuntimeError('fixture-route-denial')
            with self.assertRaises(RuntimeError): set_mode(config, root, 'active', 'new-reference', live_preflight=failed)
            self.assertEqual(delivery_status(config, root)['mode'], 'maintenance')
            self.assertEqual((root / 'main.json').read_bytes(), before)

    def test_changed_configuration_and_malformed_record_cannot_implicitly_activate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root)
            set_mode(config, root, 'active', 'verified-reference', live_preflight=lambda *a,**k:None)
            config['parentThreadId']='changed-parent'
            self.assertEqual(delivery_status(config, root)['reason'], 'activation_configuration_changed')
            for malformed in ([], {'mode':'active'}, {'mode':'active','binding':configuration_binding(config)}):
                save(root/'activation.json', malformed)
                self.assertEqual(delivery_status(config, root)['mode'], 'maintenance')
            (root/'activation.json').write_text('not-json')
            self.assertEqual(delivery_status(config, root)['mode'], 'maintenance')

    def test_hold_between_recipients_blocks_new_intent_without_rewinding_first_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path=root/'main.json'; bridge=ReceiptTransport(path)
            state,_=advance({}, sample(100,resets=0), ACCOUNT, now=10)
            answers=iter([True,False])
            with self.assertRaisesRegex(RuntimeError,'maintenance_delivery_inhibited'):
                dispatch(state,ACCOUNT,{'foremanThreadId':'foreman','parentThreadId':'parent'},bridge,path,
                         delivery_guard=lambda:next(answers))
            self.assertEqual([t for t,_ in bridge.received],['foreman'])
            self.assertEqual(set(state['episode']['events'][0]['deliveries']), {'foreman'})


if __name__ == '__main__': unittest.main()
