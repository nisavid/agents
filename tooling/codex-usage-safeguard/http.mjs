// Preserve the host's configured proxy route without depending on a CLI cache.
import {getProxyForUrl} from 'proxy-from-env';
import {fetch, ProxyAgent} from 'undici';

const agents = new Map();
export function providerFetch(url, options) {
  const proxy = getProxyForUrl(url.toString());
  if (!proxy) return fetch(url, options);
  if (!agents.has(proxy)) agents.set(proxy, new ProxyAgent(proxy));
  return fetch(url, {...options, dispatcher: agents.get(proxy)});
}
