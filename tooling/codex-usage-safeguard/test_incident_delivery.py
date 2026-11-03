"""Fixture-only incident handoff; no native app or live state."""
from pathlib import Path
import tempfile
import unittest
import json
import copy
from unittest.mock import Mock, patch

from incident_delivery import handoff_fault, match_submission, verify_ack, reconcile_receipts, reconcile_from_files


class IncidentDeliveryTests(unittest.TestCase):
    def test_missing_receipt_store_remains_pending_without_native_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'incidentReceiptStore': str(root / 'missing.json'),
                      'roleBindings': {'repairOwner': {'thread_id': 'owner-1', 'host_id': 'local'}}}
            bridge = Mock()
            (root / 'transport').mkdir(mode=0o700)
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local',
                        'recipient': 'recipient-1', 'recipient_host': 'local'}
            attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1',
                       'target_host': 'local', 'payload': 'incident-1 attempt-1'}
            handoff_fault(root / 'transport' / 'incident-outbox.json', identity, attempt, None)
            self.assertEqual(reconcile_from_files(root, config, 'incident-1', bridge), 'pending')
            bridge.call.assert_not_called()

    def test_owned_continuation_requires_each_bound_assistant_ack(self):
        identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local',
                    'recipient': 'recipient-1', 'recipient_host': 'local'}
        attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1',
                   'target_host': 'local', 'payload': 'incident-1 attempt-1'}
        observations = [
            {'kind': 'received', 'attempt_id': 'attempt-1', 'actor': 'recipient-1', 'host': 'local',
             'turn': 'receipt-turn', 'message': 'receipt-message', 'submission_turn': 'send-turn',
             'submission_message': 'send-message'},
            {'kind': 'owner_accepted', 'attempt_id': 'attempt-1', 'actor': 'owner-1', 'host': 'local',
             'turn': 'owner-turn', 'message': 'owner-message'},
            {'kind': 'continuation', 'attempt_id': 'attempt-1', 'actor': 'owner-1', 'host': 'local',
             'turn': 'next-turn', 'message': 'next-message', 'next_action': 'inspect incident'},
        ]
        journal = Mock()
        journal.read.return_value = {'identity': identity, 'attempts': [attempt], 'observations': observations}
        origin = {'identity': copy.deepcopy(identity), 'attempt': copy.deepcopy(attempt), 'status': 'submitted'}
        bridge = Mock()
        def read(_tool, args):
            thread = args['threadId']
            if thread == 'recipient-1':
                rows = [('receipt-turn', 'receipt-message', 'receipt', None)]
            else:
                rows = [('owner-turn', 'owner-message', 'owner_accepted', None),
                        ('next-turn', 'next-message', 'continuation', 'inspect incident')]
            turns = []
            if thread == 'recipient-1':
                turns.append({'id': 'send-turn', 'items': [{'id': 'send-message', 'type': 'userMessage',
                              'content': [{'type': 'text', 'text': 'incident-1 attempt-1'}]}]})
            for turn, message, kind, action in rows:
                lines = {
                    'receipt': 'QUOTA-ACK/1 {"attempt":"attempt-1","incident":"incident-1","kind":"receipt"}',
                    'owner_accepted': 'QUOTA-ACK/1 {"attempt":"attempt-1","incident":"incident-1","kind":"owner_accepted"}',
                    'continuation': 'QUOTA-ACK/1 {"attempt":"attempt-1","incident":"incident-1","kind":"continuation","next_action":"inspect incident"}',
                }
                line = lines[kind]
                turns.append({'id': turn, 'items': [{'id': message, 'type': 'agentMessage', 'text': line}]})
            return {'thread': {'id': thread, 'hostId': 'local'}, 'turns': turns}
        bridge.call.side_effect = read
        self.assertEqual(reconcile_receipts(bridge, journal, origin,
                                            {'thread': 'owner-1', 'host': 'local'}), 'owned_continuation_matched')
        journal.read.return_value['attempts'][0]['target'] = 'other-recipient'
        self.assertEqual(reconcile_receipts(bridge, journal, origin,
                                            {'thread': 'owner-1', 'host': 'local'}), 'pending')
        journal.read.return_value['attempts'][0]['target'] = 'recipient-1'
        del observations[0]['submission_message']
        self.assertEqual(reconcile_receipts(bridge, journal, origin,
                                            {'thread': 'owner-1', 'host': 'local'}), 'pending')
        observations[0]['submission_message'] = 'send-message'
        self.assertEqual(reconcile_receipts(bridge, journal, origin,
                                            {'thread': 'other-owner', 'host': 'local'}), 'receipt_matched')

    def test_journal_assertion_without_native_ack_stays_pending(self):
        identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local',
                    'recipient': 'recipient-1', 'recipient_host': 'local'}
        attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1',
                   'target_host': 'local', 'payload': 'incident-1 attempt-1'}
        received = {'id': 'obs-1', 'kind': 'received', 'attempt_id': 'attempt-1', 'evidence': 'untrusted',
                    'actor': 'recipient-1', 'host': 'local', 'turn': 'turn-1', 'message': 'message-1'}
        journal = Mock()
        journal.read.return_value = {'identity': identity, 'attempts': [attempt], 'observations': [received]}
        origin = {'identity': copy.deepcopy(identity), 'attempt': copy.deepcopy(attempt), 'status': 'submitted'}
        bridge = Mock()
        bridge.call.return_value = {'thread': {'id': 'recipient-1', 'hostId': 'local'}, 'turns': []}
        result = reconcile_receipts(bridge, journal, origin,
                                    {'thread': 'repair-owner', 'host': 'local'})
        self.assertEqual(result, 'pending')
        journal.read.return_value['observations'] = [None]
        self.assertEqual(reconcile_receipts(bridge, journal, origin,
                                            {'thread': 'repair-owner', 'host': 'local'}), 'pending')

    def test_attempt_identity_cannot_be_reused_for_another_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outbox.json'
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local',
                        'recipient': 'recipient-1', 'recipient_host': 'local'}
            attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1',
                       'target_host': 'local', 'payload': 'incident-1 attempt-1'}
            self.assertEqual(handoff_fault(path, identity, attempt, None), 'waiting_for_route')
            with self.assertRaises(ValueError):
                handoff_fault(path, {**identity, 'incident_id': 'incident-2'},
                              {**attempt, 'payload': 'incident-2 attempt-1'}, None)

    def test_crash_after_send_preserves_unknown_and_does_not_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outbox.json'
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'}
            attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1', 'target_host': 'local', 'payload': 'incident-1 attempt-1'}
            bridge = Mock()
            from storage import save
            count = 0
            def interrupted_save(target, value):
                nonlocal count
                count += 1
                if count == 3:
                    raise OSError('interrupted-after-send')
                save(target, value)
            with patch('incident_delivery.save', side_effect=interrupted_save):
                with self.assertRaises(OSError):
                    handoff_fault(path, identity, attempt, bridge)
            self.assertEqual(handoff_fault(path, identity, attempt, bridge), 'reconciliation_required')
            self.assertEqual(bridge.send.call_count, 1)

    def test_known_unsent_incident_survives_outage_and_sends_when_route_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outbox.json'
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'}
            attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1', 'target_host': 'local', 'payload': 'incident-1 attempt-1'}
            self.assertEqual(handoff_fault(path, identity, attempt, None, context={'reason': 'route unavailable'}), 'waiting_for_route')
            bridge = Mock()
            self.assertEqual(handoff_fault(path, identity, attempt, bridge), 'submitted')
            bridge.send.assert_called_once_with('recipient-1', 'incident-1 attempt-1')
            self.assertEqual(json.loads(path.read_text())['incidents']['incident-1']['context'],
                             {'reason': 'route unavailable'})

    def test_native_receipt_requires_target_attempt_and_message_to_match(self):
        bridge = Mock()
        identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'}
        attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1', 'target_host': 'local', 'payload': 'incident-1 attempt-1'}
        receipt = {'host': 'local', 'turn': 'turn-1', 'message': 'message-1'}
        bridge.call.return_value = {'thread': {'id': 'recipient-1', 'hostId': 'local'}, 'turns': [
            {'id': 'turn-1', 'items': [{'id': 'message-1', 'type': 'userMessage',
                                       'content': [{'type': 'text', 'text': 'incident-1 attempt-1'}]}]}]}
        self.assertTrue(match_submission(bridge, identity, attempt, receipt))
        bridge.call.return_value['thread']['hostId'] = 'other'
        self.assertFalse(match_submission(bridge, identity, attempt, receipt))
        bridge.call.return_value = {'thread': {'id': 'recipient-1', 'hostId': 'local'}, 'turns': [None]}
        self.assertFalse(match_submission(bridge, identity, attempt, receipt))

    def test_acknowledgment_requires_exact_assistant_message_and_bound_identity(self):
        bridge = Mock()
        target = {'thread': 'recipient-1', 'host': 'local'}
        reference = {'turn': 'turn-1', 'message': 'message-1'}
        line = 'QUOTA-ACK/1 {"attempt":"attempt-1","incident":"incident-1","kind":"receipt"}'
        item = {'id': 'message-1', 'type': 'agentMessage', 'text': line}
        bridge.call.return_value = {'thread': {'id': 'recipient-1', 'hostId': 'local'},
                                    'turns': [{'id': 'turn-1', 'items': [item]}]}
        self.assertTrue(verify_ack(bridge, target, reference, 'incident-1', 'attempt-1', 'receipt'))
        item['type'] = 'userMessage'
        self.assertFalse(verify_ack(bridge, target, reference, 'incident-1', 'attempt-1', 'receipt'))
        item['type'] = 'agentMessage'
        self.assertFalse(verify_ack(bridge, target, reference, 'incident-1', 'attempt-2', 'receipt'))
        bridge.call.return_value['thread']['hostId'] = 'other'
        self.assertFalse(verify_ack(bridge, target, reference, 'incident-1', 'attempt-1', 'receipt'))
        bridge.call.return_value['thread']['hostId'] = 'local'
        bridge.call.return_value['turns'][0]['items'].append(dict(item))
        self.assertFalse(verify_ack(bridge, target, reference, 'incident-1', 'attempt-1', 'receipt'))

    def test_submitted_is_not_receipt_and_is_not_automatically_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outbox.json'
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'}
            attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1', 'target_host': 'local', 'payload': 'incident-1 attempt-1'}
            bridge = Mock()
            self.assertEqual(handoff_fault(path, identity, attempt, bridge), 'submitted')
            self.assertEqual(handoff_fault(path, identity, attempt, bridge), 'reconciliation_required')
            bridge.send.assert_called_once_with('recipient-1', 'incident-1 attempt-1')

    def test_uncertain_submission_is_not_replayed_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipts.json'
            identity = {'incident_id': 'incident-1', 'sender': 'sender-1', 'sender_host': 'local', 'recipient': 'recipient-1', 'recipient_host': 'local'}
            attempt = {'id': 'attempt-1', 'caller': 'sender-1', 'caller_host': 'local', 'target': 'recipient-1', 'target_host': 'local', 'payload': 'incident-1 attempt-1'}
            bridge = Mock()
            bridge.send.side_effect = TimeoutError('uncertain')
            self.assertEqual(handoff_fault(path, identity, attempt, bridge), 'outcome_unknown')
            self.assertEqual(handoff_fault(path, identity, attempt, bridge), 'reconciliation_required')
            self.assertEqual(bridge.send.call_count, 1)


if __name__ == '__main__':
    unittest.main()
