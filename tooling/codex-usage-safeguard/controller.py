#!/usr/bin/env python3
"""Single local service: fresh reads, durable intents, native Steer dispatch."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import datetime as dt
import fcntl
import hashlib
import json
import os
import base64
from pathlib import Path
import subprocess
import stat
import time
import uuid

from safeguard import advance, settle, observation_failed, fallback_eligible, decimal, invalidate_daybreak_pauses, exhausted, number
from transport import Transport, publish_candidate
from activation import delivery_status, decision_lock, reset_operation_lock, ResetOperationBusy
from storage import private_directory, read_private, save
from configuration import load_config, runtime_arguments

ROOT = Path(__file__).resolve().parent
NODE = 'node'


class AccountStateMissing(ValueError):
    """An account ledger is missing despite surviving initialization evidence."""


def check_account_custody(path):
    name = path.stem
    if name not in ('main', 'daybreak'):
        return
    marker_path = path.parent / f'account-custody-{name}.json'
    absent = object()
    try:
        marker = read_private(marker_path, absent)
    except (OSError, ValueError):
        raise AccountStateMissing('account_state_reconciliation_required') from None
    if marker is not absent and (marker != {'account': name, 'initialized': True} or not path.exists()):
        raise AccountStateMissing('account_state_reconciliation_required')
    if path.exists():
        try:
            state = read_private(path)
            if not isinstance(state, dict) or not isinstance(state.get('accountId'), str) or not state['accountId']:
                raise ValueError('invalid_account_state')
            episode = state.get('episode')
            if episode is not None:
                if (not isinstance(episode, dict) or not isinstance(episode.get('id'), str)
                        or episode.get('accountId') != state['accountId']
                        or not isinstance(episode.get('active'), bool)
                        or not isinstance(episode.get('resetIdempotencyKey'), str)
                        or not isinstance(episode.get('events'), list)):
                    raise ValueError('invalid_account_episode')
                for event in episode['events']:
                    if (not isinstance(event, dict) or not isinstance(event.get('id'), str)
                            or not isinstance(event.get('kind'), str)
                            or ('deliveries' in event and not isinstance(event['deliveries'], dict))):
                        raise ValueError('invalid_account_event')
                    if (event['kind'] not in ('foreman_pause_required', 'reset_confirmation_required', 'reset_availability_unknown')
                            or not event['id'].startswith(episode['id'] + ':' + event['kind'])):
                        raise ValueError('invalid_account_event')
                    for recipient, delivery in event.get('deliveries', {}).items():
                        if (recipient not in ('foreman', 'parent') or not isinstance(delivery, dict)
                                or delivery.get('status') not in ('attempting', 'outcome_unknown', 'submitted')):
                            raise ValueError('invalid_account_delivery')
        except (OSError, ValueError, TypeError):
            raise AccountStateMissing('account_state_reconciliation_required') from None
    if marker is absent and not path.exists():
        if (name == 'main' and (path.parent / 'main-selection.json').exists()) or (
                name == 'daybreak' and any(path.parent.glob('reset-*.json'))):
            raise AccountStateMissing('account_state_reconciliation_required')
        if name == 'daybreak':
            try:
                histories = [path.parent / 'main.json']
                archive = path.parent / 'account-history'
                if archive.exists():
                    histories.extend(archive.glob('*.json'))
                for history in histories:
                    main = read_private(history, {})
                    episode = main.get('episode') if isinstance(main, dict) else None
                    if isinstance(episode, dict) and str(episode.get('supersededBy', '')).startswith('daybreak:'):
                        raise AccountStateMissing('account_state_reconciliation_required')
            except AccountStateMissing:
                raise
            except (OSError, ValueError):
                raise AccountStateMissing('account_state_reconciliation_required') from None


def save_account(path, state):
    path = Path(path)
    check_account_custody(path)
    if path.stem in ('main', 'daybreak'):
        marker_path = path.parent / f'account-custody-{path.stem}.json'
        if not marker_path.exists():
            save(marker_path, {'account': path.stem, 'initialized': True})
    save(path, state)


def reset_evidence(state_dir, state):
    """Identify the episode's reset record while the reset lock is held."""
    episode = state.get('episode') or {}
    episode_id = episode.get('id')
    if episode_id is None:
        if state.get('resetEvidence') is not None:
            raise ValueError('reset_reconciliation_required')
        return None
    if state.get('resetEvidence') is not None and state.get('resetEvidenceEpisode', episode_id) != episode_id:
        raise ValueError('reset_reconciliation_required')
    if str(uuid.UUID(episode_id)) != episode_id:
        raise ValueError('invalid_episode_id')
    absent = object()
    record = read_private(state_dir / f'reset-{episode_id}.json', absent)
    marker = read_private(state_dir / f'reset-custody-{episode_id}.json', absent)
    expected = {'episodeId': episode_id, 'accountId': state.get('accountId'),
                'idempotencyKey': episode.get('resetIdempotencyKey')}
    if marker is not absent:
        if marker != expected or record is absent:
            raise ValueError('reset_reconciliation_required')
    if record is absent:
        if state.get('resetEvidence') is not None:
            raise ValueError('reset_reconciliation_required')
        return None
    if not isinstance(record, dict) or not record:
        raise ValueError('reset_reconciliation_required')
    request = record.get('request')
    if not isinstance(request, dict) or any(request.get(k) != v for k, v in expected.items()):
        raise ValueError('reset_reconciliation_required')
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if state.get('resetEvidence') is not None and state['resetEvidence'] != digest:
        raise ValueError('reset_reconciliation_required')
    return digest


