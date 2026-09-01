# Changelog

## 1.0.0

- Use Agent Plugins v1 as the canonical package format, with a native Claude
  manifest projection and skill-local Codex metadata.
- Bind create, text, and ready publication to an explicit review mode, exact
  publication candidate, trust-anchored Task Witness observation, and v3
  append-only receipt; make required mode fail closed when that chain is absent.
- Carry the exact review mode and publication-candidate digest through Graphite
  schema-v2 repair so legacy, reconciled, or weaker receipts cannot converge.
- Reconcile writer and publisher behavior with the pinned personal source:
  reviewer-visible merge-base statistics, grammatical and executable navigation,
  field-scoped mutations, explicit extension and concurrency boundaries, and
  retained behavioral and routing evals.
- Publish portable Mergecraft PR lifecycle skills and validation corpus.
- Reconcile the review-writing cluster with the pinned personal source at dotfiles
  f700471: fix failure narratives and shared-scenario deduplication in the
  writer contract, the clean-diff approval request for CodeRabbit, and ledger
  dispositions for the reviewer-side succession and writing-style ownership.
