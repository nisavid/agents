"""Real routing guards with synthetic accounts, native readback, and private stores.

No fixture here establishes native authorization, operator consent, or store
access by another chat. Only the external account/socket/bridge boundaries are
replaced; configuration validation, receipt matching, and activation are real.
"""
import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from activation import (ROUTING_WITNESSES, configuration_binding, delivery_status,
                        evidence_digest, routing_readiness, set_mode, source_binding)
from controller import validate_config
from controller import run
from incident_receipts import ReceiptJournal
from incident_delivery import reconcile_snapshot
from preflight import preflight
from storage import read_private, save
from native_bridge import NativeBridgeError
from transport import Transport as RealTransport, publish_candidate
from test_guard import ACCOUNT, sample


class Readback:
    def __init__(self):
        self.turns = {}
        self.hosts = {}
        self.calls = []
        self.on_read = None

    def call(self, tool, args):
        if tool != 'read_thread':
            raise AssertionError('qualification must only read native state')
        self.calls.append(args['threadId'])
        if self.on_read is not None:
            self.on_read(args)
        thread = args['threadId']
        return {'thread': {'id': thread, 'hostId': self.hosts.get(thread, 'local')},
                'turns': copy.deepcopy(self.turns.get(thread, []))}

    def send(self, *args):
        raise AssertionError('qualification must never send')

    def close(self):
        pass


class RoutingReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads(Path(__file__).with_name('config.example.json').read_text())
        self.config.update(enabled=True, stateDir=str(self.root), sharedLock=str(self.root / 'legacy.lock'),
                           incidentRoutingEnabled=True, foremanThreadId='dispatch',
                           parentThreadId='parent', parentHostId='local', conciergeThreadId='concierge',
                           incidentReceiptStore=str(self.root / 'receipts.json'),
                           nativeContext={'threadId': 'genuine-fixture-caller', 'turnId': 'fixture-turn', 'host': 'local'},
                           roleBindings={
                               'foreman': {'thread_id': 'dispatch', 'host_id': 'local', 'role': 'dispatch',
                                           'acceptance_ref': 'fixture-dispatch-acceptance'},
                               'concierge': {'thread_id': 'concierge', 'host_id': 'local', 'role': 'concierge',
                                             'acceptance_ref': 'fixture-concierge-acceptance'},
                               'repairOwner': {'thread_id': 'owner', 'host_id': 'local', 'role': 'repair_owner',
                                               'acceptance_ref': 'fixture-owner-acceptance'}})
        for name in ('legacy.lock', 'reset.lock'):
            (self.root / name).touch(mode=0o600)
        (self.root / 'transport').mkdir(mode=0o700)
        (self.root / 'routing-evidence').mkdir(mode=0o700)
        self.native = Readback()
        dispatch = self.attempt('dispatch', 'dispatch-incident', 'dispatch-attempt')
        concierge = self.attempt('concierge', 'concierge-incident', 'concierge-attempt')
        save(self.root / 'transport' / 'incident-outbox.json', {
            'version': 1, 'incidents': {'concierge-incident': {
                'identity': concierge['identity'], 'attempt': concierge['attempt'], 'status': 'outcome_unknown'}}})
        journal = ReceiptJournal.create(self.config['incidentReceiptStore'])
        journal.record_incident(concierge['identity'])
        journal.record_attempt('concierge-incident', concierge['attempt'])
        for kind, target in (('received', 'concierge'), ('owner_accepted', 'owner'), ('continuation', 'owner')):
            ack_kind = 'receipt' if kind == 'received' else kind
            reference = self.ack(target, 'concierge-incident', 'concierge-attempt', ack_kind)
            observation = {'id': kind, 'kind': kind, 'attempt_id': 'concierge-attempt',
                           'evidence': 'synthetic-native-readback', 'actor': target, 'host': 'local', **reference}
            if kind == 'received':
                observation.update(submission_turn=concierge['submission']['turn'],
                                   submission_message=concierge['submission']['message'])
            if kind == 'continuation':
                observation['next_action'] = 'inspect fixture incident'
            journal.append('concierge-incident', observation)
        journal.close()
        dispatch['receipt'] = self.ack('dispatch', 'dispatch-incident', 'dispatch-attempt', 'receipt')
        artifacts = {}
        for name in ROUTING_WITNESSES:
            witness = {'fixture': True, 'observation': name, 'sourceBinding': source_binding(),
                       'limitation': 'constructed evidence; no native execution'}
            save(self.root / 'routing-evidence' / (name + '.json'), witness)
            artifacts[name] = evidence_digest(witness)
        record = read_private(self.config['incidentReceiptStore'])['incidents']['concierge-incident']
        self.packet = {'version': 1, 'configurationBinding': configuration_binding(self.config),
                       'sourceBinding': source_binding(), 'parentHost': 'local', 'dispatch': dispatch,
                       'conciergeIncident': 'concierge-incident', 'conciergeRecordDigest': evidence_digest(record),
                       'artifacts': artifacts}
        save(self.root / 'routing-readiness.json', self.packet)

    def attempt(self, target, incident, attempt_id):
        identity = {'incident_id': incident, 'sender': self.config['nativeContext']['threadId'],
                    'sender_host': 'local', 'recipient': target, 'recipient_host': 'local'}
        attempt = {'id': attempt_id, 'caller': identity['sender'], 'caller_host': 'local',
                   'target': target, 'target_host': 'local', 'payload': incident + ' ' + attempt_id}
        submission = {'host': 'local', 'turn': target + '-send', 'message': target + '-message'}
        self.native.turns.setdefault(target, []).append({
            'id': submission['turn'], 'items': [{'id': submission['message'], 'type': 'userMessage',
                                               'content': [{'type': 'text', 'text': attempt['payload']}]}]})
        return {'identity': identity, 'attempt': attempt, 'submission': submission}

    def ack(self, target, incident, attempt_id, kind):
        reference = {'turn': target + '-' + kind, 'message': target + '-' + kind + '-message'}
        data = {'incident': incident, 'attempt': attempt_id, 'kind': kind}
        if kind == 'continuation':
            data['next_action'] = 'inspect fixture incident'
        text = 'QUOTA-ACK/1 ' + json.dumps(data, sort_keys=True, separators=(',', ':'))
        self.native.turns.setdefault(target, []).append({
            'id': reference['turn'], 'items': [{'id': reference['message'], 'type': 'agentMessage', 'text': text}]})
        return reference

    def activate(self, digest=None):
        with patch('preflight.read_account'), patch('preflight.socket_identity'), \
             patch('preflight.NativeBridge', return_value=self.native), \
             patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': '/fixture/native.sock'}):
            return set_mode(self.config, self.root, 'active', 'fixture-reviewed-activation',
                            qualification_digest=digest if digest is not None else evidence_digest(self.packet))

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_real_activation_rechecks_each_stage_and_survives_restart_without_send(self):
        retained = (self.root / 'transport' / 'incident-outbox.json').read_bytes()
        self.assertEqual(self.activate()['mode'], 'active')
        self.assertEqual(delivery_status(copy.deepcopy(self.config), self.root)['mode'], 'active')
        self.assertEqual((self.root / 'transport' / 'incident-outbox.json').read_bytes(), retained)
        self.assertEqual(read_private(self.root / 'activation.json')['routingReadiness'], evidence_digest(self.packet))
        self.assertTrue({'dispatch', 'parent', 'concierge', 'owner'} <= set(self.native.calls))

    def test_oversized_qualification_inhibits_delivery(self):
        self.activate()
        (self.root / 'routing-readiness.json').write_bytes(b'x' * (256 * 1024 + 1))
        self.assertEqual(delivery_status(self.config, self.root)['mode'], 'maintenance')

    def test_live_journal_corruption_does_not_replace_sealed_qualification(self):
        self.activate()
        Path(self.config['incidentReceiptStore']).write_text('{invalid')
        self.assertEqual(delivery_status(self.config, self.root)['mode'], 'active')

    def test_missing_native_stages_and_wrong_parent_host_leave_maintenance(self):
        original = copy.deepcopy(self.native.turns)
        for target, turn in (('dispatch', 'dispatch-receipt'), ('concierge', 'concierge-send'),
                             ('concierge', 'concierge-receipt'), ('owner', 'owner-owner_accepted'),
                             ('owner', 'owner-continuation')):
            with self.subTest(target=target, turn=turn):
                self.native.turns = copy.deepcopy(original)
                self.native.turns[target] = [row for row in self.native.turns[target] if row['id'] != turn]
                with self.assertRaises(ValueError):
                    self.activate()
                self.assertEqual(delivery_status(self.config, self.root)['mode'], 'maintenance')
        self.native.turns = original
        self.native.hosts['parent'] = 'different-host'
        with self.assertRaises(RuntimeError):
            self.activate()
        self.assertEqual(delivery_status(self.config, self.root)['mode'], 'maintenance')

    def test_unrelated_journal_append_preserves_qualification(self):
        self.activate()
        store = Path(self.config['incidentReceiptStore'])
        snapshot = read_private(store)
        snapshot['incidents']['unrelated'] = {'fixture': 'unrelated'}
        save(store, snapshot)
        self.assertEqual(delivery_status(self.config, self.root)['mode'], 'active')

    def test_deeply_nested_packet_or_witness_inhibits_delivery(self):
        self.activate()
        packet = self.root / 'routing-readiness.json'
        witness = self.root / 'routing-evidence' / 'caller-provenance.json'
        real_loads = json.loads
        def decoder(value, *args, **kwargs):
            if isinstance(value, bytes) and value.startswith(b'[' * 2000):
                raise RecursionError('synthetic decoder limit')
            return real_loads(value, *args, **kwargs)
        for path in (packet, witness):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                try:
                    path.write_bytes(b'[' * 2000 + b'0' + b']' * 2000)
                    with patch('activation.json.loads', side_effect=decoder):
                        self.assertEqual(delivery_status(self.config, self.root)['mode'], 'maintenance')
                finally:
                    path.write_bytes(original)

    def test_changed_source_or_witness_inhibits_delivery(self):
        release = self.root / 'release'
        release.mkdir(mode=0o700)
        module = release / 'activation.py'
        module.write_text('# synthetic release\n')
        with patch('activation.__file__', str(module)):
            self.packet['sourceBinding'] = source_binding()
            save(self.root / 'routing-readiness.json', self.packet)
            self.activate()
            witness = self.root / 'routing-evidence' / 'consumer-restart.json'
            original = witness.read_bytes()
            save(witness, {'changed': True})
            self.assertEqual(delivery_status(self.config, self.root)['mode'], 'maintenance')
            witness.write_bytes(original)
            self.assertEqual(delivery_status(self.config, self.root)['mode'], 'active')
            module.write_text('# changed synthetic release\n')
            self.assertEqual(delivery_status(self.config, self.root)['mode'], 'maintenance')

    def test_newer_maintenance_supersedes_qualified_activation(self):
        held = False
        def hold(args):
            nonlocal held
            if not held:
                held = True
                set_mode(self.config, self.root, 'maintenance', 'newer-fixture-hold')
        self.native.on_read = hold
        with self.assertRaisesRegex(RuntimeError, 'activation_superseded_by_newer_decision'):
            self.activate()
        self.assertEqual(delivery_status(self.config, self.root)['reference'], 'newer-fixture-hold')

    def test_known_unsent_incident_does_not_alert_for_unknown_activity(self):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        outbox = read_private(outbox_path)
        retained = outbox['incidents']['concierge-incident']
        retained['status'] = 'submitted'
        retained['reconciliation'] = [{'stage': 'receipt_matched'}]
        save(outbox_path, outbox)

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()

            def call(self, tool, args):
                if args['threadId'] == 'concierge':
                    raise NativeBridgeError('native_authorization_rejected', stage='read_thread')
                return super().call(tool, args)

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('transport.NativeBridge', PollBridge), patch('transport.subprocess.run') as notice, \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                run(self.config, self.root, once=True)
                notice.assert_not_called()
        retained = read_private(outbox_path)['incidents']
        self.assertTrue(any(row.get('status') == 'not_attempted' for row in retained.values()))

    def test_failed_receipt_verification_alerts_for_unknown_activity(self):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        outbox = read_private(outbox_path)
        retained = outbox['incidents']['concierge-incident']
        retained['status'] = 'submitted'
        save(outbox_path, outbox)

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('transport.NativeBridge', PollBridge), \
                 patch('transport.reconcile_snapshot', side_effect=RuntimeError('fixture read failure')), \
                 patch('transport.subprocess.run') as notice, \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                health = run(self.config, self.root, once=True)
                self.assertEqual(health.get('receiptReconciliation'), 'unavailable')
                notice.assert_called_once()

    def poll_incidents(self, incidents, *, reconciliation_error=False, concierge_available=True,
                       corrupt_receipt=False):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        outbox = read_private(outbox_path)
        original = outbox['incidents']['concierge-incident']
        outbox['incidents'] = {}
        for name, status, history in incidents:
            row = copy.deepcopy(original)
            row['status'] = status
            row['reconciliation'] = [{'stage': stage} for stage in history]
            if status == 'not_attempted' and corrupt_receipt:
                row['identity']['recipient'] = 'previous-recipient'
            outbox['incidents'][name] = row
        save(outbox_path, outbox)
        original_outbox = outbox_path.read_bytes()
        receipt_path = Path(self.config['incidentReceiptStore'])
        if corrupt_receipt:
            receipt_path.write_text('{invalid fixture')
        original_receipt = receipt_path.read_bytes()

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()

            def call(self, tool, args):
                if args['threadId'] == 'concierge' and not concierge_available:
                    raise NativeBridgeError('native_authorization_rejected', stage='read_thread')
                return super().call(tool, args)

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('transport.NativeBridge', PollBridge), \
                 patch('transport.reconcile_snapshot', side_effect=RuntimeError('fixture read failure')
                       if reconciliation_error else None, return_value='pending'), \
                 patch('transport.subprocess.run') as notice, \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                run(self.config, self.root, once=True)
                if corrupt_receipt:
                    current = read_private(outbox_path)['incidents']
                    prior = json.loads(original_outbox)['incidents']
                    for key, before in prior.items():
                        for field in ('identity', 'attempt', 'status'):
                            self.assertEqual(current[key][field], before[field])
                    self.assertEqual(receipt_path.read_bytes(), original_receipt)
                return notice.call_count

    def test_uncertain_submission_alerts_despite_known_unsent_incident(self):
        self.assertEqual(self.poll_incidents([('a', 'not_attempted', []),
                                             ('b', 'outcome_unknown', [])], concierge_available=False), 1)

    def test_first_submission_without_receipt_check_does_not_alert(self):
        self.assertEqual(self.poll_incidents([('a', 'submitted', [])], concierge_available=False), 0)

    def test_matched_receipt_remains_certain_when_later_check_fails(self):
        self.assertEqual(self.poll_incidents([('concierge-incident', 'submitted', ['receipt_matched'])],
                                             reconciliation_error=True), 0)

    def test_unmatched_receipt_check_alerts(self):
        self.assertEqual(self.poll_incidents([('concierge-incident', 'submitted', [])]), 1)

    def test_unreadable_receipt_store_alerts_for_unmatched_submission(self):
        self.assertEqual(self.poll_incidents([('concierge-incident', 'submitted', [])],
                                             corrupt_receipt=True), 1)

    def test_unreadable_receipt_store_does_not_alert_for_known_unsent(self):
        self.assertEqual(self.poll_incidents([('concierge-incident', 'not_attempted', [])],
                                             corrupt_receipt=True), 0)

    def test_unreadable_receipt_store_does_not_undo_matched_receipt(self):
        self.assertEqual(self.poll_incidents([('concierge-incident', 'submitted', ['receipt_matched'])],
                                             corrupt_receipt=True), 0)

    def test_failed_receipt_check_remains_alertable_after_restart_and_route_loss(self):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        outbox = read_private(outbox_path)
        incident = outbox['incidents']['concierge-incident']
        incident['status'] = 'submitted'
        save(outbox_path, outbox)
        original_identity = copy.deepcopy(incident['identity'])
        original_attempt = copy.deepcopy(incident['attempt'])
        receipt_path = Path(self.config['incidentReceiptStore'])
        receipt_path.write_text('{invalid fixture')
        original_receipt = receipt_path.read_bytes()
        available = {'concierge': True}
        sends = []

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()

            def call(self, tool, args):
                if args['threadId'] == 'concierge' and not available['concierge']:
                    raise NativeBridgeError('native_authorization_rejected', stage='read_thread')
                return super().call(tool, args)

            def send(self, *args):
                sends.append(args)
                raise AssertionError('fixture must not resend')

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('transport.NativeBridge', PollBridge), patch('transport.subprocess.run') as notice, \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                run(self.config, self.root, once=True)
                self.assertEqual(notice.call_count, 1)
                run(self.config, self.root, once=True)
                self.assertEqual(notice.call_count, 1)
                alert_path = self.root / 'transport' / 'emergency-notice.json'
                alert = read_private(alert_path)
                alert['attemptedAt'] = time.time() - 3601
                save(alert_path, alert)
                available['concierge'] = False
                run(self.config, self.root, once=True)
                self.assertEqual(notice.call_count, 2)
        retained = read_private(outbox_path)['incidents']['concierge-incident']
        self.assertEqual(retained['identity'], original_identity)
        self.assertEqual(retained['attempt'], original_attempt)
        self.assertEqual(retained['status'], 'submitted')
        self.assertEqual(receipt_path.read_bytes(), original_receipt)
        self.assertEqual(sends, [])

    def check_mixed_incident_failure(self, *, fail_second_read):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        receipt_path = Path(self.config['incidentReceiptStore'])
        receipt = read_private(receipt_path)
        outbox = read_private(outbox_path)
        older = outbox['incidents']['concierge-incident']
        older['status'] = 'submitted'
        newer = copy.deepcopy(older)
        newer['identity']['incident_id'] = 'newer-incident'
        newer['attempt']['id'] = 'newer-attempt'
        newer['attempt']['payload'] = 'newer-incident newer-attempt'
        outbox['incidents']['newer-incident'] = newer
        available = {'concierge': True}
        sends = []
        receipt_reads = []

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()

            def call(self, tool, args):
                if args['threadId'] == 'concierge' and not available['concierge']:
                    raise NativeBridgeError('native_authorization_rejected', stage='read_thread')
                return super().call(tool, args)

            def send(self, *args):
                sends.append(args)
                raise AssertionError('fixture must not resend')

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        def read_store(path, *args):
            if Path(path) == receipt_path:
                receipt_reads.append(path)
                if fail_second_read and len(receipt_reads) == 2:
                    raise OSError('fixture second receipt read failure')
            return read_private(path, *args)

        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            generation = publish_candidate(self.root, self.config, {'CODEX_APP_TOOLS_PIPE_PATH': route})['generation']
            older['reconciliation'] = [{'stage': 'owned_continuation_matched',
                                         'receiptDigest': evidence_digest(receipt),
                                         'routeGeneration': generation}]
            original_older = copy.deepcopy(older)
            save(outbox_path, outbox)
            if not fail_second_read:
                receipt_path.write_text('{invalid fixture')
            original_receipt = receipt_path.read_bytes()
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('transport.NativeBridge', PollBridge), patch('transport.read_private', side_effect=read_store), \
                 patch('transport.subprocess.run') as notice, \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                run(self.config, self.root, once=True)
                self.assertEqual(len(receipt_reads), 2 if fail_second_read else 1)
                retained = read_private(outbox_path)['incidents']
                self.assertTrue(any(item.get('stage') == 'pending'
                                    for item in retained['newer-incident'].get('reconciliation', [])))
                self.assertEqual(notice.call_count, 1)
                alert_path = self.root / 'transport' / 'emergency-notice.json'
                alert = read_private(alert_path)
                alert['attemptedAt'] = time.time() - 3601
                save(alert_path, alert)
                available['concierge'] = False
                run(self.config, self.root, once=True)
                self.assertEqual(notice.call_count, 2)
        retained = read_private(outbox_path)['incidents']
        self.assertEqual(retained['concierge-incident'], original_older)
        self.assertEqual(retained['newer-incident']['identity'], newer['identity'])
        self.assertEqual(retained['newer-incident']['attempt'], newer['attempt'])
        self.assertEqual(retained['newer-incident']['status'], 'submitted')
        self.assertEqual(receipt_path.read_bytes(), original_receipt)
        self.assertEqual(sends, [])

    def test_mixed_incidents_persist_check_before_unreadable_store(self):
        self.check_mixed_incident_failure(fail_second_read=False)

    def test_mixed_incidents_persist_actual_check_before_second_read_failure(self):
        self.check_mixed_incident_failure(fail_second_read=True)

    def test_pending_receipt_is_reconciled_during_continuing_arrivals(self):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        outbox = read_private(outbox_path)
        older = outbox['incidents']['concierge-incident']
        older['status'] = 'submitted'
        older['reconciliation'] = [{'stage': 'pending'}]
        original_identity = copy.deepcopy(older['identity'])
        original_attempt = copy.deepcopy(older['attempt'])
        fixture_turns = copy.deepcopy(self.native.turns)
        state = {'clock': time.time(), 'cycles': 0}
        sends = []

        def add_incident(index):
            current = read_private(outbox_path)
            row = copy.deepcopy(older)
            row.pop('reconciliation', None)
            incident = f'newer-{index}'
            row['identity']['incident_id'] = incident
            row['attempt']['id'] = incident + '-attempt'
            row['attempt']['payload'] = incident + ' ' + row['attempt']['id']
            current['incidents'][incident] = row
            save(outbox_path, current)

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()
                self.turns = copy.deepcopy(fixture_turns)

            def send(self, *args):
                sends.append(args)
                raise AssertionError('fixture must not resend')

        class FixtureTransport(RealTransport):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, clock=lambda: state['clock'])

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        def next_cycle(_seconds):
            state['cycles'] += 1
            if state['cycles'] == 3:
                raise RuntimeError('fixture-stop')
            add_incident(state['cycles'])
            state['clock'] += 61

        save(outbox_path, outbox)
        add_incident(0)
        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport', FixtureTransport), patch('controller.time.sleep', side_effect=next_cycle), \
                 patch('transport.NativeBridge', PollBridge), patch('transport.subprocess.run'), \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'fixture-stop'):
                    run(self.config, self.root)
        retained = read_private(outbox_path)['incidents']['concierge-incident']
        self.assertTrue(any(item.get('stage') in ('receipt_matched', 'owner_accepted_matched',
                                                   'owned_continuation_matched')
                            for item in retained['reconciliation']))
        self.assertEqual(retained['identity'], original_identity)
        self.assertEqual(retained['attempt'], original_attempt)
        self.assertEqual(retained['status'], 'submitted')
        self.assertEqual(sends, [])

    def test_retained_turn_does_not_check_unmarked_fallback(self):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        receipt_path = Path(self.config['incidentReceiptStore'])
        receipt = read_private(receipt_path)
        original_receipt = receipt_path.read_bytes()
        outbox = read_private(outbox_path)
        older = outbox['incidents']['concierge-incident']
        older['status'] = 'submitted'
        state = {'clock': time.time(), 'cycles': 0, 'reads': 0, 'available': True}
        failed_checks = []
        sends = []

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()

            def call(self, tool, args):
                if args['threadId'] == 'concierge' and not state['available']:
                    raise NativeBridgeError('native_authorization_rejected', stage='read_thread')
                return super().call(tool, args)

            def send(self, *args):
                sends.append(args)
                raise AssertionError('fixture must not resend')

        class FixtureTransport(RealTransport):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, clock=lambda: state['clock'])

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        def read_store(path, *args):
            if Path(path) == receipt_path:
                state['reads'] += 1
                if state['reads'] == 2:
                    current = read_private(outbox_path)['incidents']['newer-incident']
                    failed_checks.append(any(item.get('stage') == 'pending'
                                             for item in current.get('reconciliation', [])))
                    raise OSError('fixture second receipt read failure')
            return read_private(path, *args)

        def next_cycle(_seconds):
            state['cycles'] += 1
            if state['cycles'] == 1:
                current = read_private(outbox_path)
                newer = copy.deepcopy(current['incidents']['concierge-incident'])
                newer.pop('reconciliation', None)
                newer['identity']['incident_id'] = 'newer-incident'
                newer['attempt']['id'] = 'newer-attempt'
                newer['attempt']['payload'] = 'newer-incident newer-attempt'
                current['incidents']['newer-incident'] = newer
                save(outbox_path, current)
            if state['cycles'] == 3:
                raise RuntimeError('fixture-stop')
            state['clock'] += 61
            state['reads'] = 0

        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            generation = publish_candidate(self.root, self.config, {'CODEX_APP_TOOLS_PIPE_PATH': route})['generation']
            older['reconciliation'] = [{'stage': 'owned_continuation_matched',
                                         'receiptDigest': evidence_digest(receipt),
                                         'routeGeneration': generation}]
            original_older = copy.deepcopy(older)
            save(outbox_path, outbox)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport', FixtureTransport), patch('controller.time.sleep', side_effect=next_cycle), \
                 patch('transport.NativeBridge', PollBridge), patch('transport.read_private', side_effect=read_store), \
                 patch('transport.reconcile_snapshot', wraps=reconcile_snapshot) as reconcile, \
                 patch('transport.subprocess.run') as notice, \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'fixture-stop'):
                    run(self.config, self.root)
                self.assertEqual(failed_checks, [True])
                self.assertEqual(reconcile.call_args.args[1]['identity']['incident_id'], 'newer-incident')
                self.assertEqual(notice.call_count, 1)
                alert_path = self.root / 'transport' / 'emergency-notice.json'
                alert = read_private(alert_path)
                alert['attemptedAt'] = state['clock'] - 3601
                save(alert_path, alert)
                state['available'] = False
                run(self.config, self.root, once=True)
                self.assertEqual(notice.call_count, 2)
        retained = read_private(outbox_path)['incidents']
        self.assertEqual(retained['concierge-incident'], original_older)
        self.assertEqual(retained['newer-incident']['status'], 'submitted')
        self.assertEqual(retained['newer-incident']['identity']['incident_id'], 'newer-incident')
        self.assertEqual(retained['newer-incident']['attempt']['id'], 'newer-attempt')
        self.assertEqual(receipt_path.read_bytes(), original_receipt)
        self.assertEqual(sends, [])

    def test_unchecked_incident_is_not_overtaken_by_later_ids(self):
        self.activate()
        outbox_path = self.root / 'transport' / 'incident-outbox.json'
        outbox = read_private(outbox_path)
        retained = outbox['incidents']['concierge-incident']
        retained['status'] = 'submitted'
        retained['reconciliation'] = [{'stage': 'pending'}]
        older = copy.deepcopy(retained)
        older.pop('reconciliation', None)
        older['identity']['incident_id'] = 'z-older'
        older['attempt']['id'] = 'z-older-attempt'
        older['attempt']['payload'] = 'z-older z-older-attempt'
        outbox['incidents']['z-older'] = older
        save(outbox_path, outbox)
        original_identity = copy.deepcopy(older['identity'])
        original_attempt = copy.deepcopy(older['attempt'])
        fixture_turns = copy.deepcopy(self.native.turns)
        state = {'clock': time.time(), 'cycles': 0}
        sends = []

        def add_incident(index):
            current = read_private(outbox_path)
            row = copy.deepcopy(older)
            incident = f'a-new-{index}'
            row['identity']['incident_id'] = incident
            row['attempt']['id'] = incident + '-attempt'
            row['attempt']['payload'] = incident + ' ' + row['attempt']['id']
            current['incidents'][incident] = row
            save(outbox_path, current)

        class PollBridge(Readback):
            def __init__(self, config, pipe):
                super().__init__()
                self.turns = copy.deepcopy(fixture_turns)

            def send(self, *args):
                sends.append(args)
                raise AssertionError('fixture must not resend')

        class FixtureTransport(RealTransport):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, clock=lambda: state['clock'])

        def read(account, **kwargs):
            value = sample(28)
            if account['name'] == 'daybreak':
                value.update(email=account['email'], accountId=account['accountId'])
            return value

        def next_cycle(_seconds):
            state['cycles'] += 1
            if state['cycles'] == 5:
                raise RuntimeError('fixture-stop')
            add_incident(state['cycles'])
            state['clock'] += 61

        add_incident(0)
        with socket.socket(socket.AF_UNIX) as fixture_socket:
            route = str(self.root / 'poll.sock')
            fixture_socket.bind(route)
            with patch('controller.read_account', side_effect=read), \
                 patch('controller.default_identity', return_value={k: ACCOUNT[k] for k in ('email', 'accountId')}), \
                 patch('controller.Transport', FixtureTransport), patch('controller.time.sleep', side_effect=next_cycle), \
                 patch('transport.NativeBridge', PollBridge), \
                 patch('transport.reconcile_snapshot', wraps=reconcile_snapshot) as reconcile, \
                 patch('transport.subprocess.run'), \
                 patch.dict(os.environ, {'CODEX_APP_TOOLS_PIPE_PATH': route}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'fixture-stop'):
                    run(self.config, self.root)
        checked = [call.args[1]['identity']['incident_id'] for call in reconcile.call_args_list]
        self.assertIn('z-older', checked)
        self.assertEqual(checked[0], 'z-older')
        current = read_private(outbox_path)['incidents']['z-older']
        self.assertTrue(any(item.get('stage') == 'pending' for item in current['reconciliation']))
        self.assertEqual(current['identity'], original_identity)
        self.assertEqual(current['attempt'], original_attempt)
        self.assertEqual(current['status'], 'submitted')
        self.assertEqual(sends, [])
