# FixFlow AI — Phase 2E.3 offline migration preflight

**Read-only preparation, not an index rebuild.** This package adds one script. It does not install packages, contact OpenAI, modify `local_index`, create FAISS vectors, or authorize SOP disclosure.

## Run on Windows PowerShell

From `C:\Users\Admin\Desktop\nexora\fixflow-ai`:

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\FixFlow_Phase2E3_Offline_Migration_Preflight.zip" -DestinationPath . -Force
python scripts/prepare_customer_index_migration.py 2>&1 | Tee-Object -FilePath phase2e3_preflight_console.txt
```

Upload `phase2e3_migration_preflight.json` and/or the console output.

## What is checked

- The approved excerpt allowlist and IDs are internally consistent.
- Source PDF files exist and referenced page numbers are within their bounds (requires installed `pypdf`).
- The old index manifest digest is consistent, and its FAISS file exists.
- The planned **customer-only** index has 12 approved passages and **zero raw internal SOP chunks**.

## What is deliberately NOT checked

- Whether curated paraphrases are accurate against the underlying source text, or approved for external disclosure. **A human must verify this.**
- Retrieval quality or runtime authorization of every downstream citation consumer.
- Embedding model dimensions or compatibility with the existing FAISS index.

## Critical migration safety

`backend/rag/hybrid.py` Phase 2E `build()` currently embeds both raw internal SOP chunks and approved customer excerpts. **Do not run it as part of a customer-only migration.** It would send confidential raw SOP text to the configured embedding provider. A future migration must add a dedicated customer-only builder, stage a complete index pair, verify FAISS/manifest consistency, and atomically activate with the API/worker stopped and rollback available. This is not included here because it requires explicit approval and further testing.

Do not push to GitHub or deploy yet. Keep the known portal UI tests deferred, not marked as resolved.
