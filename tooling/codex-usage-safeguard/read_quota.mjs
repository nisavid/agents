import {readFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {providerFetch} from './http.mjs';
import {readProfile} from './quota_reader.mjs';

// --default ignores CODEX_HOME; other profiles must pin email and account ID.
const args = process.argv.slice(2);
const dynamic = args[0] === '--default';
const path = dynamic ? join(homedir(), '.codex', 'auth.json') : args[0];
const mode = dynamic ? (args[1] ?? 'usage') : (args[3] ?? 'usage');
const expected = dynamic ? null : {email: args[1], id: args[2]};
function readCredentials() {
  const stored = JSON.parse(readFileSync(path, 'utf8'));
  const token = stored.tokens.access_token;
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString());
  const email = claims.email ?? claims['https://api.openai.com/profile']?.email;
  const id = stored.tokens.account_id;
  const claimed = claims['https://api.openai.com/auth']?.chatgpt_account_id;
  if (claimed && claimed !== id) throw new Error('credential_account_mismatch');
  return {email, id, token};
}
try {
  console.log(JSON.stringify(await readProfile({readCredentials, fetch: providerFetch, mode, expected})));
} catch (error) {
  const safe = /^(identity_mismatch|response_identity_mismatch|credential_account_mismatch|credentials_changed|http_\d+)$/.test(error.message);
  console.log(JSON.stringify({error: safe ? error.message : 'read_failed', queryIdentity: error.queryIdentity ?? null,
                             code: error.cause?.code ?? null}));
  process.exitCode = 1;
}
