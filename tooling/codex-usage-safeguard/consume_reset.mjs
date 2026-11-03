// Operator-only one-reset request; never called by the polling service.
import {readFileSync} from 'node:fs';
import {providerFetch} from './http.mjs';
import {readCredentials} from './credentials.mjs';
import {resetResult} from './reset_result.mjs';
import {eligibleUsage, eligibleCredit} from './reset_eligibility.mjs';

try {
  const request = JSON.parse(readFileSync(0, 'utf8'));
  for (const key of ['accountId', 'email', 'episodeId', 'idempotencyKey', 'creditId', 'confirmationReference']) {
    if (typeof request[key] !== 'string' || !request[key].trim()) throw new Error('invalid_request');
  }
  const expected = JSON.parse(readFileSync(process.argv[2], 'utf8')).accounts.find(a => a.name === 'daybreak');
  if (!expected || request.email !== expected.email || request.accountId !== expected.accountId) {
    throw new Error('wrong_account');
  }
  const {email, id, token} = readCredentials(expected.authFile);
  if (email !== request.email || id !== request.accountId) {
    throw new Error('credential_identity_mismatch');
  }
  const headers = {authorization: `Bearer ${token}`, accept: 'application/json', 'ChatGPT-Account-Id': request.accountId};
  async function api(path, options = {}) {
    const response = await providerFetch('https://chatgpt.com/backend-api/wham/' + path, {
      redirect: 'error', signal: AbortSignal.timeout(15000), ...options,
      headers: {...headers, ...(options.headers ?? {})}
    });
    if (!response.ok) throw new Error('http_error');
    return await response.json();
  }
  const usage = await api('usage');
  if (usage.account_id !== request.accountId || usage.email !== request.email) throw new Error('response_identity_mismatch');
  if (!eligibleUsage(usage)) throw new Error('not_eligible');
  const inventory = await api('rate-limit-reset-credits');
  const credit = inventory.credits?.find(c => c.id === request.creditId);
  if (!eligibleCredit(credit, Date.now())) throw new Error('credit_not_available');
  const result = await api('rate-limit-reset-credits/consume', {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({credit_id: request.creditId, redeem_request_id: request.idempotencyKey})
  });
  // Do not echo arbitrary response bodies or any credential data.
  console.log(JSON.stringify(resetResult(result, request.creditId, new Date().toISOString())));
} catch {
  console.log(JSON.stringify({error: 'reset_not_confirmed_check_saved_attempt'}));
  process.exitCode = 1;
}
