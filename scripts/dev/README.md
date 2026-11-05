# Developer diagnostics

One-off maintenance and diagnostic utilities. None are needed to install, run or test the
application; setup scripts (`build_index.py`, `create_admin.py`, `provision_local.py`,
`rebuild_customer_index_staging.py`) stay in `scripts/`.

| Script | Purpose |
| --- | --- |
| `audit_rag_visibility.py` | Lists which indexed passages are customer-visible or internal |
| `calibrate_tc08_band.py` | Read-only retrieval score diagnostics for the TC08 question (`--selftest` is offline) |
| `diagnose_delivery_rag.py` | Inspects retrieval for delivery questions |
| `diagnose_ticket.py` | Prints the stored state of one ticket |
| `phase9b_worker_smoke.py` | Worker smoke run with the MCP demo tools |
| `prepare_customer_index_migration.py` | Offline preflight for the approved customer index |
| `run_local_validation.py` | Local validation helper |

Run from the project root, e.g. `python scripts/dev/calibrate_tc08_band.py --selftest`.
