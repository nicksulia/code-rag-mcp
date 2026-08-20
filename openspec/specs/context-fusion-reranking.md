# Specification: Context Fusion, RRF Reranking & Packaging

## Status: ACTIVE
## Domain: Context Engineering

---

## 1. Overview
The Context Fusion engine merges results from dense vector search, sparse BM25 lexical search, and graph expansion using Reciprocal Rank Fusion (RRF). It filters duplicates, applies cross-encoder reranking or heuristic relevance scoring, and packages snippets into formatted context windows with exact file citations and line ranges.

---

## 2. Requirements

### 2.1 Reciprocal Rank Fusion (RRF)
- Compute score for chunk $d$:
  $$RRF(d) = \sum_{m \in M} \frac{w_m}{k + \text{rank}_m(d)}$$
  where $M = \{\text{dense}, \text{sparse}\}$, $k = 60$, $w_{\text{dense}} = 1.0$, $w_{\text{sparse}} = 0.8$.
- Apply symbol name boost: +0.25 bonus if query terms exactly match chunk symbol name.

