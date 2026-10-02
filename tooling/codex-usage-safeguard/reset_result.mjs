// Only normalized provider outcomes may leave the operator-only reset helper.
export function resetResult(result, creditId, observedAt) {
  if (!['reset', 'already_redeemed', 'no_credit', 'nothing_to_reset'].includes(result?.code)) {
    throw new Error('unknown_reset_response_code');
  }
  return {code: result.code, creditId: result.credit?.id ?? creditId, observedAt};
}
