import test from 'node:test';
import assert from 'node:assert/strict';
import {resetResult} from './reset_result.mjs';

test('unknown or missing reset outcomes fail without exposing response data', () => {
  for (const result of [null, {}, {code: 'new_code', privateData: 'secret'}]) {
    assert.throws(() => resetResult(result, 'same-credit', 'now'), /^Error: unknown_reset_response_code$/);
  }
});

test('recognized outcomes preserve the existing credit and omit unrelated fields', () => {
  for (const code of ['reset', 'already_redeemed', 'no_credit', 'nothing_to_reset']) {
    assert.deepEqual(resetResult({code, privateData: 'secret'}, 'same-credit', 'now'),
      {code, creditId: 'same-credit', observedAt: 'now'});
  }
});

test('a mismatched or malformed supplied credit cannot become a terminal result', () => {
  for (const id of ['other-credit', null, 7, '']) {
    assert.throws(() => resetResult({code: 'reset', credit: {id}}, 'same-credit', 'now'), /credit_identity_mismatch/);
  }
  assert.deepEqual(resetResult({code: 'reset', credit: {id: 'same-credit'}}, 'same-credit', 'now'),
    {code: 'reset', creditId: 'same-credit', observedAt: 'now'});
});
