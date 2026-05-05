# Docs folder

Design notes, benchmarks, and reference material for Arth — **not** the marketing website.

---

## What lives where


| Folder               | Contents                                                                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `**system-design/`** | Active architecture choices — e.g. mail vs upload priority (`[INGESTION_PATHS.md](system-design/INGESTION_PATHS.md)`) |
| `**evaluations/**`   | Smart-label benchmarks, methodology, cost/accuracy trade-offs                                                         |
| `**data-notes/**`    | How raw bank formats map into Arth’s shapes                                                                           |
| `**reference/**`     | PDF frameworks (layers map, Day‑1 questions)                                                                          |
| `**archive/**`       | Older scratch notes — useful context, not gospel                                                                      |
| `**product/**`       | Living product specs when present                                                                                     |


Older brainstorm docs sit under `[archive/system-design/](archive/system-design/)`.

---

## Highlights


| File                                                                                         | Why open it                                      |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `[system-design/INGESTION_PATHS.md](system-design/INGESTION_PATHS.md)`                       | Which path wins when mail and uploads disagree   |
| `[evaluations/llm-benchmark-2026-03/README.md](evaluations/llm-benchmark-2026-03/README.md)` | Why today’s single-pass smart-label setup exists |


