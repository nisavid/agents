"""Regression scenarios for the missed-pause incident; all accounts/IPC are fixtures."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from activation import configuration_binding, delivery_status, set_mode, decision_lock
from controller import dispatch, run
from native_bridge import NativeBridgeError
from safeguard import advance
from storage import save
from test_controller import ReceiptTransport
from test_guard import ACCOUNT, sample
from test_transport import CONFIG, TransportFixture


class RecipientRecoveryTests(TransportFixture):
    def test_slow_failed_poll_keeps_route_budget_and_recovers_without_replay(self):
        config = MaintenanceTests().config(self.root)
        config.update(copy.deepcopy(CONFIG))
        config['policy'].update(pauseWhenUnmetered=True,
            fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
        pinned = config['accounts'][1]
        zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
        state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
        save(self.root / 'daybreak.json', state)
        save(self.root / 'activation.json', {'mode': 'active',
            'binding': configuration_binding(config), 'reference': 'fixture-approval'})
        routes = []
        for index in range(4):
            self.now += 1
            route = self.route(f'route-{index}.sock', {'fixture-parent': 'deny'})
            self.publish(route, config); routes.append(route)
        checked = threading.Event()
        def notice(message):
            self.alerts.append(message); checked.set()
        manager = self.manager(config, notifier=notice)
        polls = []
        def read(account, **kwargs):
            if account['name'] == 'main': return sample(0)
            if polls: return zero
            if not checked.wait(2): raise AssertionError('fixture route checks did not complete')
            self.now += 6  # The initial five-second transport retry delay has elapsed.
            raise RuntimeError('fixture_quota_read_failed')
        class PollsComplete(Exception):
            pass
        def next_poll(_):
            polls.append({'calls': list(self.calls),
                          'state': json.loads((self.root / 'daybreak.json').read_text())})
            if len(polls) == 3: raise PollsComplete
            self.registry[routes[1]] = 'valid'
            self.now += 1
        # Keep the production controller and transport; only account/IPC, time,
        # desktop notification, and ambient identity boundaries are fixtures.
        with patch('controller.read_account', side_effect=read), \
             patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
             patch('controller.Transport', new=lambda *args: manager), \
             patch('controller.time.sleep', side_effect=next_poll), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PollsComplete): run(config, self.root)
        first = polls[0]
        self.assertEqual([call[0] for call in first['calls']
                          if call[1:] == ('read_thread', 'fixture-parent')], [routes[3], routes[2]])
        self.assertEqual([call[2] for call in first['calls'] if call[1] == 'send'], ['fixture-foreman'])
        confirmation, pause = first['state']['episode']['events']
        self.assertFalse(confirmation.get('deliveries'))
        self.assertEqual(pause['reason'], 'observation_failed_during_confirmed_exhaustion')
        self.assertEqual(set(pause['deliveries']), {'foreman'})
        self.assertEqual([call[0] for call in polls[1]['calls'][len(first['calls']):]
                          if call[1:] == ('read_thread', 'fixture-parent')], [routes[1]])
        self.assertEqual([call[2] for call in self.calls if call[1] == 'send'],
                         ['fixture-foreman', 'fixture-parent'])
        final_confirmation, final_pause = polls[-1]['state']['episode']['events']
        self.assertEqual(final_pause['deliveries']['foreman'], pause['deliveries']['foreman'])
        self.assertEqual(final_confirmation['deliveries']['parent']['coalescedInto'], final_pause['id'])
        self.assertEqual(polls[1]['state']['episode']['events'], polls[2]['state']['episode']['events'])

    def test_parent_outage_cannot_block_pause_and_recovery_does_not_repeat_foreman(self):
        pipe = self.route('app.sock', {'fixture-parent': NativeBridgeError('native_tool_rejected', stage='read_thread')})
        self.publish(pipe); manager = self.manager(); router = manager.connection()
        self.assertIsNotNone(router.for_recipient('foreman'))
        self.assertIsNone(router.for_recipient('parent'))
        state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
        path = self.root / 'main.json'
        with self.assertRaisesRegex(RuntimeError, 'parent_destination_unavailable'):
            dispatch(state, ACCOUNT, CONFIG, router, path, current_observation=sample(100))
        receipts = json.loads(path.read_text())['episode']['events'][0]['deliveries']
        self.assertEqual(set(receipts), {'foreman'})
        self.assertEqual(receipts['foreman']['status'], 'submitted')
        self.assertEqual([call[2] for call in self.calls if call[1] == 'send'], ['fixture-foreman'])
        self.assertFalse(manager.health['healthy'])
        self.assertTrue(manager.health['recipients']['foreman']['healthy'])
        self.registry[pipe] = 'valid'; self.now += 301
        router = manager.connection()
        dispatch(json.loads(path.read_text()), ACCOUNT, CONFIG, router, path, current_observation=sample(100))
        self.assertEqual([call[2] for call in self.calls if call[1] == 'send'], ['fixture-foreman', 'fixture-parent'])
        self.assertTrue(manager.health['healthy'])
        manager.close()

    def test_foreman_outage_allows_notification_without_a_pause_delivery_claim(self):
        pipe = self.route('app.sock', {'fixture-foreman': 'deny'}); self.publish(pipe)
        manager = self.manager(); router = manager.connection()
        state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
        path = self.root / 'main.json'
        with self.assertRaisesRegex(RuntimeError, 'foreman_destination_unavailable'):
            dispatch(state, ACCOUNT, CONFIG, router, path, current_observation=sample(100))
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

    def test_malformed_parent_health_cannot_discard_verified_foreman(self):
        pipe = self.route('app.sock', {'fixture-parent': 'deny'}); self.publish(pipe)
        manager = self.manager()
        save(self.root / 'transport/parent/health.json', {'retryAt': 'bad', 'candidateSet': []})
        router = manager.connection()
        self.assertIsNotNone(router.for_recipient('foreman'))
        self.assertIsNone(router.for_recipient('parent'))
        self.assertTrue(manager.health['recipients']['foreman']['healthy'])
        manager.close()

    def test_unexpected_parent_local_failure_isolated_from_foreman(self):
        pipe = self.route('app.sock'); self.publish(pipe); manager=self.manager()
        with patch.object(manager.destinations['parent'], 'connection', side_effect=TypeError('fixture')):
            router=manager.connection()
        self.assertIsNotNone(router.for_recipient('foreman'))
        self.assertFalse(manager.health['recipients']['parent']['healthy'])
        manager.close()


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

    def test_maintenance_restart_keeps_failed_read_fallback_inhibited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); pinned = config['accounts'][1]
            config['policy'].update(pauseWhenUnmetered=True,
                fallbackDecision='pause_at_zero_when_unmetered', fallbackDecisionReference='fixture-approval')
            zero = sample(100); zero.update({k: pinned[k] for k in ('email', 'accountId')})
            state, _ = advance({}, zero, pinned, now=10, policy=config['policy'])
            save(root / 'daybreak.json', state)
            before = (root / 'daybreak.json').read_bytes()
            def failed(account, **kwargs):
                if account['name'] == 'daybreak': raise RuntimeError('fixture_quota_read_failed')
                return sample(0)
            with patch('controller.read_account', side_effect=failed), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport') as transport, patch.dict(os.environ, {}, clear=True):
                for _ in range(2):
                    health = run(config, root, once=True)
                    self.assertEqual(health['delivery']['mode'], 'maintenance')
                    self.assertEqual(health['accounts']['daybreak']['observationStatus'], 'unavailable')
                    self.assertEqual((root / 'daybreak.json').read_bytes(), before)
                transport.assert_not_called()

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
                dispatch(state,ACCOUNT,{'foremanThreadId':'foreman','parentThreadId':'parent'},bridge,path,current_observation=sample(100),
                         delivery_guard=lambda:next(answers))
            self.assertEqual([t for t,_ in bridge.received],['foreman'])
            self.assertEqual(set(state['episode']['events'][0]['deliveries']), {'foreman'})

    def test_older_activation_cannot_overwrite_newer_maintenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=self.config(root)
            def preflight(*args, **kwargs):
                set_mode(config, root, 'maintenance', 'newer-hold')
            with self.assertRaisesRegex(RuntimeError, 'superseded'):
                set_mode(config, root, 'active', 'older-activation', live_preflight=preflight)
            self.assertEqual(delivery_status(config,root), {'mode':'maintenance','reason':'operator_maintenance','reference':'newer-hold'})

    def test_disk_configuration_edit_inhibits_still_running_old_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=self.config(root); path=root/'config.json'
            save(path,config); config['_configPath']=str(path)
            set_mode(config,root,'active','operator',live_preflight=lambda *a,**k:None)
            changed=copy.deepcopy(config); changed['parentThreadId']='new-parent'; save(path,changed)
            self.assertEqual(delivery_status(config,root)['reason'],'activation_configuration_changed')

    def test_restored_quota_blocks_old_unsent_pause_without_changing_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.json'; bridge=ReceiptTransport(path)
            state,_=advance({},sample(100,resets=0),ACCOUNT,now=10)
            key=state['episode']['resetIdempotencyKey']
            state,_=advance(state,sample(50,resets=0),ACCOUNT,now=20)
            with self.assertRaisesRegex(RuntimeError,'current_zero_required_before_delivery'):
                dispatch(state,ACCOUNT,{'foremanThreadId':'foreman','parentThreadId':'parent'},bridge,path,current_observation=sample(50,resets=0))
            self.assertEqual(bridge.received,[])
            self.assertFalse(state['episode']['events'][0].get('deliveries'))
            self.assertEqual(state['episode']['resetIdempotencyKey'],key)
            self.assertTrue(state['episode']['active'])  # No invented release of any hold.

    def test_maintenance_and_send_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=self.config(root); path=root/'main.json'
            set_mode(config,root,'active','operator',live_preflight=lambda *a,**k:None)
            started=threading.Event(); hold_started=threading.Event(); held=threading.Event()
            bridge=ReceiptTransport(path); send=bridge.send; observed=[]
            def blocking_send(target,prompt):
                started.set(); self.assertTrue(hold_started.wait(2))
                self.assertFalse(held.wait(0.05))
                observed.append(delivery_status(config,root)['mode'])
                send(target,prompt)
            bridge.send=blocking_send
            def hold():
                self.assertTrue(started.wait(2)); hold_started.set()
                set_mode(config,root,'maintenance','operator-hold'); held.set()
            state,_=advance({},sample(100),ACCOUNT,now=10)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(hold)
                dispatch(state,ACCOUNT,config,bridge,path,current_observation=sample(100),
                         delivery_guard=lambda:delivery_status(config,root)['mode']=='active',
                         delivery_lock=lambda:decision_lock(root))
                future.result(timeout=3)
            self.assertEqual(observed,['active']); self.assertTrue(held.is_set())
            self.assertEqual(delivery_status(config,root)['mode'],'maintenance')
            self.assertEqual(state['episode']['events'][0]['deliveries']['parent']['status'],'submitted')


if __name__ == '__main__': unittest.main()
