# Implementation Plan: Corporate Parser with API-based OCR (No Docling, No HuggingFace)

## Context

You want to replicate the MedFactory AI parser pipeline in your corporate environment with these constraints:
- **No HuggingFace models** - must use your own corporate OCR API
- **No Docling** - cannot use the layout analysis engine
- **No external embedding models** - will use your own corporate embedding API
- **Must maintain production practices** - observability, metrics, provenance, scalability

## Goal

Build a robust, scalable document parser that:
1. Detects and loads documents (PDF, DOCX, images, etc.)
2. Extracts text/layout/tables with your corporate OCR API
3. Produces a canonical DOM with full provenance
4. Supports semantic chunking via your corporate embedding API
5. Has full observability, metrics, and batch processing

---

## Architecture Overview

```
app/
├── parser/
│   ├── config.py          # ParserConfig with OCR/Embedding API endpoints
│   ├── detection.py       # File type detection (magic bytes, content sniff)
│   ├── dom/               # Canonical DOM models (Document, Block, Table, etc.)
│   ├── loaders/           # Format loaders (PDF, DOCX, image)
│   ├── extractors/        # Text, layout, table extractors
│   ├── ocr.py             # Corporate OCR API client (NOT RapidOCR)
│   ├── storage.py         # Content-addressed storage (filesystem/S3)
│   ├── events.py          # Event publishing for observability
│   └── extraction.py      # Main orchestrator: detect → load → extract → DOM
│
├── routing/               # KEEP: Intelligent document router (optional)
│   ├── config.py          # RoutingConfig with your band thresholds
│   ├── inspectors.py      # FastInspector for cheap feature extraction
│   ├── detectors/         # 9 pluggable detectors
│   ├── scoring.py         # Complexity scoring (0-100)
│   ├── policy.py          # Band routing policy
│   └── router.py          # Router: decision layer only
│
├── normalizer/            # KEEP: DOM normalization
│   ├── config.py          # NormalizerConfig
│   ├── rules.py           # Idempotent normalization rules
│   ├── normalizer.py      # DOM → Clean DOM
│   └── cli.py             # CLI entry point
│
├── chunking/              # KEEP: Semantic chunking
│   ├── config.py          # ChunkingConfig
│   ├── chunker.py         # DOM-anchored chunker
│   ├── sentences.py       # Sentence splitting
│   ├── tokenize.py        # Token counting
│   ├── store.py           # ChunkStore seam
│   └── pipeline.py        # ChunkEmbedPipeline
│
├── embedding/             # EMBEDDING SEAM (use your API)
│   ├── embedder.py        # Embedder protocol (list-in → vectors-out)
│   ├── api_client.py      # YOUR: Corporate embedding API client
│   └── factory.py         # default_embedder() picking your embedder
│
└── processing/            # KEEP: Batch execution layer
    ├── config.py          # ProcessingConfig (concurrency, manifest)
    ├── corpus.py          # Parallel hashing, manifest management
    ├── executor.py        # ThreadPoolExecutor worker pool
    └── cli.py             # Batch processing CLI
```

---

## Critical Files to Modify

### 1. `app/parser/config.py` - Add your API configuration

**Current (your baseline to adapt):**
```python
@dataclass(frozen=True)
class ParserConfig:
    parser_version: str = "parser-v0.1.0"
    ocr_enabled: bool = True
    ocr_lang: str = "en"
    layout_backend: str = "auto"  # Will be "native" only for you
```

**Your modifications:**
```python
@dataclass(frozen=True)
class ParserConfig:
    # ... existing fields ...
    
    # OCR API configuration
    ocr_api_url: str = "https://your-corporate-ocr-api/v1/ocr"
    ocr_api_key: str = ""  # From env
    ocr_timeout_seconds: int = 30
    
    # Layout backend: only "native" for you (no Docling)
    layout_backend: str = "native"  # Hardcoded, no "auto" or "docling"
    
    # Optional: routing config (if using intelligent routing)
    routing: Optional["RoutingConfig"] = None
```

---

### 2. `app/parser/ocr.py` - Replace RapidOCR with your OCR API

**What to keep:**
- `OCRProvider` protocol interface
- `LazyOCREngine` pattern (load only when needed)
- `batch_ocr_bytes()` for batch processing
- `warm()` for engine preloading
- Deterministic, idempotent behavior

**What to replace:**
- All RapidOCR-specific code → HTTP client to your corporate OCR API
- Response parsing → your API's JSON schema

