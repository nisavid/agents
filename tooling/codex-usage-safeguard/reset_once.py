#!/usr/bin/env python3
"""Operator-only Daybreak redemption after a specific human confirmation.

The observer never invokes this module. A confirmation reference is an audit
pointer; the calling agent must actually verify the human reply in the main chat.
"""
import argparse
import datetime as dt
import fcntl
import json
from pathlib import Path
import subprocess

from controller import ROOT, NODE, read_account, save
from safeguard import number
from configuration import load_config, runtime_arguments


def prepare(account, episode, usage, inventory, confirmation, now):
    if not isinstance(confirmation, str) or not confirmation.strip():
        raise ValueError('specific_confirmation_required')
    if (not episode.get('active') or episode.get('accountId') != account['accountId'] or
            usage.get('accountId') != account['accountId'] or usage.get('email') != account['email']):
        raise ValueError('episode_or_usage_account_mismatch')
    if inventory.get('credentialAccountId') != account['accountId'] or inventory.get('credentialEmail') != account['email']:
        raise ValueError('inventory_account_mismatch')
    windows = usage.get('rate_limit') or {}
    if not any(isinstance(windows.get(k), dict) and number(windows[k].get('used_percent')) and
               windows[k]['used_percent'] >= 100 for k in ('primary_window', 'secondary_window')):
        raise ValueError('quota_not_zero')
    count = (usage.get('rate_limit_reset_credits') or {}).get('applicable_available_count')
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError('no_applicable_reset')
    moment = dt.datetime.fromisoformat(now.replace('Z', '+00:00'))
    eligible = []
    for credit in inventory.get('data', {}).get('credits', []):
        if (credit.get('status') != 'available' or credit.get('is_supported_by_plan') is not True
                or credit.get('reset_type') != 'codex_rate_limits' or not isinstance(credit.get('id'), str)):
            continue
        expiry = credit.get('expires_at')
        if expiry is not None and dt.datetime.fromisoformat(expiry.replace('Z', '+00:00')) <= moment:
            continue
        eligible.append(credit)
    if not eligible:
        raise ValueError('no_eligible_reset_credit')
    eligible.sort(key=lambda x: x.get('expires_at') or '9999')
    return {'episodeId': episode['id'], 'email': account['email'], 'accountId': account['accountId'],
            'idempotencyKey': episode['resetIdempotencyKey'], 'creditId': eligible[0]['id'],
            'confirmationReference': confirmation, 'preparedAt': now}


def execute(config, runtime, account_name, episode_id, confirmation):
    if account_name != 'daybreak':
        raise ValueError('use_native_reset_tool_for_main_after_confirmation')
    account = next(a for a in config['accounts'] if a['name'] == account_name)
    with (runtime / 'reset.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        episode = json.loads((runtime / f'{account_name}.json').read_text())['episode']
        if episode['id'] != episode_id:
            raise ValueError('episode_mismatch')
        path = runtime / f'reset-{episode_id}.json'
        previous = json.loads(path.read_text()) if path.exists() else None
        if previous and previous.get('status') in ('reset', 'already_redeemed', 'no_credit', 'nothing_to_reset'):
            return previous
        if previous and previous.get('status') in ('attempting', 'outcome_unknown', 'reconciliation_required'):
            # A successful provider reset can restore quota before its response
            # reaches us. Do not bypass exact-zero/credit checks or replay an
            # uncertain consume automatically; make the reconciliation gate explicit.
            previous.setdefault('priorAttemptStatus', previous['status'])
            previous.update(status='reconciliation_required', reason='prior_reset_outcome_unconfirmed')
            save(path, previous)
            raise ValueError('reset_reconciliation_required')
        if previous:
            raise ValueError('invalid_saved_reset_status')
        usage = read_account(account, node=config['nodeBinary'])
        inventory = read_account(account, mode='resets', node=config['nodeBinary'])
        request = prepare(account, episode, usage, inventory, confirmation, dt.datetime.now(dt.UTC).isoformat())
        record = {'request': request, 'status': 'attempting'}
        save(path, record)
        try:
            result = subprocess.run([config['nodeBinary'], str(ROOT / 'consume_reset.mjs'), config['_configPath']],
                                    input=json.dumps(request), capture_output=True, text=True, timeout=55)
            value = json.loads(result.stdout)
            if result.returncode or value.get('code') not in ('reset', 'already_redeemed', 'no_credit', 'nothing_to_reset'):
                raise RuntimeError('reset_outcome_unknown')
            record.update(status=value['code'], response=value)
        except Exception:
            record['status'] = 'outcome_unknown'
            save(path, record)
            raise
        save(path, record)
        return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_arguments(parser)
    parser.add_argument('--account', required=True, choices=['daybreak'])
    parser.add_argument('--episode', required=True)
    parser.add_argument('--confirmation-reference', required=True,
                        help='Actual human reply approving this exact account/episode; verify before invocation')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.error('No reset attempted. Explicit --execute and verified per-use human confirmation are required.')
    print(json.dumps(execute(load_config(args.config, args.state_dir), Path(args.state_dir),
                             args.account, args.episode, args.confirmation_reference)))
