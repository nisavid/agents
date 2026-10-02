// Decode the existing credential file freshly; never refresh or change it.
import {readFileSync} from 'node:fs';

export function credentialIdentity(stored) {
  const token = stored.tokens.access_token;
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString());
  const email = claims.email ?? claims['https://api.openai.com/profile']?.email;
  const id = stored.tokens.account_id;
  const claimed = claims['https://api.openai.com/auth']?.chatgpt_account_id;
  if (claimed && claimed !== id) throw new Error('credential_account_mismatch');
  return {email, id, token};
}

export function readCredentials(path) {
  return credentialIdentity(JSON.parse(readFileSync(path, 'utf8')));
}
