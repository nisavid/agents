// Read-only account discovery. Never refresh credentials, switch login, or infer.
export async function readProfile({readCredentials, fetch, mode = 'usage', expected = null}) {
  if (!['usage', 'resets', 'spend'].includes(mode)) throw new Error('unknown_mode');
  let lastIdentity;
  for (let attempt = 0; attempt < 2; attempt++) {
    const {email, id, token} = readCredentials();
    lastIdentity = {email, accountId: id};
    if (!email || !id || !token || (expected && (email !== expected.email || id !== expected.id))) {
      throw Object.assign(new Error('identity_mismatch'), {queryIdentity: lastIdentity});
    }
    const endpoints = {usage: '/wham/usage', resets: '/wham/rate-limit-reset-credits',
      spend: `/accounts/${encodeURIComponent(id)}/spend-controls/current-user/monthly-usage?supports_usage_limit_modes=true`};
    const headers = {authorization: `Bearer ${token}`, accept: 'application/json', 'ChatGPT-Account-Id': id,
      'Cache-Control': 'no-store', Pragma: 'no-cache'};
    const same = current => current.id === id && current.email === email && current.token === token;
    try {
      async function get(path) {
        const response = await fetch('https://chatgpt.com/backend-api' + path, {
          headers, redirect: 'error', signal: AbortSignal.timeout(15000)
        });
        if (!response.ok) throw new Error(`http_${response.status}`);
        return await response.json();
      }
      // The existing quota endpoint returns server-authenticated identity.
      const usage = await get(endpoints.usage);
      if (usage.email !== email || usage.account_id !== id) throw new Error('response_identity_mismatch');
      const data = mode === 'usage' ? usage : await get(endpoints[mode]);
      const latest = readCredentials();
      if (latest.id !== id || latest.email !== email) {
        if (attempt === 0 && !expected) continue;
        throw new Error('credentials_changed');
      }
      const observedAt = new Date().toISOString();
      if (mode !== 'usage') return {observedAt, credentialEmail: email, credentialAccountId: id, mode, data};
      const output = {observedAt, email, accountId: id};
      for (const key of ['plan_type', 'rate_limit', 'rate_limit_reset_credits', 'credits', 'spend_control', 'model_usage', 'chatpass']) {
        output[key] = usage[key] ?? null;
      }
      return output;
    } catch (error) {
      // Only a real credential change permits one retry after a 401.
      if (error.message === 'http_401' && attempt === 0 && !same(readCredentials())) continue;
      error.queryIdentity = lastIdentity;
      throw error;
    }
  }
  throw Object.assign(new Error('credentials_changed'), {queryIdentity: lastIdentity});
}
