# Technical Design: Advanced Multi-Vector & BM25F Indexing Engine

- **Change ID**: `02-advanced-indexing-model`
- **Status**: DESIGNED

---

## 1. Architecture

```mermaid
flowchart TD
    subgraph Chunk["Code Chunk Input"]
        Sig["Signature & Docstring (Intent)"]
        Body["Code Implementation Body"]
    end

    subgraph EmbeddingEngine["Embedding Engine (384-dim / Pluggable)"]
        Subword["Positional Syntax-Weighted Neural Subword"]
        Gemini["Gemini text-embedding-004"]
        OpenAI["OpenAI text-embedding-3"]
    end

    subgraph VectorStore["Multi-Vector Store"]
        V1["V_sig (Signature Vector)"]
        V2["V_body (Body Vector)"]
    end

    subgraph BM25FStore["BM25F Store"]
        F1["Field: symbol_name (w=4.0)"]
        F2["Field: signature (w=2.5)"]
        F3["Field: docstring (w=2.0)"]
        F4["Field: code_body (w=1.0)"]
    end

    subgraph GraphCentrality["Code Graph Centrality"]
        InDeg["In-Degree / Cross-Repo References"]
    end

    Sig --> EmbeddingEngine --> V1
    Body --> EmbeddingEngine --> V2
    Sig & Body --> BM25FStore
    
    Query["Search Query Q"] --> DenseSearch["Dual Vector Cosine (0.6*V_sig + 0.4*V_body)"]
    Query --> BM25FSearch["BM25F Field Ranking"]
    DenseSearch & BM25FSearch --> Fusion["Dynamic RRF Fusion + Graph Centrality"]
    InDeg --> Fusion
    Fusion --> Output["Ranked Search Results"]
```

---

## 2. Mathematical Formulations

### 2.1 Positional Syntax-Weighted Embedding
- 384-dimensional dense representation:
  - Tokens assigned positional and grammatical weights:
    - Symbol identifiers: $\times 3.0$
    - Type annotations & Keywords: $\times 2.5$
    - Docstrings & Parameter names: $\times 2.0$
    - Code body tokens: $\times 1.0$
  - Log term frequency damping: $tf_{\text{damped}} = 1 + \ln(tf)$
  - $L_2$ normalization ensures $\cos(\vec{u}, \vec{v}) = \vec{u} \cdot \vec{v}$.

### 2.2 BM25F Field-Weighted Lexical Ranking
- Field weights: $w_{\text{sym}} = 4.0, w_{\text{sig}} = 2.5, w_{\text{doc}} = 2.0, w_{\text{body}} = 1.0$
- Per-field length normalization parameters: $b_f \in [0.5, 0.85]$
- Robertson-Spärck Jones IDF with smoothing.
