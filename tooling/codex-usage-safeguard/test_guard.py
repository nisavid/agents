import copy
import unittest
from safeguard import advance, settle, observation_failed

ACCOUNT = {'name': 'main', 'email': 'main@example.test', 'accountId': 'main-id'}

def sample(used, resets=1, balance='1000'):
    return {'observedAt': '2026-10-01T00:00:00Z', 'email': ACCOUNT['email'],
            'accountId': ACCOUNT['accountId'], 'rate_limit': {'primary_window': {'used_percent': used}, 'secondary_window': None},
            'rate_limit_reset_credits': {'available_count': resets, 'applicable_available_count': resets},
            'credits': {'balance': balance}}

class GuardTests(unittest.TestCase):
    def test_only_zero_creates_confirmation_request(self):
        state = {}
        for used in (0, 90, 99, 99.999):
            state, events = advance(state, sample(used), ACCOUNT, now=0)
            self.assertEqual(events, [])
        state, events = advance(state, sample(100), ACCOUNT, now=10)
        self.assertEqual([e['kind'] for e in events], ['reset_confirmation_required'])
        self.assertEqual(state['episode']['accountId'], ACCOUNT['accountId'])

    def test_no_applicable_reset_requests_pause_once_across_restart(self):
        state, events = advance({}, sample(100, resets=0), ACCOUNT, now=10)
        self.assertEqual([e['kind'] for e in events], ['foreman_pause_required'])
        self.assertEqual(events[0]['reason'], 'no_applicable_reset')
        original = state['episode']['id']
        for used in (99, 100, 101, None, 0, 100):
            state, events = advance(copy.deepcopy(state), sample(used, resets=2), ACCOUNT, now=20)
            self.assertEqual(events, [])
            self.assertEqual(state['episode']['id'], original)

    def test_identity_mismatch_and_missing_telemetry_never_trigger(self):
        state, events = advance({}, sample(None), ACCOUNT, now=10)
        self.assertEqual(events, [])
        self.assertNotIn('episode', state)
        bad = sample(100); bad['accountId'] = 'other'
        with self.assertRaisesRegex(ValueError, 'account_mismatch'):
            advance({}, bad, ACCOUNT, now=10)

    def test_unmetered_wait_is_blocked_unless_protective_pause_authorized(self):
        state, events = advance({}, sample(100), ACCOUNT, now=10)
        self.assertEqual(state['episode']['budgetStatus'], 'unmetered_wait_blocked')
        state, events = advance(state, sample(100), ACCOUNT, now=20,
                                policy={'pauseWhenUnmetered': True})
        self.assertEqual([e['kind'] for e in events], ['foreman_pause_required'])
        self.assertEqual(events[0]['reason'], 'unmetered_confirmation_wait')

    def test_meter_counts_downward_changes_and_pauses_before_ten_dollars(self):
        policy = {'usdPerCredit': '0.04', 'pauseAtUsd': '8', 'pauseWhenUnmetered': True}
        state, events = advance({}, sample(100, balance='1000'), ACCOUNT, now=10, policy=policy)
        self.assertEqual([e['kind'] for e in events], ['reset_confirmation_required'])
        state, events = advance(state, sample(100, balance='850'), ACCOUNT, now=20, policy=policy)
        self.assertEqual(events, [])
        state, events = advance(state, sample(100, balance='1100'), ACCOUNT, now=30, policy=policy)
        self.assertEqual(events, [])
        state, events = advance(state, sample(100, balance='1049'), ACCOUNT, now=40, policy=policy)
        self.assertEqual([e['kind'] for e in events], ['foreman_pause_required'])
        self.assertEqual(events[0]['reason'], 'waiting_spend_threshold')
        self.assertEqual(state['episode']['waitingSpendUsd'], '8.04')

    def test_stale_meter_requests_pause_when_authorized(self):
        policy = {'usdPerCredit': '0.04', 'pauseWhenUnmetered': True}
        state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
        state, events = advance(state, sample(100), ACCOUNT, now=70, policy=policy)
        self.assertEqual(events[-1]['reason'], 'unmetered_confirmation_wait')

    def test_meter_recovers_observed_debits_across_a_read_outage(self):
        policy = {'usdPerCredit': '0.04', 'pauseWhenUnmetered': False}
        state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
        state, events = advance(state, sample(100, balance='790'), ACCOUNT, now=70, policy=policy)
        self.assertEqual(events[-1]['reason'], 'waiting_spend_threshold')
        self.assertEqual(state['episode']['waitingSpendUsd'], '8.40')

    def test_restored_quota_cannot_create_a_spend_pause(self):
        policy = {'usdPerCredit': '0.04'}
        state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
        state, events = advance(state, sample(0, balance='700'), ACCOUNT, now=20, policy=policy)
        self.assertEqual(events, [])
        self.assertEqual(state['episode']['budgetStatus'], 'allowance_restored_pending_rearm')

    def test_exact_release_and_restored_usage_are_both_required_to_rearm(self):
        state, _ = advance({}, sample(100, resets=0), ACCOUNT, now=10)
        release = {'episodeId': state['episode']['id'], 'accountId': ACCOUNT['accountId'],
                   'evidenceReference': 'actual-release-reference', 'holdReleased': True}
        self.assertTrue(settle(state, sample(100), release)['episode']['active'])
        available = sample(0); available['rate_limit']['allowed'] = True
        wrong = dict(release, episodeId='old-episode')
        self.assertTrue(settle(state, available, wrong)['episode']['active'])
        restored = settle(state, available, release)
        self.assertFalse(restored['episode']['active'])
        later, events = advance(restored, sample(100, resets=0), ACCOUNT, now=50)
        self.assertEqual(events[0]['kind'], 'foreman_pause_required')
        self.assertNotEqual(later['episode']['id'], state['episode']['id'])

    def test_outage_cannot_create_exhaustion_but_can_pause_an_existing_episode(self):
        state, events = observation_failed({}, {'pauseWhenUnmetered': True})
        self.assertEqual(events, [])
        state, _ = advance({}, sample(100), ACCOUNT, now=10)
        state, events = observation_failed(state, {'pauseWhenUnmetered': True})
        self.assertEqual(events[0]['reason'], 'observation_failed_during_confirmed_exhaustion')

    def test_losing_applicable_resets_during_wait_requests_pause(self):
        policy = {'usdPerCredit': '0.04'}
        state, _ = advance({}, sample(100), ACCOUNT, now=10, policy=policy)
        state, events = advance(state, sample(100, resets=0), ACCOUNT, now=20, policy=policy)
        self.assertEqual(events[0]['reason'], 'no_applicable_reset')

    def test_daybreak_without_reset_does_not_pause_or_count_unapproved_wait(self):
        account = dict(ACCOUNT, name='daybreak')
        policy = {'usdPerCredit': '0.04'}
        state, events = advance({}, sample(100, resets=0), account, now=10, policy=policy)
        self.assertEqual(events, [])
        state, events = advance(state, sample(100, resets=0, balance='500'), account, now=20, policy=policy)
        self.assertEqual(events, [])
        self.assertEqual(state['episode']['budgetStatus'], 'no_reset_confirmation_pending')
        state, events = advance(state, sample(100, resets=1, balance='500'), account, now=30, policy=policy)
        self.assertEqual([e['kind'] for e in events], ['reset_confirmation_required'])
        state, events = advance(state, sample(100, resets=1, balance='300'), account, now=40, policy=policy)
        self.assertEqual(events[-1]['reason'], 'waiting_spend_threshold')

    def test_daybreak_losing_reset_does_not_immediately_pause(self):
        account = dict(ACCOUNT, name='daybreak')
        policy = {'usdPerCredit': '0.04'}
        state, _ = advance({}, sample(100), account, now=10, policy=policy)
        state, events = advance(state, sample(100, resets=0, balance='999'), account, now=20, policy=policy)
        self.assertEqual(events, [])

    def test_daybreak_when_also_main_retains_main_no_reset_rule(self):
        account = dict(ACCOUNT, name='daybreak', alsoCurrentMain=True)
        state, events = advance({}, sample(100, resets=0), account, now=10)
        self.assertEqual(events[0]['reason'], 'no_applicable_reset')

    def test_daybreak_becoming_main_during_same_episode_enables_main_rule(self):
        account = dict(ACCOUNT, name='daybreak')
        state, _ = advance({}, sample(100, resets=0), account, now=10)
        state, events = advance(state, sample(100, resets=0), dict(account, alsoCurrentMain=True), now=20)
        self.assertEqual(events[0]['reason'], 'no_applicable_reset')

if __name__ == '__main__':
    unittest.main()
