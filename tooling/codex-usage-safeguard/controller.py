#!/usr/bin/env python3
"""Single local service: fresh reads, durable intents, native Steer dispatch."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import json
import os
import base64
from pathlib import Path
import subprocess
import time
import uuid

from safeguard import advance, settle, observation_failed, decimal
from transport import Transport, publish_candidate
from storage import save
from configuration import load_config, runtime_arguments

ROOT = Path(__file__).resolve().parent
NODE = 'node'


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


def select_main_identity(root, template, reading):
    """Commit a server-validated main-account transition before classifying quota."""
    path = root / 'main.json'
    selector = root / 'main-selection.json'
    old = json.loads(path.read_text()) if path.exists() else {}
    selected = json.loads(selector.read_text()) if selector.exists() else {}
    identity = {'accountId': reading['accountId'], 'email': reading['email']}
    changed = (selected.get('accountId'), selected.get('email')) != (identity['accountId'], identity['email'])
    if changed:
        if old:
            archive = root / 'account-history'; archive.mkdir(mode=0o700, exist_ok=True)
            generation = old.get('identityGeneration') or str(uuid.uuid4())
            old.update(supersededBy=identity, pendingApprovalsInvalidated=True)
            save(archive / f'{generation}.json', old)
        selected = {**identity, 'generation': str(uuid.uuid4()), 'observedAt': reading['observedAt']}
        # State first: a crash before selector persistence creates another clean
        # generation, never attributes an old episode to the newly selected account.
        old = {'accountId': identity['accountId'], 'identityGeneration': selected['generation']}
        save(path, old)
        save(selector, selected)
    elif old.get('accountId') != identity['accountId'] or old.get('identityGeneration') != selected['generation']:
        raise ValueError('main_selection_state_mismatch')
    return {**template, **identity, 'identityGeneration': selected['generation']}, old


def make_prompt(event, episode, account, state_path, recipient):
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
            return (common + f"Pause condition: {event['reason']}. The named Codex Foreman is the "
                    "sole pause coordinator. Inspect the recorded foreman delivery status before "
                    "reporting delivery; submitted does not mean acknowledged or stopped. Notify "
                    "Ivan in the main chat, reconcile missing/uncertain delivery by reading the "
                    "Foreman thread, and do not create a second pause coordinator or replay this "
                    f"episode. Applicable reset count at detection: {episode['resetCount']!r}; "
                    f"saved reset idempotency key: {episode['resetIdempotencyKey']}. "
                    "If a reset is available, ask for explicit confirmation of that specific "
                    "reset for this account unless that question is already pending; never ask "
                    "twice for the same episode. No reset, account switch or automatic "
                    "resume is authorized by this event. The local observer continues without "
                    "model polling. Read OPERATING.md for the reset and release procedure.")
        return (common + f"Pause reason: {event['reason']}. Ivan authorized this conditional protective "
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


def dispatch(state, account, config, bridge, path):
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
        targets = [('parent', config['parentThreadId'])]
        if event['kind'] == 'foreman_pause_required':
            targets.insert(0, ('foreman', config['foremanThreadId']))
        for key, target in targets:
            delivery = event.setdefault('deliveries', {}).get(key)
            if delivery:
                if delivery['status'] == 'submitted':
                    continue
                # Preserve the original evidence even if a later pause mentions
                # the same approval. Reconnection/coalescing cannot resolve it.
                failures.append('delivery_outcome_requires_reconciliation')
                continue
            if event['kind'] in ('reset_confirmation_required', 'reset_availability_unknown'):
                pause = next((e for e in episode['events'] if e['kind'] == 'foreman_pause_required'), None)
                delivered = (pause or {}).get('deliveries', {}).get('parent', {})
                if delivered.get('status') == 'submitted' and delivered.get('includesEvent') == event['id']:
                    event.setdefault('deliveries', {})['parent'] = {
                        'status': 'submitted', 'target': target, 'coalescedInto': pause['id']}
                    save(path, state)
                    continue
            event['deliveries'][key] = {'status': 'attempting', 'target': target, 'at': time.time()}
            if event['kind'] == 'foreman_pause_required' and key == 'parent':
                confirmation = next((e for e in episode['events'] if e['kind'] in (
                    'reset_confirmation_required', 'reset_availability_unknown')), None)
                if confirmation:
                    event['deliveries'][key]['includesEvent'] = confirmation['id']
            save(path, state)
            try:
                bridge.send(target, make_prompt(event, episode, account, path, key))
            except Exception:
                event['deliveries'][key]['status'] = 'outcome_unknown'
                save(path, state)
                failures.append('delivery_outcome_unknown')
                continue
            event['deliveries'][key]['status'] = 'submitted'
            save(path, state)
    if failures:
        raise RuntimeError(';'.join(sorted(set(failures))))


def validate_config(config, activate=False):
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
    if activate:
        policy = config['policy']
        if config.get('enabled') is not True:
            raise ValueError('activation_policy_not_approved')
        if policy.get('pauseWhenUnmetered') is True and (
                policy.get('fallbackDecision') != 'pause_at_zero_when_unmetered' or
                not policy.get('fallbackDecisionReference')):
            raise ValueError('unmetered_fallback_not_approved')
        rate = decimal(policy.get('usdPerCredit'))
        target = decimal(policy.get('pauseAtUsd'))
        if rate is None or rate <= 0 or target is None or not 0 < target < 10:
            raise ValueError('valid_meter_required_for_activation')
    if config['policy'].get('usdPerCredit') is not None and not config['policy'].get('usdRateEvidence'):
        raise ValueError('unverified_dollar_conversion')


def run(config, state_dir, *, once=False, observe_only=False):
    validate_config(config, activate=not observe_only)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_dir / 'observer.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        shared = None
        if not observe_only:
            # The original watcher already uses this lock. Retain its singleton
            # boundary across deployment, rollback, and accidental old restarts.
            shared = Path(config['sharedLock']).open('r')
            fcntl.flock(shared, fcntl.LOCK_EX | fcntl.LOCK_NB)
        transport = Transport(state_dir, config) if not observe_only else None
        if transport and os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'):
            try:
                publish_candidate(state_dir, config, os.environ)
            except (OSError, ValueError):
                pass  # A stale bootstrap route must not stop quota observation.
        try:
            while True:
                started = time.monotonic()
                health = {'observedAt': dt.datetime.now(dt.UTC).isoformat(), 'pid': os.getpid(),
                          'observeOnly': observe_only, 'accounts': {}}
                bridge = None
                # Both profile reads start together; one slow/failing account cannot
                # indefinitely starve the other account's threshold check.
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {a['name']: executor.submit(read_account, a, node=config.get('nodeBinary', NODE)) for a in config['accounts']}
                    if transport:
                        try:
                            bridge = transport.connection()
                            health['transport'] = 'verified'
                        except Exception:
                            health['transport'] = 'unavailable'
                    alias_reading = None
                    for configured in config['accounts']:
                        account = configured
                        name = account['name']; path = state_dir / f'{name}.json'
                        try:
                            try:
                                reading = futures[name].result()
                            except Exception:
                                if name != 'daybreak' or alias_reading is None:
                                    raise
                                # Default credentials may identify the same pinned
                                # account. Its already server-validated reading is
                                # valid evidence for that identity, never another one.
                                reading = alias_reading
                            if account.get('dynamicDefault'):
                                account, state = select_main_identity(state_dir, account, reading)
                                daybreak = next(a for a in config['accounts'] if a['name'] == 'daybreak')
                                if reading['accountId'] == daybreak['accountId'] and reading['email'] == daybreak['email']:
                                    alias_reading = reading
                                    state['delegatedTo'] = 'daybreak'
                                    save(path, state)
                                    health['accounts'][name] = {'status': 'covered_by_daybreak', 'sampleAt': reading['observedAt']}
                                    continue
                            else:
                                state = json.loads(path.read_text()) if path.exists() else {}
                                try:
                                    account = {**account, 'alsoCurrentMain': default_identity() == {
                                        'email': account['email'], 'accountId': account['accountId']}}
                                except Exception:
                                    account = {**account, 'alsoCurrentMain': False}
                            release_path = state_dir / f'release-{name}.json'
                            release = json.loads(release_path.read_text()) if release_path.exists() else None
                            prior_episode = state.get('episode')
                            state = settle(state, reading, release)
                            if prior_episode and prior_episode.get('active') and not state['episode']['active']:
                                save(state_dir / ('closed-' + prior_episode['id'] + '.json'), state['episode'])
                            state, _ = advance(state, reading, account, now=time.time(), policy=config['policy'])
                            save(path, state)
                            if not observe_only:
                                if bridge is None:
                                    raise RuntimeError('native_transport_unavailable')
                                dispatch(state, account, config, bridge, path)
                            health['accounts'][name] = {'status': 'healthy', 'sampleAt': reading['observedAt'],
                                                       'email': reading['email'], 'accountId': reading['accountId']}
                        except Exception as error:
                            health['accounts'][name] = {'status': 'degraded', 'errorType': type(error).__name__,
                                                       'reason': str(error) if isinstance(error, (RuntimeError, ValueError)) else 'read_or_dispatch_failed'}
                            if bridge is not None and isinstance(error, RuntimeError) and 'delivery' in str(error):
                                transport.close(); bridge = None
                            if not observe_only and path.exists():
                                try:
                                    retained = json.loads(path.read_text())
                                    if configured.get('dynamicDefault'):
                                        selected = json.loads((state_dir / 'main-selection.json').read_text())
                                        if default_identity() != {'email': selected['email'], 'accountId': selected['accountId']}:
                                            continue
                                        account = {**configured, 'email': selected['email'], 'accountId': selected['accountId'],
                                                   'identityGeneration': selected['generation']}
                                    retained, new_events = observation_failed(retained, config['policy'])
                                    if new_events:
                                        save(path, retained)
                                        bridge = transport.connection()
                                        dispatch(retained, account, config, bridge, path)
                                except Exception:
                                    health['accounts'][name]['fallbackDelivery'] = 'unconfirmed'
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
