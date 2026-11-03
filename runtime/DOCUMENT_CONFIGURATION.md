# Connecting your documents

Loom opens document stores only when you supply a configuration. Save the following
synthetic example as `documents.json`, then set `LOOM_DOCUMENT_CONFIG` to its path:

```json
{
  "families": {
    "invoices": {
      "description": "Example invoices",
      "rows": [{"id": "sample-1", "amount": "42.50", "page": "DOC_10010", "loan_file": "project-A"}]
    }
  },
  "pages": {"DOC_10010": "Synthetic invoice total: $42.50"}
}
```

`kinds()` lists your configured families. `docs("invoices")` reads the example.
`stages($42.50, "project-A")` matches that project's supplied records. `anchor_check`
searches the supplied page text and neighboring numeric page identifiers.

A family can instead contain `path` and `query`, for example
`{"path":"example.sqlite","query":"SELECT id, amount, page FROM invoices"}`.
Paths resolve relative to the configuration file. SQLite is opened read-only;
each family is limited to 10,000 rows. Use decimal strings for exact dollar amounts.
An invalid configured store raises an error rather than being silently skipped.

Timeline commands accept `--store` and `--ledger`, or `LOOM_TIMELINE_STORE` and
`LOOM_LEDGER`. The bank command is `money ledger [min AMOUNT]`.
Keep your configuration and documents outside the repository.