def read_account(account, mode='usage', node=NODE):
    env = dict(os.environ)
    # This process never uses CLI fallback accounts or starts inference.
    env.pop('CODEX_HOME', None)
    selection = ['--default', mode] if account.get('dynamicDefault') else [
        account['authFile'], account['email'], account['accountId'], mode]
    result = subprocess.run([node, str(ROOT / 'read_quota.mjs'), *selection],
                            env=env, capture_output=True, text=True, timeout=65 if mode != 'usage' else 35)
    try:
        output = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('quota_reader_failed') from None
    if result.returncode:
        raise RuntimeError(output.get('error', 'quota_reader_failed'))
    return output


def default_identity():
    """Cheap local switch/race check; live attribution still requires server validation."""
    tokens = json.loads((Path.home() / '.codex' / 'auth.json').read_text())['tokens']
    claims = json.loads(base64.urlsafe_b64decode(tokens['access_token'].split('.')[1] + '==='))
    email = claims.get('email') or claims.get('https://api.openai.com/profile', {}).get('email')
    return {'email': email, 'accountId': tokens.get('account_id')}


def check_main_selection(root, state=None):
    """Validate the persisted main identity without reading credentials or writing."""
    old = read_private(root / 'main.json', {}) if state is None else state
    absent = object()
    selected = read_private(root / 'main-selection.json', absent)
    present = selected is not absent
    if not present:
        selected = {}
    if not isinstance(old, dict) or not isinstance(selected, dict):
        raise ValueError('main_selection_state_mismatch')
    episode = old.get('episode') or {}
    sample = old.get('lastSample') or {}
    if not isinstance(episode, dict) or not isinstance(sample, dict):
        raise ValueError('main_selection_state_mismatch')
    if not present and not old.get('identityGeneration') and episode.get('active'):
        raise ValueError('main_selection_adoption_required')
    # A predecessor with neither generation nor selector can be adopted. Once
    # either side exists, both sides must agree before a new identity is selected.
    if present or old.get('identityGeneration'):
        if (not isinstance(old.get('identityGeneration'), str) or not old['identityGeneration']
                or selected.get('generation') != old['identityGeneration']
                or selected.get('accountId') != old.get('accountId')
                or not isinstance(selected.get('email'), str) or not selected['email']
                or (episode.get('email') is not None and selected['email'] != episode['email'])
                or (sample and any(sample.get(key) != selected.get(key) for key in ('accountId', 'email')))):
            raise ValueError('main_selection_state_mismatch')
    return old, selected


def select_main_identity(root, template, reading):
    """Commit a server-validated main-account transition before classifying quota."""
    path = root / 'main.json'
    selector = root / 'main-selection.json'
    old, selected = check_main_selection(root)
    identity = {'accountId': reading['accountId'], 'email': reading['email']}
    changed = (selected.get('accountId'), selected.get('email')) != (identity['accountId'], identity['email'])
    if changed:
        if old:
            archive = root / 'account-history'; archive.mkdir(mode=0o700, exist_ok=True)
            generation = old.get('identityGeneration') or str(uuid.uuid4())
            old.update(supersededBy=identity, pendingApprovalsInvalidated=True)
            save(archive / f'{generation}.json', old)
        selected = {**identity, 'generation': str(uuid.uuid4()), 'observedAt': reading['observedAt']}
        # State first: an interrupted selector transition requires reconciliation;
        # it never attributes an old episode to the newly selected account.
        old = {'accountId': identity['accountId'], 'identityGeneration': selected['generation']}
        save_account(path, old)
        save(selector, selected)
    elif old.get('accountId') != identity['accountId'] or old.get('identityGeneration') != selected['generation']:
        raise ValueError('main_selection_state_mismatch')
    return {**template, **identity, 'identityGeneration': selected['generation']}, old


