# Reproduce real-data retrieval checks

Download the original SciFact or NFCorpus ZIP from the official [BEIR dataset catalog](https://github.com/beir-cellar/beir/wiki/Datasets-available). Keep it outside the repository. Run from the repository root:

```sh
python3 bench/real_eval.py --runtime runtime --dataset /absolute/path/scifact.zip --out /absolute/path/scifact-evaluation.json
```

The script reads the test split, preserves original abstracts and calculates Recall@10, nDCG@10 and MRR@10 independently. It does not tune parameters or insert relevance labels into a graph. Dataset and index hashes, every query ranking and snippet checks are retained. Runtime defaults were selected separately using only SciFact's 809 training queries. See docs/VALIDATION.md for the measured results and limits.

The benchmark reports binary average precision at 10 for each query as
`ap10_fraction`. It sums precision at each retrieved positive judgment and
divides by the smaller of 10 and the number of judged positives. A query with
no positive judgment contributes zero. The arithmetic mean across all queries
is available as the reduced fraction `map10_fraction` and the number `map10`.

Pass `--baseline earlier-report.json` to compare the same judged queries in two
runs. The paired report counts wins, ties and losses, computes the exact change
in MAP@10, and shows the smallest and largest change after leaving out one
query. If that range crosses zero, removing a single query can reverse which
run leads. Both reports must contain per-query AP@10 fractions.
