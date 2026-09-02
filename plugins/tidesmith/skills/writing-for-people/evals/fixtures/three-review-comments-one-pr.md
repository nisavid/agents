# Brief: three inline review comments on a CSV export PR

You are reviewing a colleague's pull request that adds a CSV export
endpoint to a web service. You read the diff. You did not run the code or
the tests. The PR description says the author "tested locally on a 200-row
fixture". You will post three inline comments, one per finding below.

## Finding A: unbounded read in `export_csv` (blocks merge)

In `export/handlers.py`, `export_csv` does `rows = list(query.all())`
before it starts writing, so the whole result set is held in memory. The
query targets the `orders` table, which the schema notes in
`docs/schema.md` put at roughly 40 million rows in production. The handler
already returns a `StreamingResponse`, so the fix is to iterate the query
in chunks with `query.yield_per(5000)` and write rows as they arrive; memory
then stays flat regardless of table size. You want this fixed before merge.
You have not observed the handler fail; a 200-row fixture would never show
the problem.

## Finding B: unused imports (cleanup)

`export/handlers.py` imports `json` and `Optional`. Neither is used
anywhere in the file. Trivial to drop.

## Finding C: parameter name (judgment call, not blocking)

The new endpoint names its format query parameter `fmt`. The two existing
endpoints in the same file, `export_json` and `export_xlsx`, name the same
parameter `format`. `format` shadows a Python builtin, which is a plausible
reason the author chose `fmt`, and the existing endpoints already live with
that shadowing. You lean toward `format` for consistency with the existing
API surface, but you would not hold the PR over it.
