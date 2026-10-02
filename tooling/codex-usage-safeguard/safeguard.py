#!/usr/bin/env python3
"""Deterministic quota-event policy. No reset redemption lives in this process."""
import copy
from decimal import Decimal, InvalidOperation
import math
import uuid


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def decimal(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def wait_budget(episode, sample, previous, now, policy):
    balance = decimal((sample.get('credits') or {}).get('balance'))
    rate = decimal(policy.get('usdPerCredit'))
    last = decimal(episode.get('lastBalance'))
    fresh = now - previous.get('lastReadAt', now) <= 30
    if balance is None or rate is None or rate <= 0:
        episode['budgetStatus'] = 'unmetered_wait_blocked'
        return 'unmetered_confirmation_wait' if policy.get('pauseWhenUnmetered') is True else None
    used = decimal(episode.get('waitingCredits', '0'))
    if last is not None:
        used += max(Decimal(0), last - balance)
    episode.update(lastBalance=str(balance), waitingCredits=str(used), waitingSpendUsd=str(used * rate),
                   budgetStatus='estimated_from_balance_debits')
    target = decimal(policy.get('pauseAtUsd', '8'))
    if target is None or not 0 < target < 10:
        raise ValueError('invalid_spend_threshold')
    if used * rate >= target:
        return 'waiting_spend_threshold'
    if not fresh:
        episode['budgetStatus'] = 'observed_debits_across_sampling_gap'
        if policy.get('pauseWhenUnmetered') is True:
            return 'unmetered_confirmation_wait'
    return None


def emit(episode, events, kind, reason=None):
    if any(e['kind'] == kind for e in episode['events']):
        return
    event = {'id': episode['id'] + ':' + kind, 'kind': kind}
    if reason:
        event['reason'] = reason
    episode['events'].append(event)
    events.append(event)


def settle(previous, sample, release):
    """Consume an exact operator release only when matching usage is restored."""
    state = copy.deepcopy(previous)
    episode = state.get('episode')
    if not episode or not episode.get('active') or not release:
        return state
    if (release.get('episodeId') != episode['id'] or release.get('accountId') != episode['accountId']
            or not release.get('evidenceReference') or sample.get('accountId') != episode['accountId']
            or sample.get('email') != episode['email']):
        return state
    paused = any(e['kind'] == 'foreman_pause_required' for e in episode['events'])
    if paused and release.get('holdReleased') is not True:
        return state
    rate = sample.get('rate_limit') or {}
    windows = [rate[k] for k in ('primary_window', 'secondary_window') if isinstance(rate.get(k), dict)]
    if (rate.get('allowed') is not True or not windows or
            not all(number(w.get('used_percent')) and 0 <= w['used_percent'] < 100 for w in windows)):
        return state
    episode.update(active=False, release=release, settledAt=sample['observedAt'])
    return state


def reset_count(sample):
    reset = sample.get('rate_limit_reset_credits') or {}
    count = reset.get('applicable_available_count')
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None


def observation_failed(previous, policy):
    """Only an already-confirmed exhaustion episode can justify outage fallback."""
    state = copy.deepcopy(previous)
    episode = state.get('episode')
    events = []
    if episode and episode.get('active') and policy.get('pauseWhenUnmetered') is True:
        emit(episode, events, 'foreman_pause_required', 'observation_failed_during_confirmed_exhaustion')
    return state, events


def advance(previous, sample, account, *, now, policy=None):
    """Consume one fresh profile reading; emit persistent intents, never actions."""
    state = copy.deepcopy(previous)
    policy = policy or {}
    if sample.get('email') != account['email'] or sample.get('accountId') != account['accountId']:
        raise ValueError('account_mismatch')
    if state.get('accountId', account['accountId']) != account['accountId']:
        raise ValueError('state_account_mismatch')
    windows = [v for k, v in (sample.get('rate_limit') or {}).items()
               if k in ('primary_window', 'secondary_window') and isinstance(v, dict)]
    used = [v['used_percent'] for v in windows if number(v.get('used_percent')) and v['used_percent'] >= 0]
    exhausted = any(v >= 100 for v in used)
    main_scope = account['name'] == 'main' or account.get('alsoCurrentMain') is True
    state.update(accountId=account['accountId'], lastSample=sample, lastReadAt=now)
    events = []
    episode = state.get('episode')
    if episode and episode.get('active'):
        # An available blip never rearms. A deliberate, verified release is needed.
        if not exhausted:
            if used and len(used) == len(windows):
                episode['budgetStatus'] = 'allowance_restored_pending_rearm'
                balance = decimal((sample.get('credits') or {}).get('balance'))
                if balance is not None:
                    episode['lastBalance'] = str(balance)
            else:
                episode['budgetStatus'] = 'quota_unknown_wait_blocked'
            return state, events
        if not any(e['kind'] == 'foreman_pause_required' for e in episode['events']):
            count = reset_count(sample)
            pending = any(e['kind'] == 'reset_confirmation_required' for e in episode['events'])
            if not pending and count and count > 0:
                episode.update(resetCount=count, confirmationStartedAt=now)
                balance = decimal((sample.get('credits') or {}).get('balance'))
                episode.update(lastBalance=str(balance) if balance is not None else None,
                               waitingCredits='0', waitingSpendUsd='0')
                emit(episode, events, 'reset_confirmation_required')
                pending = True
            if count == 0 and main_scope:
                reason = 'no_applicable_reset'
            elif pending or main_scope:
                reason = wait_budget(episode, sample, previous, now, policy)
            else:
                episode['budgetStatus'] = 'no_reset_confirmation_pending'
                reason = None
            if reason:
                emit(episode, events, 'foreman_pause_required', reason)
        return state, events
    if not exhausted:
        return state, events
    count = reset_count(sample)
    episode = {'id': str(uuid.uuid4()), 'active': True, 'accountId': account['accountId'],
               'email': account['email'], 'openedAt': now, 'observedAt': sample['observedAt'],
               'resetCount': count, 'resetIdempotencyKey': str(uuid.uuid4()), 'events': []}
    state['episode'] = episode
    if count and count > 0:
        emit(episode, events, 'reset_confirmation_required')
    elif count == 0 and main_scope:
        emit(episode, events, 'foreman_pause_required', 'no_applicable_reset')
    elif count is None:
        emit(episode, events, 'reset_availability_unknown')
    if not main_scope and not count:
        episode['budgetStatus'] = 'no_reset_confirmation_pending'
        return state, events
    # Include debits since the preceding healthy sample to cover detection lag.
    prior_balance = decimal((previous.get('lastSample', {}).get('credits') or {}).get('balance'))
    if prior_balance is not None:
        episode['lastBalance'] = str(prior_balance)
    reason = wait_budget(episode, sample, previous, now, policy)
    if reason:
        emit(episode, events, 'foreman_pause_required', reason)
    return state, events