def adopt_main_episode(root, main):
    """Transfer a matching episode without discarding either ledger's receipts."""
    episode = main.get('episode') or {}
    if not episode.get('active'):
        return main
    path = root / 'daybreak.json'
    existing = read_private(path)
    if existing is not None:
        if not isinstance(existing, dict) or existing.get('episode') != episode:
            raise ValueError('canonical_episode_reconciliation_required')
    else:
        migrated = copy.deepcopy(main)
        migrated.pop('identityGeneration', None)
        migrated.pop('delegatedTo', None)
        save_account(path, migrated)
    main = copy.deepcopy(main)
    main['episode']['active'] = False
    main['episode']['supersededBy'] = 'daybreak:' + episode['id']
    return main


def make_prompt(event, episode, account, state_path, recipient, *, new_routing=False):
    common = (f"Quota safeguard event {event['id']} for {account['name']} account "
              f"{account['email']} ({account['accountId']}). Core Codex quota first reached "
              f"0% at {episode['observedAt']}. Deduplicate by episode {episode['id']}. "
              f"Read {state_path} and {ROOT / 'OPERATING.md'} before acting. ")
    if account.get('dynamicDefault'):
        common += ("This main-account event is valid only while main-selection.json retains "
                   f"identity generation {account['identityGeneration']}. Before asking, pausing or "
                   "redeeming, verify that generation, current default credentials and app identity "
                   "still match this account. If superseded, do not act on this old notification. ")
    if event['kind'] == 'foreman_pause_required':
        if event.get('reason') == 'no_applicable_reset' and account['name'] == 'daybreak':
            common += ("This no-reset pause is authorized ONLY because this account was also the "
                       "current default main account. Recheck default credentials immediately before "
                       "acting; if it is no longer main, do not issue this no-reset pause. ")
        if recipient == 'parent':
            coordinator = ('Codex Dispatch coordinates the pause. ' if new_routing else
                           'The named Codex Foreman is the sole pause coordinator. ')
            return (common + f"Pause condition: {event['reason']}. " + coordinator +
                    "Inspect the recorded coordinator delivery status before "
                    "reporting delivery; submitted does not mean acknowledged or stopped. Notify "
                    "Ivan in the main chat, reconcile missing/uncertain delivery by reading the "
                    "coordinator thread, and do not create a second pause coordinator or replay this "
                    f"episode. Applicable reset count at detection: {episode['resetCount']!r}; "
                    f"saved reset idempotency key: {episode['resetIdempotencyKey']}. "
                    "If a reset is available, ask for explicit confirmation of that specific "
                    "reset for this account unless that question is already pending; never ask "
                    "twice for the same episode. No reset, account switch or automatic "
                    "resume is authorized by this event. The local observer continues without "
                    "model polling. Read OPERATING.md for the reset and release procedure.")
        role = 'Codex Dispatch: ' if new_routing else ''
        return (common + role + f"Pause reason: {event['reason']}. Ivan authorized this conditional protective "
                "pause. Verify the event reason against policy in config.json and a fresh matching-account "
                "read where available. An outage alone permits a pause only if the policy explicitly "
                "records Ivan's approval for that fallback; do not assume such approval. "
                "Then use native Steer to issue pause "
                "orders to all currently active Codex root threads, including Codex work using "
                "another account; preserve work, relay to their owned Codex children and track "
                "actual safe-stop acknowledgments and continuation wakes. Never count delivery as "
                "stopped. Keep Claude-only work running. Use your established limited Claude-Foreman "
                "relay only for Claude-managed Codex work. Do not replay historical episodes. "
                "No reset or purchase is authorized by this event. Notify Ivan through the current "
                "parent. Do not resume paused work automatically. No hard dollar cap is guaranteed.")
    return (common + f"Reset applicability count at detection: {episode['resetCount']!r}. "
            "Wake the current parent to request explicit human confirmation in Ivan's main chat "
            "for ONE reset for this exact account and episode, after refreshing applicable reset "
            "availability. This standing monitoring request is not redemption confirmation. Use "
            f"idempotency key {episode['resetIdempotencyKey']} for this logical attempt. "
            "For main, use the native consume_usage_reset tool only after confirming the app is "
            "still on this account. For Daybreak, the app's main-account reset tool targets the "
            "wrong account; use only the identity-checked profile procedure in OPERATING.md after "
            "specific confirmation. Never switch accounts, renew credentials, buy credits or "
            "resets, change permissions, or invent a dollar conversion. Ask once and return; the "
            "local watcher tracks the waiting condition without model polling. If availability "
            "is unknown, describe it as unknown and investigate once. Preserve independent holds.")


