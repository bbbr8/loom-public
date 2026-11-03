# Reproduce real-data retrieval checks

Download the original SciFact or NFCorpus ZIP from the official [BEIR dataset catalog](https://github.com/beir-cellar/beir/wiki/Datasets-available). Keep it outside the repository. Run from the repository root:

```sh
python3 bench/real_eval.py --runtime runtime --dataset /absolute/path/scifact.zip --out /absolute/path/scifact-evaluation.json
```

The script reads the test split, preserves original abstracts and calculates Recall@10, nDCG@10 and MRR@10 independently. It does not tune parameters or insert relevance labels into a graph. Dataset and index hashes, every query ranking and snippet checks are retained. Runtime defaults were selected separately using only SciFact's 809 training queries. See docs/VALIDATION.md for the measured results and limits.
