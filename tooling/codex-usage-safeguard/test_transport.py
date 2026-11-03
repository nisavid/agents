"""Connection lifecycle tests use private fixture sockets and fake destinations."""
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from transport import Transport, publish_candidate
from native_bridge import NativeBridgeError
from incident_receipts import ReceiptJournal


CONFIG = {'nativeContext': {'threadId': 'fixture-owner', 'turnId': 'fixture-turn', 'host': 'fixture'},
          'foremanThreadId': 'fixture-foreman', 'parentThreadId': 'fixture-parent',
          'nodeBinary': 'node', 'nativeBridgeScript': '/fixture/server.mjs'}


class FixtureBridge:
    def __init__(self, route, registry, calls):
        self.route = route; self.registry = registry; self.calls = calls; self.closed = False

    def call(self, tool, arguments):
        self.calls.append((self.route, tool, arguments['threadId']))
        mode = self.registry[self.route]
        if isinstance(mode, dict): mode = mode.get(arguments['threadId'], 'valid')
        if isinstance(mode, Exception):
            raise mode
        if mode == 'deny':
            raise RuntimeError('fixture_authorization_rejected')
        target = arguments['threadId']
        return {'thread': {'id': 'wrong' if mode == 'wrong' else target, 'hostId': 'local',
                           'title': ('Renamed' if mode == 'renamed' else
                                     'Codex Foreman' if target == 'fixture-foreman' else 'Parent')}}

    def send(self, target, prompt):
        self.calls.append((self.route, 'send', target))

    def close(self):
        self.closed = True


class TransportFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = {}; self.calls = []; self.alerts = []; self.sockets = []
        self.now = 1000

    def tearDown(self):
        for sock in self.sockets: sock.close()
        self.temp.cleanup()

    def route(self, name, mode='valid'):
        path = str(self.root / name)
        sock = socket.socket(socket.AF_UNIX); sock.bind(path)
        self.sockets.append(sock); self.registry[path] = mode
        return path

    def manager(self, config=None, notifier=None):
        return Transport(self.root, config or CONFIG,
            factory=lambda cfg, route: FixtureBridge(route['pipe'], self.registry, self.calls),
            notifier=notifier or self.alerts.append, clock=lambda: self.now)

    def publish(self, route, config=None):
        return publish_candidate(self.root, config or CONFIG, {'CODEX_APP_TOOLS_PIPE_PATH': route}, now=self.now)


