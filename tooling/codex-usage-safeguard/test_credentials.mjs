import assert from 'node:assert/strict';
import {test} from 'node:test';
import {credentialIdentity} from './credentials.mjs';

function stored(claimed) {
  const claims = {'https://api.openai.com/profile': {email: 'fixture@example.test'},
    'https://api.openai.com/auth': {chatgpt_account_id: claimed}};
  return {tokens: {account_id: 'fixture-id',
    access_token: `fixture.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.not-a-real-token`}};
}

test('reader and reset helper share claimed-account validation', () => {
  assert.equal(credentialIdentity(stored('fixture-id')).email, 'fixture@example.test');
  assert.equal(credentialIdentity(stored('fixture-id')).id, 'fixture-id');
  assert.throws(() => credentialIdentity(stored('foreign-id')), /credential_account_mismatch/);
});
