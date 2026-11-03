"""Offline candidate: retain uncertain incident submissions without replay."""
import json
from pathlib import Path

from storage import read_private, save


def match_submission(bridge, identity, attempt, receipt):
    """Match a native message; this does not authenticate its sender or prove nondelivery."""
    if (attempt['target'] != identity['recipient'] or attempt['target_host'] != identity['recipient_host']
            or receipt['host'] != identity['recipient_host'] or identity['incident_id'] not in attempt['payload']
            or attempt['id'] not in attempt['payload']):
        return False
    response = bridge.call('read_thread', {'threadId': identity['recipient'], 'turnLimit': 30,
                                           'includeOutputs': False, 'maxOutputCharsPerItem': len(attempt['payload']) + 1024})
    if not isinstance(response, dict):
        return False
    thread = response.get('thread', {})
    if (not isinstance(thread, dict) or thread.get('id') != identity['recipient']
            or thread.get('hostId') != receipt['host'] or not isinstance(response.get('turns'), list)):
        return False
    for turn in response.get('turns', []):
        if not isinstance(turn, dict) or turn.get('id') != receipt['turn'] or not isinstance(turn.get('items'), list):
            continue
        for item in turn.get('items', []):
            if not isinstance(item, dict) or item.get('id') != receipt['message'] or item.get('type') != 'userMessage':
                continue
            content = item.get('content', [])
            if content == [{'type': 'text', 'text': attempt['payload']}]:
                return True
    return False


def verify_ack(bridge, target, reference, incident_id, attempt_id, kind, *, next_action=None):
    """Match a versioned exact assistant acknowledgment; no match is not nondelivery."""
    if kind not in ('receipt', 'owner_accepted', 'continuation'):
        raise ValueError('invalid_acknowledgment_kind')
    if not all(isinstance(value, str) and value for value in (incident_id, attempt_id)):
        raise ValueError('invalid_acknowledgment_identity')
    data = {'incident': incident_id, 'attempt': attempt_id, 'kind': kind}
    if kind == 'continuation':
        if not isinstance(next_action, str) or not next_action.strip():
            raise ValueError('continuation_requires_next_action')
        data['next_action'] = next_action
    elif next_action is not None:
        raise ValueError('unexpected_next_action')
    line = 'QUOTA-ACK/1 ' + json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    response = bridge.call('read_thread', {'threadId': target['thread'], 'turnLimit': 30,
                                           'includeOutputs': False, 'maxOutputCharsPerItem': len(line) + 1024})
    if not isinstance(response, dict) or not isinstance(response.get('thread'), dict):
        return False
    thread = response['thread']
    if thread.get('id') != target['thread'] or thread.get('hostId') != target['host']:
        return False
    turns = response.get('turns')
    if not isinstance(turns, list):
        return False
    matches = 0
    for turn in turns:
        if not isinstance(turn, dict) or turn.get('id') != reference['turn'] or not isinstance(turn.get('items'), list):
            continue
        for item in turn['items']:
            if (isinstance(item, dict) and item.get('id') == reference['message']
                    and item.get('type') == 'agentMessage' and item.get('text') == line):
                matches += 1
    return matches == 1