**Your implementation pattern:**
```python
from dataclasses import dataclass
from typing import Protocol, Optional, Tuple
import httpx
import asyncio

@dataclass
class OCRResult:
    text: str
    confidence: float
    layout: list  # Your API's block/line/word structure
    engine_version: str = "corporate-ocr-v1"

class OCRProvider(Protocol):
    def ocr_bytes(self, data: bytes, lang: str = "en") -> OCRResult: ...
    def batch_ocr_bytes(self, images: list[bytes], lang: str = "en") -> list[OCRResult]: ...

class CorporateOCREngine:
    """OCR engine using your corporate API."""
    
    def __init__(self, api_url: str, api_key: str, timeout: int = 30):
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout
            )
        return self._client
    
    async def ocr_bytes(self, data: bytes, lang: str = "en") -> OCRResult:
        """Single image OCR via your API."""
        client = await self._get_client()
        response = await client.post(
            f"{self.api_url}/ocr",
            files={"file": ("image", data, "application/octet-stream")},
            data={"lang": lang}
        )
        response.raise_for_status()
        result = response.json()
        
        return OCRResult(
            text=result["text"],
            confidence=result.get("confidence", 1.0),
            layout=result.get("layout", []),
            engine_version="corporate-ocr-v1"
        )
    
    async def batch_ocr_bytes(self, images: list[bytes], lang: str = "en") -> list[OCRResult]:
        """Batch OCR - sends multiple images in one request."""
        client = await self._get_client()
        files = [(f"image_{i}", ("img.jpg", img, "image/jpeg")) 
                 for i, img in enumerate(images)]
        
        response = await client.post(
            f"{self.api_url}/batch-ocr",
            files=files,
            data={"lang": lang}
        )
        response.raise_for_status()
        results = response.json()["results"]
        
        return [OCRResult(
            text=r["text"],
            confidence=r.get("confidence", 1.0),
            layout=r.get("layout", []),
            engine_version="corporate-ocr-v1"
        ) for r in results]
```

**Key points:**
- Use `httpx.AsyncClient` for async batch processing
- Add retry logic for transient failures
- Log all API calls for observability
- Return `OCRResult` with your API's layout structure

---

### 3. `app/parser/loaders/pdf_loader.py` - Modify PDF loader

