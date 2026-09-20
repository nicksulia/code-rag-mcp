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
  where $M = \{\text{dense}, \text{sparse}\}$, $k = 60$, $w_{\text{dense}} = 1.0$, $w_{\text{sparse}} = 0.85$.
- Apply symbol name boost: +0.020 bonus if the query and the chunk's symbol name match (either contains the other, case-insensitive).
- Apply code graph centrality boost for the chunk's symbol (shared libraries & cross-repo endpoints).
- Apply a multiplicative path-class penalty to the boosted score (see the retrieval-quality spec): `test` 0.65, `fixture` 0.5, `noise` 0.4, `source` 1.0; `Example*` symbols in test files are treated as source.
- Apply multiplicative hop decay for chunks from expanded repositories.