def reconcile_receipts(bridge, journal, origin, expected_owner):
    """Read-only reconciliation; assertions need exact native assistant readback."""
    if (not isinstance(origin, dict) or origin.get('status') not in ('submitted', 'outcome_unknown')
            or not isinstance(origin.get('identity'), dict) or not isinstance(origin.get('attempt'), dict)):
        return 'pending'
    incident_id = origin['identity'].get('incident_id')
    attempt_id = origin['attempt'].get('id')
    if not all(isinstance(value, str) and value for value in (incident_id, attempt_id)):
        return 'pending'
    record = journal.read(incident_id)
    if (not isinstance(record, dict) or not isinstance(record.get('identity'), dict)
            or record['identity'].get('incident_id') != incident_id
            or not isinstance(record.get('attempts'), list) or not isinstance(record.get('observations'), list)
            or not all(isinstance(item, dict) for item in record['attempts'] + record['observations'])):
        return 'pending'
    attempts = [item for item in record.get('attempts', []) if item.get('id') == attempt_id]
    if len(attempts) != 1 or record['identity'] != origin['identity'] or attempts[0] != origin['attempt']:
        return 'pending'
    identity = record['identity']
    if not all(isinstance(identity.get(key), str) and identity[key] for key in ('recipient', 'recipient_host')):
        return 'pending'
    if (attempts[0].get('target') != identity['recipient'] or
            attempts[0].get('target_host') != identity['recipient_host'] or
            not isinstance(expected_owner, dict) or not all(
                isinstance(expected_owner.get(key), str) and expected_owner[key] for key in ('thread', 'host'))):
        return 'pending'
    observations = record.get('observations', [])
    matched_stage = 'pending'
    for kind, target in (
        ('received', {'thread': identity['recipient'], 'host': identity['recipient_host']}),
        ('owner_accepted', expected_owner),
        ('continuation', expected_owner),
    ):
        candidates = [item for item in observations if item.get('kind') == kind and item.get('attempt_id') == attempt_id]
        if len(candidates) != 1:
            return matched_stage
        item = candidates[0]
        if item.get('actor') != target['thread'] or item.get('host') != target['host']:
            return matched_stage
        if kind == 'received':
            if not all(isinstance(item.get(key), str) and item[key]
                       for key in ('submission_turn', 'submission_message')):
                return matched_stage
            submission = {'host': target['host'], 'turn': item['submission_turn'],
                          'message': item['submission_message']}
            try:
                if not match_submission(bridge, identity, attempts[0], submission):
                    return matched_stage
            except Exception:
                return matched_stage
        if not all(isinstance(item.get(key), str) and item[key] for key in ('turn', 'message')):
            return matched_stage
        reference = {'turn': item['turn'], 'message': item['message']}
        ack_kind = 'receipt' if kind == 'received' else kind
        try:
            matched = verify_ack(bridge, target, reference, incident_id, attempt_id, ack_kind,
                                 next_action=item.get('next_action'))
        except Exception:
            return matched_stage
        if not matched:
            return matched_stage
        matched_stage = {'received': 'receipt_matched', 'owner_accepted': 'owner_accepted_matched',
                         'continuation': 'owned_continuation_matched'}[kind]
    return matched_stage


def reconcile_from_files(state_dir, config, incident_id, bridge):
    """Read existing private records; absence and unreadability remain pending."""
    try:
        outbox = read_private(Path(state_dir) / 'transport' / 'incident-outbox.json')
        if not isinstance(outbox, dict) or outbox.get('version') != 1 or not isinstance(outbox.get('incidents'), dict):
            return 'pending'
        origin = outbox['incidents'].get(incident_id)
        snapshot = read_private(config['incidentReceiptStore'])
        return reconcile_snapshot(bridge, origin, snapshot, config['roleBindings']['repairOwner'])
    except (OSError, ValueError, KeyError, TypeError):
        return 'pending'


def reconcile_snapshot(bridge, origin, snapshot, owner):
    """Reconcile against one immutable in-memory receipt snapshot."""
    if (not isinstance(snapshot, dict) or snapshot.get('version') != 1
            or not isinstance(snapshot.get('incidents'), dict) or not isinstance(owner, dict)):
        return 'pending'
    class Snapshot:
        def read(self, incident_id):
            return snapshot['incidents'].get(incident_id)
    return reconcile_receipts(bridge, Snapshot(), origin,
                              {'thread': owner.get('thread_id'), 'host': owner.get('host_id')})


def handoff_fault(path, identity, attempt, bridge, *, context=None):
    """A successful submission is not receiver acknowledgment or repair acceptance."""
    state = read_private(path, {'version': 1, 'incidents': {}})
    if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('incidents'), dict):
        raise ValueError('invalid_incident_outbox')
    incident_id = identity['incident_id']
    if (attempt['target'] != identity['recipient'] or attempt['target_host'] != identity['recipient_host']
            or attempt['caller'] != identity['sender'] or attempt['caller_host'] != identity['sender_host']
            or incident_id not in attempt['payload']
            or attempt['id'] not in attempt['payload']):
        raise ValueError('attempt_correlation_mismatch')
    old = state['incidents'].get(incident_id)
    for other_id, other in state['incidents'].items():
        if other_id != incident_id and other['attempt']['id'] == attempt['id']:
            raise ValueError('attempt_identity_conflict')
    if old is not None:
        if old['identity'] != identity or old['attempt'] != attempt:
            raise ValueError('incident_identity_conflict')
        if old['status'] != 'not_attempted':
            return 'reconciliation_required'
    else:
        state['incidents'][incident_id] = {'identity': identity, 'attempt': attempt, 'status': 'not_attempted',
                                           'context': context}
        save(path, state)
    if bridge is None:
        return 'waiting_for_route'
    state['incidents'][incident_id]['status'] = 'outcome_unknown'
    save(path, state)
    try:
        bridge.send(attempt['target'], attempt['payload'])
    except Exception:
        return 'outcome_unknown'
    state['incidents'][incident_id]['status'] = 'submitted'
    save(path, state)
    return 'submitted'
