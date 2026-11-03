# Collection functions

Loom 1.10 adds seven functions for analytical pipelines. They operate on lists or realm records, keep encounter order, and preserve exact numbers, money, dates, original records and source locators. The kernels live in the standard-library leaf `surd_collections.py`; the interpreter supplies language callbacks, typed keys and cooperative budget checkpoints.

| Function | Result |
| --- | --- |
| `group_by(f, xs)` | A list of `{"key": key, "items": [...]}` groups in first-key encounter order |
| `unique_by(f, xs)` | The first item for each distinct key, in encounter order |
| `partition(f, xs)` | `{"matched": [...], "rest": [...]}`, preserving order on both sides |
| `scan(f, xs, start)` | Initial accumulator followed by every running accumulator; `f(acc, item)` |
| `flat_map(f, xs)` | Concatenated callback lists, flattened exactly one level |
| `chunk(xs, size)` | Batches of positive whole-number size, with a possibly shorter final batch |
| `flatten(xs)` | One level of list flattening; scalar items and deeper nesting remain |

Unary callback functions also accept `(xs, f)`, matching `map` and `filter`. `scan` follows `reduce` and takes the function first. Callback types and arities are validated even for an empty input. `flat_map` requires each callback result to be a list.

## Group without losing key meaning

```surd
let rows = [
  {"id":"r1", "kind":"test", "amount":$0.10, "locator":"fixture/a:1"},
  {"id":"r2", "kind":"review", "amount":$0.20, "locator":"fixture/b:1"},
  {"id":"r3", "kind":"test", "amount":$0.30, "locator":"fixture/a:2"}
]
let groups = group_by(fn(row) => row["kind"], rows)
expect groups[0]["key"] == "test"
expect map(fn(row) => row["locator"], groups[0]["items"]) == ["fixture/a:1", "fixture/a:2"]
let running = scan(fn(total, row) => total + row["amount"], rows, $0)
expect running == [$0, $0.10, $0.30, $0.60]
```

Group keys use typed structural identity. Text `"1"`, exact number `1`, float `float(1)`, boolean `true` and money `$1` are separate keys. Equivalent exact expressions such as `1/3` and `2/6` share a key. Lists are ordered composite keys; maps ignore insertion order while preserving each key/value's type. Supported scalar keys are text, booleans, `none`, finite floats, exact numbers, angles, money and dates. Unsupported values such as functions, gates, records or meanings need an explicit supported projection, such as a record's id or locator.

Groups return their original typed key rather than coercing it into a string map index. Composite group keys are copied when a group is created, so a later callback mutation cannot rename an earlier group. Grouping and deduplication are caller-chosen data operations; they do not establish source independence or factual equivalence.

## Running state and mutation

`scan` includes the initial value: an empty input returns `[start]`, and n inputs yield n+1 snapshots. Lists and maps in each accumulator are copied recursively so later callback mutations cannot rewrite earlier prefixes. Other language values are retained as leaves. The original accumulator can still be changed by a mutating callback, just as with `reduce`.

All seven functions create fresh outer result containers. Input items remain the original items except for scan snapshots and copied group keys. Iteration uses a snapshot of the input's top-level items; callback appends cannot extend the same traversal. Callback side effects and earlier session changes remain if a later callback fails or a budget expires.

## Limits and execution

Inputs and each output list are bounded at 1,000,000 items. Group-key and scan-snapshot traversal shares a 2,000,000-node allowance per function call and supports structural depth through 32; cycles in these structures fail. `scan`'s initial snapshot counts toward its output limit. Chunk size must be a positive exact whole number. No approximate conversion is used to identify keys or accumulate values.

Every traversal and callback uses the current interpreter's cooperative execution budget. Use `--max-steps` and `--timeout` for additional bounds. Shape/key failures use `S-E009`, callback arity uses `S-E004`, collection size/structure limits use `S-E010`, and the shared execution budget uses `S-E017`. Native work inside callbacks retains the interpreter's existing process-limit boundary.

Run `loom-lang run examples/collections.surd`, `python3 tests/test_collections.py`, or the seeded independent reference evaluator `python3 tests/collections_oracle.py --cases 2000`. The portable release profile includes the regression suite and reference evaluator. These checks use synthetic rows.