def dispatch(state, account, config, bridge, path, *, current_observation=None,
             fallback_event_ids=(), delivery_guard=None, delivery_lock=None):
    episode = state.get('episode')
    if not episode or not episode.get('active'):
        return
    if account.get('dynamicDefault'):
        selected = json.loads((path.parent / 'main-selection.json').read_text())
        if selected.get('generation') != account.get('identityGeneration') or default_identity() != {
                'email': account['email'], 'accountId': account['accountId']}:
            raise RuntimeError('main_identity_changed_before_dispatch')
    failures = []
    # A protective pause must not wait behind the approval notification.
    events = sorted(episode['events'], key=lambda e: e['kind'] != 'foreman_pause_required')
    for event in events:
        if event.get('invalidated'):
            if any(d['status'] != 'submitted' for d in event.get('deliveries', {}).values()):
                failures.append('delivery_outcome_requires_reconciliation')
            continue
        targets = [('parent', config['parentThreadId'])]
        if event['kind'] == 'foreman_pause_required':
            targets.insert(0, ('foreman', config['foremanThreadId']))
        for key, target in targets:
            if (account['name'] == 'daybreak' and event['kind'] == 'foreman_pause_required'
                    and event.get('reason') == 'no_applicable_reset'):
                current = default_identity() == {'email': account['email'], 'accountId': account['accountId']}
                if invalidate_daybreak_pauses(episode, dict(account, alsoCurrentMain=current), time.time()):
                    save_account(path, state)
                if event.get('invalidated'):
                    if any(d['status'] != 'submitted' for d in event.get('deliveries', {}).values()):
                        failures.append('delivery_outcome_requires_reconciliation')
                    break
            delivery = event.setdefault('deliveries', {}).get(key)
            if delivery:
                if delivery['status'] == 'submitted':
                    continue
                # Preserve the original evidence even if a later pause mentions
                # the same approval. Reconnection/coalescing cannot resolve it.
                failures.append('delivery_outcome_requires_reconciliation')
                continue
            if event['kind'] in ('reset_confirmation_required', 'reset_availability_unknown'):
                pause = next((e for e in reversed(episode['events'])
                              if e['kind'] == 'foreman_pause_required' and
                              e.get('deliveries', {}).get('parent', {}).get('includesEvent') == event['id']), None)
                delivered = (pause or {}).get('deliveries', {}).get('parent', {})
                if delivered:
                    event.setdefault('deliveries', {})['parent'] = {
                        **delivered, 'coalescedInto': pause['id']}
                    save_account(path, state)
                    if delivered['status'] != 'submitted':
                        failures.append('delivery_outcome_requires_reconciliation')
                    continue
            with delivery_lock() if delivery_lock is not None else nullcontext():
                if delivery_lock is not None:
                    expected = copy.deepcopy(state)
                    for pending in expected.get('episode', {}).get('events', []):
                        if pending.get('deliveries') == {}:
                            pending.pop('deliveries')
                    persisted = read_private(path)
                    if isinstance(persisted, dict):
                        for pending in (persisted.get('episode') or {}).get('events', []):
                            if pending.get('deliveries') == {}:
                                pending.pop('deliveries')
                    if persisted != expected:
                        raise RuntimeError('delivery_state_changed')
                fallback_pause = (event['id'] in fallback_event_ids and
                                  event['kind'] == 'foreman_pause_required' and
                                  event.get('reason') == 'observation_failed_during_confirmed_exhaustion' and
                                  fallback_eligible(state, config.get('policy', {})))
                if not exhausted(current_observation or {}) and not fallback_pause:
                    failures.append('current_zero_required_before_delivery')
                    continue
                if delivery_guard is not None and not delivery_guard():
                    failures.append('maintenance_delivery_inhibited')
                    continue
                recipient_bridge = bridge.for_recipient(key) if hasattr(bridge, 'for_recipient') else bridge
                if recipient_bridge is None:
                    failures.append(key + '_destination_unavailable')
                    continue  # Known unsent: no uncertain receipt and no replay permission needed.
                event['deliveries'][key] = {'status': 'attempting', 'target': target, 'at': time.time()}
                if event['kind'] == 'foreman_pause_required' and key == 'parent':
                    confirmation = next((e for e in episode['events'] if e['kind'] in (
                        'reset_confirmation_required', 'reset_availability_unknown')), None)
                    if confirmation:
                        event['deliveries'][key]['includesEvent'] = confirmation['id']
                save_account(path, state)
                try:
                    recipient_bridge.send(target, make_prompt(event, episode, account, path, key,
                                                             new_routing=config.get('incidentRoutingEnabled') is True))
                except Exception:
                    event['deliveries'][key]['status'] = 'outcome_unknown'
                    save_account(path, state)
                    failures.append('delivery_outcome_unknown')
                    continue
                event['deliveries'][key]['status'] = 'submitted'
                save_account(path, state)
    if failures:
        raise RuntimeError(';'.join(sorted(set(failures))))


