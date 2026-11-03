# Loom: improvement during use

For authorized Loom work, review `SELF_IMPROVEMENT.md` and run `loom-lang usage review`
at entry. After each meaningful agent-assisted use, inspect the actual result
against the user's goal and an independently stated expected behavior. Record
observed wrong results, slowness or confusing output using `loom-lang usage feedback`.
An expected rejection can be correct behavior; never treat exit status or repeated
outputs as an answer oracle. Retain successful behavior when no demonstrated
improvement exists.

For a demonstrated defect, preserve a pinned portable baseline before editing,
make the smallest repair, add distinct original/transfer/control checks, and run
`loom-lang improve cycle` plus applicable release guards. A held candidate remains held.
Use the validation queue for substantial tests; preserve original job IDs and receipts.
Do not alter native case evidence, shared memory, model weights, or unrelated
projects. Do not launch background work or external uploads. Direct library uses
need an explicit task receipt because automatic collection covers the CLI only.
