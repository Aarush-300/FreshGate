# Evaluating the evidence for a claim

The verifier builds a query from the normalized claim and searches with Tavily. Optional Wikipedia, GitHub and academic search clients can supplement retrieval according to configuration and claim type. A retrieved result needs a usable URL and snippet to enter source evaluation. Publication dates can be missing.

## How a source affects judgment

A source record carries a URL, domain, snippet, publication date when available, authority and primary-source indicators. The verifier selects independent evidence before judging. An enforced policy can exclude sources by domain, authority or age and require a minimum source count. If too few sources qualify, the result explains the evidence gap.

The judge receives the claim and selected material. It can classify evidence as supporting or contradicting, explain the verdict, and propose a correction. Source references from the judgment are matched against retrieved URLs. If judging fails, a fallback can attempt an evidence-based result or return an unverifiable verdict. The recorded method identifies that path.

## Reviewing a disputed result

Suppose an article quotes an old price and a vendor page lists a new price. First match product, tier, currency, region and billing period. Then check the effective date on the page. A recently retrieved snippet can still describe an older offer. Open the cited source before using a suggested correction. If the policy excluded the vendor page, inspect its eligibility rules and preview a change in Policy Studio.

The [verifier](../core/verifier.py) handles retrieval and judgment. [Source evaluation](../core/sources.py) supplies source attributes. The [scoring guide](07_scoring_methodology.md) explains how those attributes influence the numerical result.
