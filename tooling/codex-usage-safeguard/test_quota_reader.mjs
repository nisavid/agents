import test from 'node:test';
import assert from 'node:assert/strict';
import {readProfile} from './quota_reader.mjs';

const A = {email: 'a@example.test', id: 'account-a', token: 'token-a'};
const B = {email: 'b@example.test', id: 'account-b', token: 'token-b'};
const ok = c => ({ok: true, status: 200, json: async () => ({email: c.email, account_id: c.id, rate_limit: {}})});

test('default identity is discovered and server-validated without a pinned account', async () => {
  const result = await readProfile({readCredentials: () => A, fetch: async (url, options) => {
    assert.equal(options.headers.authorization, 'Bearer token-a');
    assert.equal(options.headers['ChatGPT-Account-Id'], 'account-a');
    return ok(A);
  }});
  assert.equal(result.email, A.email);
  assert.equal(result.accountId, A.id);
});

test('an unchanged credential receives only one request after 401', async () => {
  let calls = 0;
  await assert.rejects(readProfile({readCredentials: () => A, fetch: async () => {
    calls++; return {ok: false, status: 401};
  }}), /http_401/);
  assert.equal(calls, 1);
});

test('401 with a changed credential retries once using the new matched account', async () => {
  let current = A; let calls = 0;
  const result = await readProfile({readCredentials: () => current, fetch: async (url, options) => {
    calls++;
    if (calls === 1) { current = B; return {ok: false, status: 401}; }
    assert.equal(options.headers.authorization, 'Bearer token-b');
    assert.equal(options.headers['ChatGPT-Account-Id'], 'account-b');
    return ok(B);
  }});
  assert.equal(calls, 2); assert.equal(result.accountId, B.id);
});

test('pinned Daybreak rejects credential changes and foreign server identity', async () => {
  let calls = 0;
  await assert.rejects(readProfile({readCredentials: () => B, expected: A, fetch: async () => { calls++; }}), /identity_mismatch/);
  assert.equal(calls, 0);
  await assert.rejects(readProfile({readCredentials: () => A, expected: A, fetch: async () => ok(B)}), /response_identity_mismatch/);
});

test('spending URL, header and token share the same discovered identity', async () => {
  let calls = 0;
  await assert.rejects(readProfile({readCredentials: () => B, mode: 'spend', fetch: async (url, options) => {
    calls++;
    assert.equal(options.headers.authorization, 'Bearer token-b');
    assert.equal(options.headers['ChatGPT-Account-Id'], B.id);
    if (calls === 1) return ok(B);
    assert.ok(url.includes('/accounts/account-b/spend-controls/'));
    return {ok: false, status: 401};
  }}), /http_401/);
  assert.equal(calls, 2);
});

test('a successful response for an account switched away from is discarded', async () => {
  let current = A; let calls = 0;
  const result = await readProfile({readCredentials: () => current, fetch: async () => {
    calls++;
    if (calls === 1) {current = B; return ok(A);}
    return ok(B);
  }});
  assert.equal(result.accountId, B.id); assert.equal(calls, 2);
});

test('repeated credential changes do not cause unbounded 401 retries', async () => {
  let current = A; let calls = 0;
  await assert.rejects(readProfile({readCredentials: () => current, fetch: async () => {
    calls++; current = current === A ? B : A;
    return {ok: false, status: 401};
  }}), /http_401/);
  assert.equal(calls, 2);
});