def validate_config(config, activate=False, qualify=False):
    if activate and config.get('incidentRoutingEnabled') is True:
        from activation import routing_readiness
        routing_readiness(config, config['stateDir'])
    destinations = [config['foremanThreadId'], config['parentThreadId']]
    if 'conciergeThreadId' in config:
        destinations.append(config['conciergeThreadId'])
    if (not all(isinstance(value, str) and value.strip() for value in destinations)
            or len(set(destinations)) != len(destinations)):
        raise ValueError('distinct_notification_destinations_required')
    if config.get('incidentRoutingEnabled') is True:
        bindings = config.get('roleBindings')
        if not isinstance(bindings, dict) or 'conciergeThreadId' not in config:
            raise ValueError('incident_routing_binding_required')
        for key, role, target in (('foreman', 'dispatch', config['foremanThreadId']),
                                  ('concierge', 'concierge', config['conciergeThreadId'])):
            binding = bindings.get(key)
            if (not isinstance(binding, dict) or binding.get('thread_id') != target or binding.get('role') != role
                    or not all(isinstance(binding.get(field), str) and binding[field].strip()
                               for field in ('host_id', 'acceptance_ref'))):
                raise ValueError('incident_routing_binding_required')
        store = config.get('incidentReceiptStore')
        owner = bindings.get('repairOwner')
        if (not isinstance(store, str) or not Path(store).is_absolute() or not isinstance(owner, dict)
                or owner.get('role') != 'repair_owner' or not all(
                    isinstance(owner.get(field), str) and owner[field].strip()
                    for field in ('thread_id', 'host_id', 'acceptance_ref'))):
            raise ValueError('incident_routing_binding_required')
        if owner['thread_id'] in destinations:
            raise ValueError('distinct_notification_destinations_required')
        if not isinstance(config.get('parentHostId'), str) or not config['parentHostId'].strip():
            raise ValueError('parent_host_required')
    accounts = config['accounts']
    if len({a['name'] for a in accounts}) != len(accounts):
        raise ValueError('duplicate_account_configuration')
    if [a['name'] for a in accounts] != ['main', 'daybreak']:
        raise ValueError('unexpected_account_scope')
    for account in accounts:
        if account['name'] == 'main' and account.get('dynamicDefault') is not True:
            raise ValueError('main_must_follow_default_identity')
        if account['name'] == 'daybreak' and (account.get('dynamicDefault') or not all(account.get(k) for k in ('email', 'accountId', 'authFile'))):
            raise ValueError('daybreak_must_remain_pinned')
    if not 5 <= config['intervalSeconds'] <= 60:
        raise ValueError('invalid_interval')
    if activate or qualify:
        policy = config['policy']
        if config.get('enabled') is not True:
            raise ValueError('activation_policy_not_approved')
        if policy.get('pauseWhenUnmetered') is True and (
                policy.get('fallbackDecision') != 'pause_at_zero_when_unmetered' or
                not isinstance(policy.get('fallbackDecisionReference'), str) or
                not policy['fallbackDecisionReference'].strip()):
            raise ValueError('unmetered_fallback_not_approved')
        rate = decimal(policy.get('usdPerCredit'))
        target = decimal(policy.get('pauseAtUsd'))
        if rate is None or rate <= 0 or target is None or not 0 < target < 10:
            raise ValueError('valid_meter_required_for_activation')
    if config['policy'].get('usdPerCredit') is not None and not config['policy'].get('usdRateEvidence'):
        raise ValueError('unverified_dollar_conversion')