class TransportTests(TransportFixture):
    def test_clock_rollback_rechecks_cached_destinations(self):
        pipe = self.route('available.sock')
        self.publish(pipe)
        transport = self.manager()
        transport.connection()
        initial_reads = len(self.calls)
        self.now -= 100
        transport.connection()
        self.assertGreater(len(self.calls), initial_reads)
        transport.close()

    def test_clock_rollback_releases_persisted_route_backoff(self):
        pipe = self.route('unavailable.sock', mode='deny')
        self.publish(pipe)
        transport = self.manager()
        with self.assertRaises(RuntimeError):
            transport.connection()
        transport.close()
        self.registry[pipe] = 'valid'
        self.now -= 100
        replacement = self.manager()
        self.assertIsNotNone(replacement.connection())
        replacement.close()

    def test_clock_rollback_retries_previously_attempted_route(self):
        pipe = self.route('unavailable.sock', mode='deny')
        self.publish(pipe)
        transport = self.manager()
        with self.assertRaises(RuntimeError):
            transport.connection()
        self.registry[pipe] = 'valid'
        self.now -= 100
        self.assertIsNotNone(transport.connection())
        transport.close()

    def test_retained_incident_keeps_original_attempt_across_release_change(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture'}}}
        self.publish(pipe, config)
        transport = self.manager(config); transport.connection()
        with patch('transport.__file__', '/release-a/transport.py'):
            incident = transport.observation_failed('main')
        path = self.root / 'transport' / 'incident-outbox.json'
        original = json.loads(path.read_text())['incidents'][incident]
        sends = len([call for call in self.calls if call[1] == 'send'])
        with patch('transport.__file__', '/release-b/transport.py'):
            self.assertEqual(transport.observation_failed('main'), incident)
        current = json.loads(path.read_text())['incidents'][incident]
        self.assertEqual(current['attempt'], original['attempt'])
        self.assertEqual(len([call for call in self.calls if call[1] == 'send']), sends)
        config['conciergeThreadId'] = 'changed-concierge'
        with self.assertRaisesRegex(ValueError, 'incident_identity_conflict'):
            transport.observation_failed('main')
        self.assertEqual(len([call for call in self.calls if call[1] == 'send']), sends)
        transport.close()

    def test_known_unsent_incident_recovers_across_release_change_without_replaying_uncertain_send(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture'}}}
        transport = self.manager(config)
        with patch('transport.__file__', '/release-a/transport.py'):
            incident = transport.observation_failed('main')
        path = self.root / 'transport' / 'incident-outbox.json'
        before = json.loads(path.read_text())['incidents'][incident]
        self.assertEqual(before['status'], 'not_attempted')
        transport.destinations['concierge'].bridge = FixtureBridge(pipe, self.registry, self.calls)
        with patch('transport.__file__', '/release-b/transport.py'):
            transport.observation_failed('main')
        state = json.loads(path.read_text())
        self.assertEqual(state['incidents'][incident]['attempt'], before['attempt'])
        self.assertEqual(len([call for call in self.calls if call[1] == 'send']), 1)
        state['incidents'][incident]['status'] = 'outcome_unknown'
        from storage import save
        save(path, state)
        with patch('transport.__file__', '/release-a/transport.py'):
            transport.observation_failed('main')
        self.assertEqual(len([call for call in self.calls if call[1] == 'send']), 1)
        transport.close()

    def test_transport_health_write_failure_marks_incident_registration_failed(self):
        pipe = self.route('unavailable.sock', mode='deny')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture'}}}
        self.publish(pipe, config)
        transport = self.manager(config)
        from storage import save as real_save
        def fail_health(path, value):
            if Path(path) == self.root / 'transport' / 'health.json':
                raise OSError('fixture-health-write-failure')
            return real_save(path, value)
        with patch('transport.save', side_effect=fail_health):
            with self.assertRaises(OSError): transport.connection()
        self.assertTrue(transport.incident_registration_failed)
        self.assertEqual([call for call in self.calls if call[1] == 'send'], [])
        transport.close()

    def test_corrupt_incident_outbox_does_not_block_healthy_dispatch(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        transport = self.manager(config)
        path = self.root / 'transport' / 'incident-outbox.json'
        path.write_text('{invalid')
        path.chmod(0o600)
        self.assertIsNotNone(transport.connection().for_recipient('foreman'))
        self.assertEqual(transport.intake_status, 'outcome_unknown')
        self.assertEqual(path.read_text(), '{invalid')
        transport.close()

    def test_maintenance_blocks_incident_send_but_preserves_known_unsent(self):
        pipe = self.route('partial.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        self.registry[pipe] = {'fixture-foreman': 'deny'}
        transport = Transport(self.root, config,
            factory=lambda cfg, route: FixtureBridge(route['pipe'], self.registry, self.calls),
            notifier=self.alerts.append, clock=lambda: self.now, delivery_guard=lambda: False)
        transport.connection()
        self.assertEqual([call for call in self.calls if call[1] == 'send'], [])
        outbox = json.loads((self.root / 'transport' / 'incident-outbox.json').read_text())
        self.assertEqual(next(iter(outbox['incidents'].values()))['status'], 'not_attempted')
        transport.close()

    def test_emergency_alert_survives_unreadable_notice_state_without_replacing_it(self):
        transport = self.manager()
        target = self.root / 'unrelated'
        target.write_text('preserved')
        path = self.root / 'transport' / 'emergency-notice.json'
        os.symlink(target, path)
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_text(), 'preserved')

    def test_emergency_alert_survives_notice_write_failure(self):
        transport = self.manager()
        with patch('transport.save', side_effect=OSError('fixture-write-failure')):
            self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
            self.assertFalse(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertEqual(len(self.alerts), 1)

    def test_emergency_alert_after_clock_moves_backward(self):
        transport = self.manager()
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.now -= 100
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertEqual(len(self.alerts), 2)
        transport.close()

    def test_unreadable_emergency_notice_may_repeat_after_restart(self):
        path = self.root / 'transport' / 'emergency-notice.json'
        transport = self.manager()
        path.write_text('damaged')
        path.chmod(0o600)
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        transport.close()
        replacement = self.manager()
        self.assertTrue(replacement.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertEqual(path.read_text(), 'damaged')
        self.assertEqual(len(self.alerts), 2)
        replacement.close()

    def test_emergency_notice_throttles_when_durable_state_becomes_unreadable(self):
        transport = self.manager()
        path = self.root / 'transport' / 'emergency-notice.json'
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        path.write_text('damaged')
        self.assertFalse(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertEqual(path.read_text(), 'damaged')
        self.assertEqual(len(self.alerts), 1)
        transport.close()

    def test_observer_throttle_survives_transport_replacement(self):
        throttle = {}
        config = CONFIG
        first = Transport(self.root, config, notifier=self.alerts.append, clock=lambda: self.now,
                          emergency_throttle=throttle)
        path = self.root / 'transport' / 'emergency-notice.json'
        path.write_text('damaged'); path.chmod(0o600)
        self.assertTrue(first.emergency_notice(intake_failed=True, active=True, used_percent=100))
        first.close()
        replacement = Transport(self.root, config, notifier=self.alerts.append, clock=lambda: self.now,
                                emergency_throttle=throttle)
        self.assertFalse(replacement.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertEqual(path.read_text(), 'damaged')
        self.now += 3600
        self.assertTrue(replacement.emergency_notice(intake_failed=True, active=True, used_percent=100))
        replacement.close()

    def test_emergency_notice_throttles_after_storage_recovers_from_failed_attempt(self):
        def failed_notice(_message):
            raise RuntimeError('fixture-unknown-notifier-outcome')
        transport = self.manager(notifier=failed_notice)
        with patch('transport.save', side_effect=OSError('fixture-write-failure')):
            with self.assertRaises(RuntimeError):
                transport.emergency_notice(intake_failed=True, active=True, used_percent=100)
        transport.notifier = self.alerts.append
        self.assertFalse(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.now += 3600
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=100))
        self.assertEqual(len(self.alerts), 1)
        transport.close()

    def test_incident_receipt_and_owned_continuation_reconcile_end_to_end_offline(self):
        pipe = self.route('partial.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'},
                                   'repairOwner': {'thread_id': 'fixture-owner-chat', 'host_id': 'local',
                                                   'role': 'repair_owner', 'acceptance_ref': 'fixture-owner'}}}
        self.publish(pipe, config)
        self.registry[pipe] = {'fixture-foreman': 'deny'}
        transport = self.manager(config)
        with patch('transport.__file__', '/release-a/transport.py'):
            transport.connection()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        origin = next(iter(json.loads(outbox_path.read_text())['incidents'].values()))
        incident, attempt = origin['identity']['incident_id'], origin['attempt']['id']
        sends = len([call for call in self.calls if call[1] == 'send'])
        self.registry[pipe] = 'valid'
        with patch('transport.__file__', '/release-b/transport.py'):
            transport.connection()
        self.assertEqual(len([call for call in self.calls if call[1] == 'send']), sends)
        self.assertEqual(json.loads(outbox_path.read_text())['incidents'][incident]['attempt'], origin['attempt'])
        journal = ReceiptJournal.create(self.root / 'receipts.json')
        journal.record_incident(origin['identity'])
        journal.record_attempt(incident, origin['attempt'])
        journal.append(incident, {'id': 'receipt', 'kind': 'received', 'attempt_id': attempt,
            'evidence': 'fixture-evidence', 'actor': 'fixture-concierge', 'host': 'local',
            'turn': 'receipt-turn', 'message': 'receipt-message',
            'submission_turn': 'send-turn', 'submission_message': 'send-message'})
        for kind, turn in (('owner_accepted', 'owner-turn'), ('continuation', 'next-turn')):
            observation = {'id': kind, 'kind': kind, 'attempt_id': attempt, 'evidence': 'fixture-evidence',
                           'actor': 'fixture-owner-chat', 'host': 'local', 'turn': turn, 'message': kind}
            if kind == 'continuation': observation['next_action'] = 'inspect incident'
            journal.append(incident, observation)
        journal.close()
        class NativeReadFixture:
            def call(self, _tool, arguments):
                thread = arguments['threadId']
                turns = []
                if thread == 'fixture-concierge':
                    turns.append({'id': 'send-turn', 'items': [{'id': 'send-message', 'type': 'userMessage',
                                 'content': [{'type': 'text', 'text': origin['attempt']['payload']}]}]})
                    kinds = [('receipt', 'receipt-turn', 'receipt-message')]
                else:
                    kinds = [('owner_accepted', 'owner-turn', 'owner_accepted'),
                             ('continuation', 'next-turn', 'continuation')]
                for kind, turn, message in kinds:
                    suffix = ',"next_action":"inspect incident"' if kind == 'continuation' else ''
                    line = f'QUOTA-ACK/1 {{"attempt":"{attempt}","incident":"{incident}","kind":"{kind}"{suffix}}}'
                    turns.append({'id': turn, 'items': [{'id': message, 'type': 'agentMessage', 'text': line}]})
                return {'thread': {'id': thread, 'hostId': 'local'}, 'turns': turns}
            def close(self): pass
        transport.destinations['concierge'].bridge.close()
        transport.destinations['concierge'].bridge = NativeReadFixture()
        transport.reconcile_pending()
        result = json.loads(outbox_path.read_text())['incidents'][incident]
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['reconciliation'][-1]['stage'], 'owned_continuation_matched')
        self.assertEqual(transport.intake_status, 'not_needed')
        transport.close()

    def test_matched_receipt_suppresses_intake_failure_without_closing_incident(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'},
                                   'repairOwner': {'thread_id': 'owner', 'host_id': 'local',
                                                   'role': 'repair_owner', 'acceptance_ref': 'fixture'}}}
        self.publish(pipe, config)
        transport = self.manager(config)
        transport.connection()
        incident = transport.observation_failed('main')
        self.assertEqual(transport.intake_status, 'submitted')
        (self.root / 'receipts.json').write_text('{}')
        (self.root / 'receipts.json').chmod(0o600)
        with patch('transport.reconcile_snapshot', return_value='receipt_matched'):
            transport.reconcile_pending()
        outbox = json.loads((self.root / 'transport' / 'incident-outbox.json').read_text())
        self.assertEqual(outbox['incidents'][incident]['status'], 'submitted')
        self.assertEqual(outbox['incidents'][incident]['reconciliation'][-1]['stage'], 'receipt_matched')
        self.assertEqual(transport.intake_status, 'not_needed')
        self.now += 61
        with patch('transport.reconcile_snapshot', return_value='receipt_matched') as readback:
            transport.reconcile_pending()
            readback.assert_called_once()
        (self.root / 'receipts.json').unlink()
        self.now += 61
        transport.reconcile_pending()
        self.assertEqual(transport.intake_status, 'submitted')
        transport.close()

    def test_transient_receipt_readback_recovers_without_resending(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'},
                                   'repairOwner': {'thread_id': 'owner', 'host_id': 'local',
                                                   'role': 'repair_owner', 'acceptance_ref': 'fixture'}}}
        self.publish(pipe, config)
        transport = self.manager(config)
        transport.connection()
        incident = transport.observation_failed('main')
        (self.root / 'receipts.json').write_text('{}')
        (self.root / 'receipts.json').chmod(0o600)
        sends = len([call for call in self.calls if call[1] == 'send'])
        with patch('transport.reconcile_snapshot', side_effect=['pending', 'owned_continuation_matched']) as readback:
            transport.reconcile_pending()
            self.now += 61
            transport.close()
            transport = self.manager(config)
            transport.connection()
            transport.reconcile_pending()
            self.assertEqual(readback.call_count, 2)
        outbox = json.loads((self.root / 'transport' / 'incident-outbox.json').read_text())
        self.assertEqual(outbox['incidents'][incident]['reconciliation'][-1]['stage'], 'owned_continuation_matched')
        self.assertEqual(len([call for call in self.calls if call[1] == 'send']), sends)
        transport.close()

    def test_one_submitted_fault_does_not_hide_another_known_unsent_fault(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        transport = self.manager(config)
        transport.connection()
        destination = transport.destinations['concierge']
        bridge = destination.bridge
        destination.bridge = None
        transport.observation_failed('main')
        destination.bridge = bridge
        transport.observation_failed('daybreak')
        self.assertEqual(transport.intake_status, 'waiting_for_route')
        transport.close()

    def test_observation_failure_is_deduplicated_until_recovery(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        transport = self.manager(config)
        transport.connection()
        first = transport.observation_failed('main')
        self.assertEqual(transport.observation_failed('main'), first)
        self.assertEqual(len([call for call in self.calls if call[1:] == ('send', 'fixture-concierge')]), 1)
        transport.observation_recovered('main')
        self.assertNotEqual(transport.observation_failed('main'), first)
        self.assertEqual(len([call for call in self.calls if call[1:] == ('send', 'fixture-concierge')]), 2)
        transport.close()

    def test_observation_fault_known_unsent_survives_restart_and_route_recovery(self):
        pipe = self.route('partial.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        self.registry[pipe] = {'fixture-concierge': 'deny'}
        transport = self.manager(config)
        transport.connection()
        incident = transport.observation_failed('main')
        self.assertEqual(transport.intake_status, 'waiting_for_route')
        transport.observation_recovered('main')
        transport.close()
        self.registry[pipe] = 'valid'
        self.now += 301
        replacement = self.manager(config)
        replacement.connection()
        outbox = json.loads((self.root / 'transport' / 'incident-outbox.json').read_text())
        self.assertEqual(outbox['incidents'][incident]['status'], 'submitted')
        self.assertNotEqual(replacement.intake_status, 'waiting_for_route')
        replacement.close()

    def test_unregistered_observation_fault_recovers_after_transport_replacement(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture'}}}
        self.publish(pipe, config)
        transport = self.manager(config); transport.connection()
        with patch('incident_delivery.save', side_effect=OSError('fixture-outbox-write')):
            with self.assertRaises(OSError): transport.observation_failed('main')
        fault_path = self.root / 'transport' / 'observation-faults.json'
        incident = json.loads(fault_path.read_text())['main']['incident']
        with patch('incident_delivery.save', side_effect=OSError('fixture-outbox-write')):
            with self.assertRaises(OSError): transport.observation_failed('main')
        self.assertEqual(json.loads(fault_path.read_text())['main']['incident'], incident)
        transport.close()
        replacement = self.manager(config); replacement.connection()
        replacement.observation_recovered('main')
        outbox = json.loads((self.root / 'transport' / 'incident-outbox.json').read_text())
        self.assertEqual(outbox['incidents'][incident]['status'], 'submitted')
        self.assertEqual(len([call for call in self.calls if call[1:] == ('send', 'fixture-concierge')]), 1)
        replacement.observation_recovered('main')
        self.assertEqual(len([call for call in self.calls if call[1:] == ('send', 'fixture-concierge')]), 1)
        replacement.close()

    def test_observation_recovery_never_replays_uncertain_attempt(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture'}}}
        self.publish(pipe, config)
        transport = self.manager(config); transport.connection()
        bridge = transport.destinations['concierge'].bridge
        with patch.object(bridge, 'send', side_effect=TimeoutError('fixture-uncertain')) as send:
            transport.observation_failed('main')
            incident = json.loads((self.root / 'transport' / 'observation-faults.json').read_text())['main']['incident']
            transport.observation_recovered('main')
            self.assertEqual(send.call_count, 1)
        record = json.loads((self.root / 'transport' / 'incident-outbox.json').read_text())['incidents'][incident]
        self.assertEqual(record['status'], 'outcome_unknown')
        transport.close()

    def test_emergency_notice_requires_failed_intake_and_urgent_or_unknown_context(self):
        transport = self.manager()
        self.assertFalse(transport.emergency_notice(intake_failed=False, active=True, used_percent=100))
        self.assertFalse(transport.emergency_notice(intake_failed=True, active=False, used_percent=100))
        self.assertFalse(transport.emergency_notice(intake_failed=True, active=True, used_percent=94.9))
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=True, used_percent=95))
        self.assertFalse(transport.emergency_notice(intake_failed=True, active=True, used_percent=95))
        self.now += 3601
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=None, used_percent=50))
        self.now += 3601
        self.assertTrue(transport.emergency_notice(intake_failed=True, active=False, used_percent=None))
        self.assertEqual(len(self.alerts), 3)

    def test_incident_recipient_remains_available_when_dispatch_fails(self):
        pipe = self.route('partial.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge',
                  'incidentRoutingEnabled': True,
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        self.registry[pipe] = {'fixture-foreman': 'deny', 'fixture-concierge': 'renamed'}
        transport = self.manager(config)
        self.assertIsNone(transport.connection().for_recipient('foreman'))
        self.assertIsNotNone(transport.for_recipient('concierge'))
        transport.close()

    def test_disabled_incident_routing_does_not_probe_concierge(self):
        pipe = self.route('available.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': False}
        self.publish(pipe, config)
        transport = self.manager(config)
        transport.connection()
        self.assertFalse(any(call[2] == 'fixture-concierge' for call in self.calls))
        transport.close()

    def test_fault_handoff_is_submitted_once_when_dispatch_route_fails(self):
        pipe = self.route('partial.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        self.registry[pipe] = {'fixture-foreman': 'deny'}
        transport = self.manager(config)
        transport.connection()
        transport.connection()
        self.assertEqual(len([call for call in self.calls if call[1:] == ('send', 'fixture-concierge')]), 1)
        self.assertEqual(self.alerts, [])
        transport.close()

    def test_known_unsent_fault_is_handed_off_after_full_route_recovery(self):
        pipe = self.route('outage.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        self.registry[pipe] = 'deny'
        transport = self.manager(config)
        with self.assertRaises(RuntimeError):
            transport.connection()
        self.assertEqual([call for call in self.calls if call[1] == 'send'], [])
        self.registry[pipe] = 'valid'
        self.now += 301
        transport.connection()
        self.assertEqual(len([call for call in self.calls if call[1:] == ('send', 'fixture-concierge')]), 1)
        transport.close()

    def test_outbox_write_failure_during_recovery_keeps_incident_recoverable(self):
        pipe = self.route('outage.sock')
        config = {**CONFIG, 'conciergeThreadId': 'fixture-concierge', 'incidentRoutingEnabled': True,
                  'incidentReceiptStore': str(self.root / 'receipts.json'),
                  'roleBindings': {'concierge': {'thread_id': 'fixture-concierge', 'host_id': 'local',
                                                'role': 'concierge', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        self.registry[pipe] = 'deny'
        transport = self.manager(config)
        with self.assertRaises(RuntimeError):
            transport.connection()
        self.registry[pipe] = 'valid'
        self.now += 301
        with patch('transport.handoff_fault', side_effect=OSError('fixture-write-failure')):
            self.assertIsNotNone(transport.connection().for_recipient('foreman'))
            self.assertEqual(transport.intake_status, 'outcome_unknown')
        transport.connection()
        self.assertEqual(len([call for call in self.calls if call[1:] == ('send', 'fixture-concierge')]), 1)
        transport.close()

    def test_accepted_dispatch_binding_survives_recipient_rename(self):
        pipe = self.route('renamed.sock')
        config = {**CONFIG, 'roleBindings': {'foreman': {'thread_id': 'fixture-foreman',
                  'host_id': 'local', 'role': 'dispatch', 'acceptance_ref': 'fixture-acceptance'}}}
        self.publish(pipe, config)
        transport = self.manager(config)
        self.registry[pipe] = {'fixture-foreman': 'renamed'}
        self.assertIsNotNone(transport.connection().for_recipient('foreman'))
        transport.close()

    def test_concurrent_companions_coalesce_and_missing_pipe_keeps_candidate(self):
        pipe = self.route('app.sock')
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.publish(pipe), range(32)))
        self.assertEqual(publish_candidate(self.root, CONFIG, {}, now=self.now)['status'], 'no_inherited_route')
        candidates = json.loads((self.root / 'transport' / 'candidates.json').read_text())
        self.assertEqual(len(candidates), 1)
        self.assertEqual((self.root / 'transport' / 'candidates.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.calls, [])

    def test_clock_rollback_keeps_and_prefers_new_route_in_full_registry(self):
        for index in range(16):
            pipe = self.route(f'old-{index}.sock', mode='valid' if index == 0 else 'deny')
            self.now = 2000 + index
            self.publish(pipe)
            if index == 0:
                previous = self.manager()
                previous.connection()
                previous.close()
                self.registry[pipe] = 'deny'
        new_pipe = self.route('new.sock')
        self.now = 1000
        published = self.publish(new_pipe)
        path = self.root / 'transport' / 'candidates.json'
        candidates = json.loads(path.read_text())
        self.assertEqual(len(candidates), 16)
        self.assertIn(published['generation'], candidates)
        before = path.read_bytes()
        self.publish(new_pipe)
        self.assertEqual(path.read_bytes(), before)
        transport = self.manager()
        self.assertEqual(transport.connection().for_recipient('foreman').route, new_pipe)
        transport.close()

    def test_boolean_route_timestamp_is_reconstructed_without_losing_accepted_route(self):
        old_pipe = self.route('accepted.sock')
        self.publish(old_pipe)
        transport = self.manager(); transport.connection(); transport.close()
        path = self.root / 'transport' / 'candidates.json'
        candidates = json.loads(path.read_text())
        next(iter(candidates.values()))['publishedAt'] = True
        from storage import save
        save(path, candidates)
        new_pipe = self.route('new.sock')
        self.publish(new_pipe)
        self.assertEqual(len(json.loads(path.read_text())), 1)
        self.assertEqual(self.manager().connection().for_recipient('foreman').route, old_pipe)

    def test_extreme_route_timestamp_does_not_block_accepted_route(self):
        old_pipe = self.route('accepted.sock')
        self.publish(old_pipe)
        transport = self.manager(); transport.connection(); transport.close()
        path = self.root / 'transport' / 'candidates.json'
        candidates = json.loads(path.read_text())
        next(iter(candidates.values()))['publishedAt'] = 10 ** 400
        from storage import save
        save(path, candidates)
        self.assertEqual(self.manager().connection().for_recipient('foreman').route, old_pipe)

    def test_exhausted_publication_order_preserves_registry(self):
        self.publish(self.route('old.sock'))
        path = self.root / 'transport' / 'candidates.json'
        candidates = json.loads(path.read_text())
        next(iter(candidates.values()))['publishedAt'] = sys.float_info.max
        from storage import save
        save(path, candidates)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'route_publication_order_exhausted'):
            self.publish(self.route('new.sock'))
        self.assertEqual(path.read_bytes(), before)

    def test_adopts_only_after_both_exact_destinations_then_renews_after_failure(self):
        first = self.route('one.sock'); second = self.route('two.sock')
        self.publish(first); transport = self.manager()
        old = transport.connection().for_recipient('foreman')
        self.assertEqual([c[2] for c in self.calls], ['fixture-foreman', 'fixture-parent'])
        self.now += 1; self.publish(second)
        self.assertIs(transport.connection().for_recipient('foreman'), old)  # A healthy accepted route remains stable.
        self.registry[first] = 'deny'; self.now += 61
        new = transport.connection().for_recipient('foreman')
        self.assertEqual(new.route, second); self.assertTrue(old.closed)
        self.assertEqual(self.alerts, [])
        transport.close()
        self.assertEqual(self.manager().connection().for_recipient('foreman').route, second)

    def test_rejected_registration_latches_one_actionable_alert_until_recovery(self):
        pipe = self.route('rejected.sock', 'deny'); self.publish(pipe)
        transport = self.manager()
        for _ in range(4):
            with self.assertRaises(RuntimeError): transport.connection()
            self.now += 301
        self.assertEqual(len(self.alerts), 1)
        self.assertIn('registration', self.alerts[0])
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)
        self.registry[pipe] = 'valid'; self.now += 301
        transport.connection()
        health = json.loads((self.root / 'transport' / 'health.json').read_text())
        self.assertTrue(health['healthy']); self.assertIsNone(health['incident'])
        self.registry[pipe] = 'deny'; self.now += 301
        with self.assertRaises(RuntimeError): transport.connection()
        self.assertEqual(len(self.alerts), 2)

    def test_stale_socket_and_app_rejection_remain_distinguishable_during_cooldown(self):
        first = self.route('old.sock'); self.publish(first)
        transport = self.manager(); transport.connection(); transport.close()
        os.unlink(first)
        self.now += 301
        second = self.route('new.sock', NativeBridgeError('native_app_request_failed',
            stage='read_thread', rpc_code=-32000))
        self.publish(second)
        with self.assertRaises(RuntimeError): transport.connection()
        health = json.loads((self.root / 'transport' / 'health.json').read_text())
        self.assertEqual([x['kind'] for x in health['recipients']['foreman']['lastFailures']],
                         ['native_socket_missing', 'native_app_request_failed'])
        self.assertEqual(health['recipients']['foreman']['lastFailures'][1]['recipient'], 'foreman')
        failed_at = health['recipients']['foreman']['lastFailureAt']
        self.now += 6  # Health retry expires before the per-route retry cooldown.
        with self.assertRaises(RuntimeError): transport.connection()
        retained = json.loads((self.root / 'transport' / 'health.json').read_text())
        self.assertEqual(retained['recipients']['foreman']['lastFailures'], health['recipients']['foreman']['lastFailures'])
        self.assertEqual(retained['recipients']['foreman']['lastFailureAt'], failed_at)
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(any(c[1] == 'send' for c in self.calls))
        self.registry[second] = 'valid'; self.now += 301
        transport.connection()
        recovered = json.loads((self.root / 'transport' / 'health.json').read_text())
        self.assertTrue(recovered['healthy'])
        self.assertNotIn('lastFailures', recovered)
        transport.close()

    def test_unknown_failure_details_cannot_leak_into_health(self):
        pipe = self.route('private.sock', RuntimeError('secret-token fixture@example.test'))
        self.publish(pipe)
        with self.assertRaises(RuntimeError): self.manager().connection()
        raw = (self.root / 'transport' / 'health.json').read_text()
        self.assertNotIn('secret-token', raw)
        self.assertNotIn('fixture@example.test', raw)
        self.assertEqual(json.loads(raw)['recipients']['foreman']['lastFailures'][0]['kind'], 'native_route_check_failed')

    def test_changed_context_and_wrong_foreman_cannot_be_adopted(self):
        pipe = self.route('app.sock')
        changed = copy.deepcopy(CONFIG); changed['nativeContext']['turnId'] = 'unregistered'
        self.publish(pipe, changed)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(self.calls, [])
        self.publish(pipe); self.registry[pipe] = 'wrong'; self.now += 301
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertFalse((self.root / 'transport' / 'foreman' / 'accepted.json').exists())

    def test_failed_notification_is_recorded_without_repetition_after_restart(self):
        def broken(message): raise RuntimeError('desktop_unavailable')
        with self.assertRaises(RuntimeError): self.manager(notifier=broken).connection()
        health = json.loads((self.root / 'transport' / 'health.json').read_text())
        self.assertEqual(health['notice']['status'], 'failed')
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(self.alerts, [])

    def test_outage_reminder_after_clock_moves_backward(self):
        transport = self.manager()
        with self.assertRaises(RuntimeError):
            transport.connection()
        self.assertEqual(len(self.alerts), 1)
        self.now -= 100
        with self.assertRaises(RuntimeError):
            transport.connection()
        self.assertEqual(len(self.alerts), 2)
        transport.close()

    def test_policy_and_delivery_files_survive_transport_upgrade_and_rollback(self):
        preserved = {'episode': {'id': 'open', 'active': True, 'resetIdempotencyKey': 'same',
            'events': [{'deliveries': {'foreman': {'status': 'outcome_unknown'}}}]},
            'identityGeneration': 'generation', 'approval': 'actual-human-reference'}
        state = self.root / 'main.json'; state.write_text(json.dumps(preserved))
        before = state.read_bytes(); self.publish(self.route('app.sock'))
        transport = self.manager(); transport.connection(); transport.close()
        self.manager().connection()
        self.assertEqual(state.read_bytes(), before)
        self.assertFalse(any(c[1] == 'send' for c in self.calls))

    def test_inherited_route_must_be_owned_socket_and_private_registry(self):
        regular = self.root / 'ordinary'; regular.write_text('not a socket')
        with self.assertRaises(ValueError): self.publish(str(regular))
        pipe = self.route('app.sock'); self.publish(pipe)
        os.chmod(self.root / 'transport' / 'candidates.json', 0o644)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)
        before = (self.root / 'transport' / 'candidates.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'private_owned_file_required'):
            self.publish(pipe)
        self.assertEqual((self.root / 'transport' / 'candidates.json').read_bytes(), before)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)

    def test_publication_recovers_only_malformed_private_route_metadata(self):
        from storage import save
        pipe = self.route('app.sock'); self.publish(pipe)
        path = self.root / 'transport' / 'candidates.json'
        ledger = self.root / 'main.json'; ledger.write_text('preserved-episode-and-receipts')
        for malformed in ([], 42, {'bad': {}}, {'bad': {'publishedAt': 'wrong'}}):
            with self.subTest(malformed=malformed):
                save(path, malformed)
                self.publish(pipe)
                self.assertEqual(len(json.loads(path.read_text())), 1)
        path.write_text('invalid JSON')
        self.publish(pipe)
        self.assertEqual(len(json.loads(path.read_text())), 1)
        self.assertEqual(ledger.read_text(), 'preserved-episode-and-receipts')
        self.assertEqual(self.calls, [])

    def test_corrupt_candidate_registry_cannot_displace_accepted_route(self):
        pipe = self.route('app.sock'); self.publish(pipe)
        manager = self.manager(); manager.connection(); manager.close()
        (self.root / 'transport' / 'candidates.json').write_text('invalid JSON')
        self.assertEqual(self.manager().connection().for_recipient('foreman').route, pipe)
        self.assertEqual(self.alerts, [])

    def test_unreadable_health_and_malformed_registry_record_one_recovery_incident(self):
        root = self.root / 'transport'; root.mkdir(mode=0o700)
        (root / 'health.json').write_text('bad JSON')
        (root / 'candidates.json').write_text('{"bad": {"publishedAt": "invalid"}}')
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)
        with self.assertRaises(RuntimeError): self.manager().connection()
        self.assertEqual(len(self.alerts), 1)


if __name__ == '__main__': unittest.main()
