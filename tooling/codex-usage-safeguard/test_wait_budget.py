import copy
import unittest

from safeguard import wait_budget


class WaitBudgetValidationTests(unittest.TestCase):
    def test_invalid_threshold_cannot_mutate_metered_or_unmetered_state(self):
        for balance in ('100', None):
            for threshold in ('bad', '0', '10'):
                with self.subTest(balance=balance, threshold=threshold):
                    episode = {'waitingCredits': '2', 'lastBalance': '110'}
                    before = copy.deepcopy(episode)
                    with self.assertRaisesRegex(ValueError, 'invalid_spend_threshold'):
                        wait_budget(episode, {'credits': {'balance': balance}}, {}, 1,
                                    {'pauseAtUsd': threshold, 'usdPerCredit': '.04'})
                    self.assertEqual(episode, before)

    def test_corrupt_waiting_credits_fail_closed_without_replacing_ledger_values(self):
        for value in (None, 'bad', '-1', 'NaN'):
            with self.subTest(value=value):
                episode = {'waitingCredits': value, 'lastBalance': '110'}
                before = copy.deepcopy(episode)
                with self.assertRaisesRegex(ValueError, 'invalid_waiting_credits'):
                    wait_budget(episode, {'credits': {'balance': '100'}}, {}, 1,
                                {'pauseAtUsd': '8', 'usdPerCredit': '.04'})
                self.assertEqual(episode, before)