**What to keep:**
- PyMuPDF (`fitz`) for loading PDFs (still valid - it's just a PDF parser)
- Document metadata extraction
- Image extraction from PDF
- Table detection (your choice of heuristic or your API)

**What to modify:**
- Replace OCR calls with your `CorporateOCREngine`
- Remove Docling path entirely

**Pattern:**
```python
from app.parser.ocr import CorporateOCREngine
from app.parser.parts import RecoveredDocument, RecoveredBlock

class PDFLoader:
    def __init__(self, config: ParserConfig, ocr_engine: Optional[CorporateOCREngine] = None):
        self.config = config
        self.ocr_engine = ocr_engine  # Your OCR API client
    
    async def load(self, data: bytes, route: str = "native") -> RecoveredDocument:
        import fitz
        
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            pages = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # Extract text (native PyMuPDF)
                text_blocks = self._extract_text_blocks(page)
                
                # If no text blocks, OCR the page (your corporate API)
                if not text_blocks and self.ocr_engine:
                    pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
                    img_data = pix.tobytes("png")
                    ocr_result = await self.ocr_engine.ocr_bytes(img_data)
                    text_blocks = self._convert_ocr_to_blocks(ocr_result, page_num)
                
                # Extract images
                images = self._extract_images(page, page_num)
                
                # Extract tables (heuristic or your API)
                tables = self._extract_tables(page) if self.config.pdf_extract_tables else []
                
                pages.append(Page(
                    index=page_num,
                    width=page.rect.width,
                    height=page.rect.height,
                    blocks=text_blocks + [RecoveredBlock(...) for img in images],  # Combine
                    images=images,
                    tables=tables
                ))
            
            return RecoveredDocument(
                pages=pages,
                metadata=self._extract_metadata(doc),
                layout_backend="native",  # No docling for you
                ocr_engine="corporate-ocr" if any(page.uses_ocr for page in pages) else None
            )
        finally:
            doc.close()
```

---

### 4. `app/embedding/embedder.py` - Create API-based embedder

**What to keep:**
- `Embedder` protocol interface
- `batch_embed()` pattern for batching
- `SentenceTransformerEmbedder` as reference (replace with your client)

**What to add:**
- `CorporateEmbeddingClient` class

**Your implementation:**
```python
from dataclasses import dataclass
from typing import Protocol, List
import httpx

class Embedder(Protocol):
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...
    @property
    def name(self) -> str: ...

@dataclass
class CorporateEmbeddingClient:
    """Client for your corporate embedding API."""
    
    api_url: str
    api_key: str
    timeout: int = 30
    
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Batch embed texts via your API."""
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout
        ) as client:
            response = await client.post(
                f"{self.api_url}/embed",
                json={"texts": texts}
            )
            response.raise_for_status()
            return response.json()["embeddings"]
    
    @property
    def name(self) -> str:
        return "corporate-embedding-v1"

# In embedding/factory.py
def default_embedder() -> Embedder:
    """Return your corporate embedder."""
    return CorporateEmbeddingClient(
        api_url=os.getenv("CORPORATE_EMBED_API_URL", "https://your-embed-api/v1"),
        api_key=os.getenv("CORPORATE_EMBED_API_KEY", "")
    )
```

---

### 5. `app/processing/` - Keep batch processing (minimal changes)

**No changes needed** if your OCR/embedding APIs are async-capable.

**If your APIs are sync-only**, wrap them:
```python
# app/processing/executor.py - modify to use sync clients
def _create_sync_embedder(config: ProcessingConfig) -> Embedder:
    """Wrap async embedder for sync batch processing."""
    embedder = factory.default_embedder()
    if hasattr(embedder, 'embed_documents'):  # Is async
        return SyncEmbedderWrapper(embedder)
    return embedder

class SyncEmbedderWrapper:
    """Sync wrapper for async embedder."""
    def __init__(self, async_embedder):
        self.async_embedder = async_embedder
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        import asyncio
        return asyncio.run(self.async_embedder.embed_documents(texts))
```

---

### 6. `app/routing/` - Optional: Keep or simplify

**If keeping:** Update router to only use "native" path
```python
# app/routing/policy.py
@dataclass(frozen=True)
class RoutingPolicy:
    """Three-band routing policy."""
    
    def route(self, complexity: float, confidence: float) -> str:
        # Your corporate thresholds
        if complexity <= 30:
            return "native"  # Only native path
        elif complexity <= 60:
            return "native"  # Enrichment still native for you
        else:
            return "native"  # Docling not available - fallback to native
```

**If removing:** Delete the `app/routing/` directory entirely - the parser will default to native extraction.

---

## Implementation Steps

### Phase 1: Core Parser (4-5 days)

1. **Copy existing module structure** - Copy `app/parser/`, `app/normalizer/`, `app/chunking/`, `app/processing/` to your corporate repo

2. **Update configuration** - `parser/config.py` with your API endpoints

3. **Implement OCR client** - `parser/ocr.py` with corporate OCR API

4. **Update loaders** - `parser/loaders/pdf_loader.py` to use your OCR

5. **Implement embedding client** - `embedding/api_client.py` with corporate embedding API

6. **Test single-document pipeline** - Parse a PDF → verify DOM structure → verify OCR worked

### Phase 2: Batch Processing (2-3 days)

7. **Update embedding factory** - `embedding/factory.py` to use your client

8. **Test batch execution** - Process 100 documents → verify manifest, parallelism, error handling

9. **Add observability** - Hook events to your logging system (Prometheus/Grafana)

### Phase 3: Production Hardening (3-4 days)

10. **Error handling** - Graceful degradation, retry logic, DLQ for failures

11. **Metrics collection** - Parse latency, OCR success rate, embedding latency, error rates

12. **Provenance tracking** - Verify all API calls are logged with version info

13. **Integration tests** - Test corpus, edge cases (scanned docs, tables, images)

---

## Key Design Principles to Preserve

### 1. Idempotency
```python
# Same input bytes always produce same output
document_id = f"d-{sha256(data)[:16]}"
chunk_id = sha256(f"{doc_id}:{text}:{source_block_ids}".encode())
```

### 2. Provenance Tracking
Every DOM must carry:
```python
Provenance(
    parser_version="parser-v0.1.0",
    ocr_engine="corporate-ocr-v1",
    ocr_api_version="2024-01-15",
    layout_backend="native",
    routing=RoutingDecision(...) if applicable,
    extraction_timestamp=datetime.utcnow().isoformat()
)
```

### 3. Observability
Every parse event:
```python
{
    "event": "document.parsed.v1",
    "doc_id": "d-abc123",
    "parser_version": "parser-v0.1.0",
    "layout_backend": "native",
    "ocr_engine": "corporate-ocr-v1",
    "timings": {
        "detect_ms": 2.3,
        "load_ms": 45.1,
        "ocr_ms": 890.5,
        "build_ms": 12.3,
        "total_ms": 950.2
    },
    "blocks": 127,
    "tables": 3,
    "images": 5,
    "pages": 12
}
```

### 4. Batch Processing
- Parallel hashing for manifest
- Content-addressed storage (never overwrite same hash)
- Worker pool with retries + backoff
- Crash-safe progress (manifest flushed periodically)

---

## API Response Formats to Design

### Your OCR API should return:
```json
{
  "text": "Full extracted text...",
  "confidence": 0.92,
  "layout": [
    {
      "type": "block|paragraph|line|word",
      "text": "...",
      "bbox": [x, y, width, height],
      "confidence": 0.95
    }
  ],
  "engine_version": "2024.3.1"
}
```

### Your Embedding API should return:
```json
{
  "embeddings": [
    [0.12, -0.45, 0.67, ...],  // 1024 dims for BGE-M3 equivalent
    [-0.23, 0.11, ...]
  ],
  "model_version": "embedding-v1.2",
  "token_count": 256
}
```

---

## Testing Strategy

### Unit Tests (per module)
```bash
# Parser tests
tests/test_parser_detection.py      # File type detection
tests/test_parser_ocr.py            # OCR API client
tests/test_parser_pdf_loader.py     # PDF loading
tests/test_parser_dom.py            # DOM construction

# Normalizer tests
tests/test_normalizer_rules.py      # Rule transformations
tests/test_normalizer.py            # End-to-end normalization

# Chunking tests
tests/test_chunking_tokenizer.py    # Token counting
tests/test_chunking_chunker.py      # DOM-anchored chunking

# Integration tests
tests/test_integration_parse.py     # Full parse pipeline
tests/test_integration_batch.py     # Batch processing
```

### Test Corpus
- 10 clean digital PDFs (should use native extraction)
- 5 scanned PDFs (should trigger OCR)
- 5 mixed documents (some pages text, some scanned)
- 3 DOCX files
- 2 image files (PNG/JPG)

---

## Deployment Checklist

- [ ] Corporate OCR API endpoint configured (env vars or config file)
- [ ] Corporate embedding API endpoint configured
- [ ] Storage location (filesystem path or S3 bucket) created
- [ ] Event logging system (Prometheus, Datadog, or custom)
- [ ] Log rotation configured
- [ ] Monitoring/alerts configured (parse failure rate, OCR latency, etc.)
- [ ] Backup policy for stored DOMs and images
- [ ] Security: API keys managed via secrets manager
- [ ] Network: Allow outbound to OCR/embedding APIs

---

## Troubleshooting

### OCR returns empty text
- Check image resolution (your API may need minimum DPI)
- Verify image format (PNG/JPEG/TIFF support)
- Add logging to see actual API request/response

### Embedding batch fails
- Check token count per batch (your API may have limits)
- Verify embedding dimension matches chunking expectations
- Add retry logic for 5xx errors

### Batch processing slow
- Increase concurrency (worker pool size)
- Batch OCR calls (send multiple images per request)
- Profile where time is spent (detect, load, OCR, build)

---

## Files Reference

| Original File | Corporate Modification |
|---------------|------------------------|
| `app/parser/config.py` | Add OCR API URL/key, remove Docling fields |
| `app/parser/ocr.py` | Replace RapidOCR with CorporateOCREngine |
| `app/parser/loaders/pdf_loader.py` | Use your OCR, remove Docling path |
| `app/embedding/embedder.py` | Add CorporateEmbeddingClient |
| `app/embedding/factory.py` | Return your embedder |
| `app/routing/` | Simplify to native-only or remove |
| `app/processing/` | No changes (works as-is) |
| `app/normalizer/` | No changes (works as-is) |
| `app/chunking/` | No changes (works as-is) |

---

## Summary

**What to copy:** Module structure, DOM models, normalizer, chunking, batch processing

**What to replace:**
1. OCR engine → your corporate API client
2. Embedder → your corporate embedding API client  
3. Layout backend → hardcode "native" (no Docling)
4. Routing (optional) → simplify to native-only

**What to keep:** Provenance tracking, observability, idempotency, batch processing, testing patterns

The architecture is designed to be swappable - you're just replacing two specific components (OCR, embedding) while keeping the entire pipeline structure and quality guarantees intact.
