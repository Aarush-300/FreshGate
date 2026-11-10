# FreshGate: Scoring Methodology Specification

## 1. Mathematical Scoring Foundation

FreshGate decouples factual accuracy from evidence freshness by evaluating four distinct dimensions:

$$\text{Score} = \text{clamp}_{[0, 100]}\left( \text{round}\left( 100 \times V_q \times R \times C \times (0.80 + 0.20 \times A) \right) \right)$$

Where:
- $V_q \in \{1.0, 0.70, 0.50, 0.10\}$: Verdict Quality
- $R \in [0.50, 1.0]$: Evidence Recency Factor
- $A \in [0.30, 1.0]$: Composite Source Authority Factor
- $C \in [0.40, 1.0]$: Consensus & Agreement Factor

---

## 2. Dimension Formulations

### 2.1 Verdict Quality ($V_q$)
The fundamental truth status determined by the LLM Judge against web evidence:
$$V_q = \begin{cases} 
1.00 & \text{if verdict} = \text{confirmed} \\ 
0.70 & \text{if verdict} = \text{partially\_supported} \\ 
0.50 & \text{if verdict} = \text{unverifiable} \\ 
0.10 & \text{if verdict} = \text{contradicted} 
\end{cases}$$

### 2.2 Evidence Recency Factor ($R$)
Calculated from the publication age of the freshest retrieved supporting source relative to current time $t_{\text{now}}$:
Let $\Delta t$ be the age in days:
$$R = \begin{cases} 
1.00 & \text{if } \Delta t \le 30 \text{ days} \\ 
1.00 - 0.50 \times \left(\frac{\Delta t - 30}{335}\right) & \text{if } 30 < \Delta t \le 365 \text{ days} \\ 
0.50 & \text{if } \Delta t > 365 \text{ days or undated} 
\end{cases}$$

### 2.3 Source Authority Multiplier ($A$)
Combines the average authority score of all independent sources, incorporating the primary domain bonus:
$$A = \min\left(1.0, \frac{1}{N}\sum_{i=1}^N \text{authority\_score}(s_i) + \mathbb{I}_{[\text{has\_primary}]} \times 0.10 \right)$$
The authority factor is scaled as a bounded multiplier:
$$\text{AuthorityMultiplier} = 0.80 + 0.20 \times A \in [0.86, 1.00]$$

### 2.4 Consensus & Agreement Factor ($C$)
Measures independent source corroboration without double-counting syndication:
$$C = \max\left(0.40, \min\left(1.0, \frac{N_{\text{agreeing}}}{3.0}\right)\right)$$
- If $\ge 3$ independent sources agree: $C = 1.0$.
- If 1 source: $C = \max(0.40, 0.33) = 0.40$.
- If 2 sources: $C = 0.67$.

---

## 3. Numerical Derivations for Canonical Scenarios

### Scenario A: Confirmed Pricing Claim with Primary Vendor Citation
- Claim: *"ChatGPT Plus costs $20 per month"*
- Verdict: `confirmed` $\implies V_q = 1.0$
- Evidence Age: 10 days old $\implies R = 1.0$
- Sources: `openai.com/pricing` (primary vendor: $0.90 + 0.10 = 1.0$), `reuters.com` ($0.85$). $N=2$
- Agreement: 2 sources $\implies C = \min(1.0, 2/3) = 0.67$
- Composite:
  $$\text{Score} = 100 \times 1.0 \times 1.0 \times 0.67 \times (0.80 + 0.20 \times 0.925) \approx \mathbf{66} \dots \mathbf{95} \text{ (with 3 sources: } \mathbf{98}\text{)}$$

### Scenario B: Contradicted Deprecated Software Claim with Old Sources
- Claim: *"Python 2.7 is the currently supported version"*
- Verdict: `contradicted` $\implies V_q = 0.10$
- Source: 400 days old $\implies R = 0.50$
- Authority: $A = 0.90 \implies 0.80 + 0.20 \times 0.90 = 0.98$
- Agreement: $C = 0.67$
- Composite:
  $$\text{Score} = 100 \times 0.10 \times 0.50 \times 0.67 \times 0.98 = \mathbf{3} / 100$$
*(Guaranteed to score $< 15$, accurately flagging the response as severely stale).*

### Scenario C: Fabricated Startup Claim (Unverifiable)
- Claim: *"AcmeNeuro achieved 800 petaflops"*
- Verdict: `unverifiable` $\implies V_q = 0.50$
- Agreement: 0 sources $\implies C = 0.40$
- Recency: Undated $\implies R = 0.50$
- Authority: $A = 0.50 \implies 0.90$
- Composite:
  $$\text{Score} = 100 \times 0.50 \times 0.50 \times 0.40 \times 0.90 = \mathbf{9} \dots \mathbf{50} \text{ (baseline unverified)}$$
