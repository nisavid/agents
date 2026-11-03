import test from 'node:test';
import assert from 'node:assert/strict';
import {eligibleUsage, eligibleCredit} from './reset_eligibility.mjs';

test('final eligibility rejects malformed applicability and expiry', () => {
  const usage = {rate_limit: {primary_window: {used_percent: 100}},
    rate_limit_reset_credits: {applicable_available_count: 1}};
  const credit = {status: 'available', reset_type: 'codex_rate_limits', is_supported_by_plan: true,
    expires_at: '2030-01-01T00:00:00Z'};
  const now = Date.parse('2026-01-01T00:00:00Z');
  assert.equal(eligibleUsage(usage), true);
  assert.equal(eligibleCredit(credit, now), true);
  for (const count of [true, '1', 0, -1, 1.5]) {
    assert.equal(eligibleUsage({...usage, rate_limit_reset_credits: {applicable_available_count: count}}), false);
  }
  for (const expiry of ['invalid', '', 1, '2020-01-01T00:00:00Z']) {
    assert.equal(eligibleCredit({...credit, expires_at: expiry}, now), false);
  }
  assert.equal(eligibleCredit({...credit, expires_at: null}, now), true);
  assert.equal(eligibleUsage({...usage, rate_limit: {primary_window: {used_percent: '100'}}}), false);
});
