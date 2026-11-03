// Final provider read must independently prove quota and credit eligibility.
export function eligibleUsage(usage) {
  const exhausted = ['primary_window', 'secondary_window'].some(key => {
    const used = usage?.rate_limit?.[key]?.used_percent;
    return typeof used === 'number' && Number.isFinite(used) && used >= 100;
  });
  const count = usage?.rate_limit_reset_credits?.applicable_available_count;
  return exhausted && Number.isInteger(count) && count > 0;
}

export function eligibleCredit(credit, now) {
  if (credit?.status !== 'available' || credit?.reset_type !== 'codex_rate_limits' ||
      credit?.is_supported_by_plan !== true) return false;
  if (credit.expires_at == null) return true;
  if (typeof credit.expires_at !== 'string' || !credit.expires_at.trim()) return false;
  const expires = Date.parse(credit.expires_at);
  return Number.isFinite(expires) && expires > now;
}