def open_singleton_lock(path, *, create=False):
    private_directory(Path(path).parent, create=False)
    flags = (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY) | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid() or opened.st_mode & 0o077
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise ValueError('existing_owned_singleton_lock_required')
        return os.fdopen(descriptor, 'r+' if create else 'r')
    except BaseException:
        os.close(descriptor)
        raise


def run(config, state_dir, *, once=False, observe_only=False):
    validate_config(config, activate=False)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open_singleton_lock(state_dir / 'observer.lock', create=True) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Every mode writes state, including observe-only. The predecessor lock
        # keeps those writes exclusive across deployment and rollback.
        shared = open_singleton_lock(config['sharedLock'])
        try:
            fcntl.flock(shared, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            shared.close()
            raise
        transport = None
        emergency_throttle = {}
        try:
            while True:
                started = time.monotonic()
                health = {'observedAt': dt.datetime.now(dt.UTC).isoformat(), 'pid': os.getpid(),
                          'observeOnly': observe_only, 'accounts': {}}
                activation = ({'mode': 'observe_only'} if observe_only else delivery_status(config, state_dir))
                armed = activation['mode'] == 'active'
                if armed:
                    try:
                        validate_config(config, activate=True)
                    except ValueError:
                        armed = False
                        activation = {'mode': 'maintenance', 'reason': 'activation_policy_invalid'}
                health['delivery'] = activation
                if armed and transport is None:
                    transport = Transport(state_dir, config,
                                          delivery_guard=lambda: delivery_status(config, state_dir)['mode'] == 'active',
                                          delivery_lock=lambda: decision_lock(state_dir),
                                          emergency_throttle=emergency_throttle)
                    if os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'):
                        try: publish_candidate(state_dir, config, os.environ)
                        except (OSError, ValueError): pass
                elif not armed and transport is not None:
                    transport.close(); transport = None
                bridge = None
                fresh_usage = []
                intake_errors = {}
                # Both profile reads start together; one slow/failing account cannot
                # indefinitely starve the other account's threshold check.
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {a['name']: executor.submit(read_account, a, node=config.get('nodeBinary', NODE)) for a in config['accounts']}
                    if transport:
                        try:
                            with decision_lock(state_dir):
                                current_activation = delivery_status(config, state_dir)
                                if current_activation['mode'] == 'active':
                                    # The outer decision lock already guards any
                                    # synchronous handoff during connection.
                                    nested_lock = transport.delivery_lock
                                    transport.delivery_lock = None
                                    try:
                                        bridge = transport.connection()
                                    finally:
                                        transport.delivery_lock = nested_lock
                                    health['transport'] = 'verified' if transport.health.get('healthy') else 'partial'
                                else:
                                    transport.close(); transport = None
                                    armed = False; activation = current_activation
                                    health['delivery'] = activation
                        except Exception:
                            health['transport'] = 'unavailable'
                    alias_reading = None
                    skip_daybreak = False
                    for configured in config['accounts']:
                        account = configured
                        name = account['name']; path = state_dir / f'{name}.json'
                        if name == 'daybreak' and skip_daybreak:
                            health['accounts'][name] = {'status': 'deferred', 'reason': 'canonical_episode_reconciliation_required'}
                            continue
                        observation_acquired = False
                        try:
                            check_account_custody(path)
                            try:
                                reading = futures[name].result()
                            except Exception:
                                if name != 'daybreak' or alias_reading is None:
                                    raise
                                # Default credentials may identify the same pinned
                                # account. Its already server-validated reading is
                                # valid evidence for that identity, never another one.
                                reading = alias_reading
                            observation_acquired = True
                            rate = reading.get('rate_limit')
                            windows = [rate.get(key) for key in ('primary_window', 'secondary_window')] if isinstance(rate, dict) else []
                            values = [window.get('used_percent') for window in windows if isinstance(window, dict)]
                            fresh_usage.append(max(values) if values and all(number(value) and 0 <= value <= 100 for value in values) else None)
                            if account.get('dynamicDefault'):
                                account, state = select_main_identity(state_dir, account, reading)
                                daybreak = next(a for a in config['accounts'] if a['name'] == 'daybreak')
                                if reading['accountId'] == daybreak['accountId'] and reading['email'] == daybreak['email']:
                                    alias_reading = reading
                                    try:
                                        with reset_operation_lock(state_dir, blocking=False):
                                            state = adopt_main_episode(state_dir, state)
                                    except (ResetOperationBusy, ValueError):
                                        skip_daybreak = True
                                        health['accounts'][name] = {'status': 'deferred', 'reason': 'canonical_episode_reconciliation_required'}
                                        continue
                                    state['delegatedTo'] = 'daybreak'
                                    save_account(path, state)
                                    if transport and config.get('incidentRoutingEnabled') is True:
                                        try: transport.observation_recovered(name)
                                        except Exception: intake_errors[name] = 'observation_recovery_record_failed'
                                    health['accounts'][name] = {'status': 'covered_by_daybreak', 'sampleAt': reading['observedAt']}
                                    continue
                            else:
                                try:
                                    account = {**account, 'alsoCurrentMain': default_identity() == {
                                        'email': account['email'], 'accountId': account['accountId']}}
                                except Exception:
                                    account = {**account, 'alsoCurrentMain': None}
                            with reset_operation_lock(state_dir, blocking=False) if name == 'daybreak' else nullcontext():
                                if name == 'daybreak':
                                    # The parallel read may precede a reset that
                                    # finished while this observer waited. Use a
                                    # fresh reading taken under the operation lock.
                                    # A successful non-exhausted reading must also
                                    # veto later fallback if this refresh fails.
                                    veto = next((sample for sample in (reading, alias_reading)
                                                 if sample is not None and not exhausted(sample)), None)
                                    if path.exists() and veto is not None:
                                        retained = json.loads(path.read_text())
                                        retained['lastSample'] = veto
                                        retained['lastReadAt'] = time.time()
                                        save_account(path, retained)
                                    fresh_usage.pop()
                                    observation_acquired = False
                                    try:
                                        reading = read_account(account, node=config.get('nodeBinary', NODE))
                                    except Exception:
                                        if alias_reading is None:
                                            raise
                                        default = next(a for a in config['accounts'] if a.get('dynamicDefault'))
                                        reading = read_account(default, node=config.get('nodeBinary', NODE))
                                        if (reading.get('email'), reading.get('accountId')) != (account['email'], account['accountId']):
                                            raise ValueError('default_account_mismatch')
                                    observation_acquired = True
                                    rate = reading.get('rate_limit')
                                    windows = [rate.get(key) for key in ('primary_window', 'secondary_window')] if isinstance(rate, dict) else []
                                    values = [window.get('used_percent') for window in windows if isinstance(window, dict)]
                                    fresh_usage.append(max(values) if values and all(number(value) and 0 <= value <= 100 for value in values) else None)
                                    state = json.loads(path.read_text()) if path.exists() else {}
                                release_path = state_dir / f'release-{name}.json'
                                release = json.loads(release_path.read_text()) if release_path.exists() else None
                                prior_episode = state.get('episode')
                                if name == 'daybreak' and prior_episode:
                                    prior_reset_digest = reset_evidence(state_dir, state)
                                if name == 'daybreak':
                                    state['fallbackEvidenceValid'] = True
                                state = settle(state, reading, release)
                                if prior_episode and prior_episode.get('active') and not state['episode']['active']:
                                    closed = copy.deepcopy(state['episode'])
                                    if name == 'daybreak':
                                        closed['resetEvidence'] = prior_reset_digest
                                    save(state_dir / ('closed-' + prior_episode['id'] + '.json'), closed)
                                state, _ = advance(state, reading, account, now=time.time(), policy=config['policy'])
                                if name == 'daybreak':
                                    if prior_episode and (state.get('episode') or {}).get('id') != prior_episode.get('id'):
                                        state.pop('resetEvidence', None)
                                        state.pop('resetEvidenceEpisode', None)
                                    state['resetEvidence'] = reset_evidence(state_dir, state)
                                    if state.get('episode'):
                                        state['resetEvidenceEpisode'] = state['episode']['id']
                                save_account(path, state)
                                if transport and config.get('incidentRoutingEnabled') is True:
                                    try: transport.observation_recovered(name)
                                    except Exception: intake_errors[name] = 'observation_recovery_record_failed'
                                if armed:
                                    if bridge is None:
                                        raise RuntimeError('native_transport_unavailable')
                                    dispatch(state, account, config, bridge, path,
                                             current_observation=reading,
                                             delivery_guard=lambda: delivery_status(config, state_dir)['mode'] == 'active',
                                             delivery_lock=lambda: decision_lock(state_dir))
                            health['accounts'][name] = {'status': 'healthy', 'observationStatus': 'healthy',
                                                       'deliveryStatus': health.get('transport', 'unavailable') if armed else activation['mode'],
                                                       'sampleAt': reading['observedAt'],
                                                       'email': reading['email'], 'accountId': reading['accountId']}
                        except AccountStateMissing:
                            health['accounts'][name] = {'status': 'deferred', 'reason': 'account_state_reconciliation_required'}
                        except ResetOperationBusy:
                            health['accounts'][name] = {'status': 'deferred', 'reason': 'reset_in_progress'}
                        except Exception as error:
                            if not observation_acquired:
                                fresh_usage.append(None)
                                if transport and config.get('incidentRoutingEnabled') is True:
                                    try:
                                        transport.observation_failed(name)
                                    except Exception:
                                        intake_errors[name] = 'observation_incident_record_failed'
                            health['accounts'][name] = {'status': 'degraded',
                                                       'observationStatus': 'healthy' if observation_acquired else 'unavailable',
                                                       'sampleAt': reading['observedAt'] if observation_acquired else None,
                                                       'deliveryStatus': 'degraded' if armed else activation['mode'],
                                                       'errorType': type(error).__name__,
                                                       'reason': str(error) if isinstance(error, (RuntimeError, ValueError)) else 'read_or_dispatch_failed'}
                            if (bridge is not None and isinstance(error, RuntimeError) and
                                    'delivery_outcome_unknown' in str(error).split(';')):
                                transport.close(); bridge = None
                            if not observation_acquired and armed and path.exists():
                                try:
                                    with reset_operation_lock(state_dir, blocking=False) if name == 'daybreak' else nullcontext():
                                        retained = json.loads(path.read_text())
                                        if name == 'daybreak' and retained.get('resetEvidence') != reset_evidence(state_dir, retained):
                                            continue
                                        if configured.get('dynamicDefault'):
                                            _, selected = check_main_selection(state_dir, retained)
                                            if default_identity() != {'email': selected['email'], 'accountId': selected['accountId']}:
                                                continue
                                            account = {**configured, 'email': selected['email'], 'accountId': selected['accountId'],
                                                       'identityGeneration': selected['generation']}
                                        retained, new_events = observation_failed(retained, config['policy'])
                                        if new_events:
                                            save_account(path, retained)
                                        episode = retained.get('episode') or {}
                                        fallback_ids = set()
                                        if fallback_eligible(retained, config['policy']):
                                            fallback_ids = {e['id'] for e in episode.get('events', [])
                                                            if e.get('kind') == 'foreman_pause_required'
                                                            and e.get('reason') == 'observation_failed_during_confirmed_exhaustion'
                                                            and not e.get('invalidated')}
                                        if fallback_ids:
                                            if bridge is None:
                                                raise RuntimeError('native_transport_unavailable')
                                            dispatch(retained, account, config, bridge, path,
                                                     fallback_event_ids=fallback_ids,
                                                     delivery_guard=lambda: delivery_status(config, state_dir)['mode'] == 'active',
                                                     delivery_lock=lambda: decision_lock(state_dir))
                                except Exception:
                                    health['accounts'][name]['fallbackDelivery'] = 'unconfirmed'
                if (transport and config.get('incidentRoutingEnabled') is True and
                        transport.incident_error is not True):
                    try:
                        with decision_lock(state_dir):
                            if delivery_status(config, state_dir)['mode'] == 'active':
                                transport.reconcile_pending()
                    except Exception:
                        health['receiptReconciliation'] = 'unavailable'
                        transport.intake_status = 'outcome_unknown'
                if (transport and config.get('incidentRoutingEnabled') is True and
                        (transport.intake_status in ('waiting_for_route', 'outcome_unknown', 'submitted') or
                         transport.incident_registration_failed is True or
                         'observation_incident_record_failed' in intake_errors.values())):
                    used = (max(fresh_usage) if len(fresh_usage) == len(config['accounts']) and
                            all(value is not None for value in fresh_usage) else None)
                    try:
                        with decision_lock(state_dir):
                            if delivery_status(config, state_dir)['mode'] == 'active':
                                transport.emergency_notice(intake_failed=True, active=None, used_percent=used,
                                                           uncertain=transport.intake_uncertain)
                    except Exception:
                        health['emergencyNotice'] = 'failed'
                if intake_errors:
                    health['intakeErrors'] = intake_errors
                save(state_dir / 'health.json', health)
                if once:
                    return health
                time.sleep(max(0, config['intervalSeconds'] - (time.monotonic() - started)))
        finally:
            if transport is not None:
                transport.close()
            if shared is not None:
                shared.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_arguments(parser)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--observe-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(load_config(args.config, args.state_dir), Path(args.state_dir),
                         once=args.once, observe_only=args.observe_only)))
