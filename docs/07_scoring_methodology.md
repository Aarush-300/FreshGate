# Interpreting the freshness score

A freshness score summarizes the verdict and its evidence on a 0–100 scale. The current composite uses four factors. The pipeline also records confidence and temporal indicators for inspection.

| Factor | Implementation |
| --- | --- |
| Verdict weight | Confirmed 1.00, partially supported 0.70, unverifiable 0.50, contradicted 0.10 |
| Recency | Age of the newest parseable source date. Full weight through 30 days, declining linearly to 0.50 at 365 days. Missing dates use 0.50. |
| Source count | Count of sources on the claim divided by three, bounded between 0.40 and 1.00 |
| Authority | Mean source authority with a primary-source bonus of 0.10, bounded between 0.40 and 1.00. Empty evidence uses 0.50. |

```text
score = round(100 × verdict_weight × recency × source_count_factor
              × (0.80 + 0.20 × authority))
```

The scorer applies caps of 10 for a contradicted claim and 50 for an unverifiable claim. For example, a confirmed claim with three recent sources and a final authority factor of 0.80 scores **96**. With one equally recent source and the same authority, the source-count factor falls to 0.40 and the score becomes **38**. A supported statement can therefore have a modest score when the evidence is thin.

## Time and uncertainty

The publication date measures the age of evidence. The verification timestamp records when FreshGate checked the claim. Category half-life, degradation curve, estimated flip probability and suggested recheck interval describe how the result may age. These indicators and judge confidence are reported separately from the composite formula. Read predictions as scheduling heuristics. A measured change requires another observation.

Replay lets you compare scored snapshots and inspect changes to the underlying sources. [Scorer implementation](../core/scorer.py) · [Replay](verification-replay.md)
