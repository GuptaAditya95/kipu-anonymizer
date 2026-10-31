# Design Document — Kipu Pay corpus anonymization

**Challenge:** "Vende los datos sin vender a la gente" (Reto Frontier, Oct 2026)
**Build approach:** pipeline written with the Pi coding agent; runs as deterministic code behind an HTTP service.

---

## 0. Summary

Kipu Pay, a Colombian fintech, holds three years of support chats, internal documents and customer tables that a vendor wants for training customer-service models. The deal only closes if two conditions hold: **the data stays useful for training** and **no real person can be identified**.

We will build a **rules-first anonymization pipeline** that produces a 1:1 copy of the corpus (2,028 chat messages, 26 documents, 6,243 table rows). Every natural person receives **one consistent, realistic pseudonym across all sources**; quasi-identifiers are **generalized, not deleted**; business content (organizations, places, amounts, internal IDs) is kept intact.

Success is measured on three scored goals: **Leaks (45 pts)**, **Utility (35 pts)** and **Consistency (20 pts)**. The pipeline is served as a public HTTPS service that regenerates the full output in **< 2 hours** and answers `/anonymize` in **< 60 s per batch of 50 records**.

Honest target: **~85–88 with rules only** (the organizers' benchmark for a solid rules solution is ~88). A narrow Jev classification step is a stretch goal that may add a few points on recall.

---

# Pillar 1 — Understanding the problem

## 1. What the system must solve

| Input | Volume | Notes from inspecting the data |
|---|---|---|
| Chats (Slack export) | 2,028 msgs, 320 threads, 6 channels, 187 users (1 bot) | `<@UID>` mentions, spelled-out emails ("lmejia arroba hotmail punto com"), attachments with names in filenames |
| Docs | 26 (11 PDF, 6 HTML, 9 MD; 2 in English) | PDF `Author`/`Title` metadata with real names, front matter, HTML comments (`<!-- draft reviewed by … (email) -->`), names in filenames |
| `clientes.csv` | 149 rows, Latin-1, `;` | name, cédula, email, phone, city, neighborhood, address, birth date, occupation, free-text notes naming other clients |
| `transacciones.csv` | 6,030 rows | mostly merchants (Rappi, Avianca, Bancolombia) — must be kept; joins by `cliente_id` |
| `pqrs.xml` | 28 rows | cédula in attributes, agent usernames, free text with occupation + city, XML comment with an employee email |
| `empleados.xml` | 36 rows | `reporta_a="David Mejía"` (short name of another employee) |

Output: exactly 4 files (`chats.jsonl`, `docs.jsonl`, `tables.jsonl`, `entities.json`), every input ID present once, no key or column removed.

## 2. What we treat as PII

| Type | Examples | Treatment |
|---|---|---|
| **Direct** | full name, short name ("Manuela"), ALL CAPS without accents, filename slug (`manuela_castano`), cédula with/without dots, passport, email (incl. "arroba/punto"), phone (with/without +57), address, birth date, plate, bank account, Slack `<@UID>` | Replace with a same-category pseudonym |
| **Indirect (quasi-identifiers)** | neighborhood + occupation + year that together single out one person | Generalize: occupation → sector, neighborhood → another neighborhood in the same city, year → "hace unos años" — in **both** the table row and the text |
| **Hidden locations** | PDF metadata, MD front matter, HTML comments/meta, XML attributes and comments, attachment filenames, document filenames | Same replacement engine applied to every field, not only body text |

## 3. What we keep on purpose, and why

| Kept | Why |
|---|---|
| Organizations, banks, wallets, regulators, merchants (Bancolombia, Nequi, Superfinanciera, Rappi, DataCrédito) | Not natural persons; removing them costs utility points (false-positive tests) |
| Places (cities, Plaza Botero, Parque de Bolívar), public figures (Shakira) | Not personal data on their own; rule explicitly says keep |
| Product / company names (Kipu Pay, Kipi bot) | Business content |
| Internal IDs (`CL-`, `TX-`, `KP-`, `PQR-`, credit numbers) | Needed so tables still join; they only identify inside Kipu's systems, and the sample's ideal output keeps them |
| Amounts, business-event dates, city, `fecha_vinculacion` | Training value; city alone is not identifying (the sample keeps them) |

## 4. Constraints and assumptions

**Constraints:** `/run` < 2 h; `/anonymize` < 60 s per 50 records; no finished PII products or PII libraries (Presidio, Comprehend…); 600 req/min on the sources API; sources **grow without warning** mid-event and existing people must keep the same pseudonym; only 3 evaluation runs; service must be online from 19:00 through demo day; all calls arrive with a Bearer header.

**Assumptions:**
- A1. `cliente_id` and `KP-id` are true identities; the master tables are the ground truth for who exists.
- A2. A chat user can be linked to the master tables by email and/or full name; when not linkable, they become their own entity.
- A3. Namesake tie-breakers live in another source (case number, amount + date, attachment, master row) — never in nearby city/job text (stated in the brief).
- A4. New data never modifies old records.
- A5. PDFs have an extractable text layer (verified on samples).
- A6. Almost all data is Colombian; non-Colombian identifiers are rare.

## 5. Hard cases and how we handle each

| # | Hard case (seen in data or brief) | Handling | Confidence |
|---|---|---|---|
| H1 | **Namesakes** — two clients named *Camila Arango Velásquez* (public test pub_04) | Identity = `cliente_id`, never the name; two entities, two pseudonyms | High in tables; medium in free text (see L3) |
| H2 | **Agent with the same name as a client** (e.g. *Luis Santander* client vs `luis.santander@kipupay.co`) | Separate employee and client registries; role decided by source (employee table / Slack profile title / kipupay.co domain) | Medium-high |
| H3 | **Surname that is a place** (*Santander*) | Replaced only when part of a matched person name; standalone place names are on the keep-list | Medium-high |
| H4 | **Short forms** ("Hola Manuela", `reporta_a="David Mejía"`) | Each entity stores variants; each variant maps to the matching form of the pseudonym (first name → first name) | High for registry people |
| H5 | **ALL CAPS / no accents** in statements | Matching on normalized text (NFKC, casefold, accents stripped) | High |
| H6 | **Spelled-out emails** | Dedicated regex; replacement keeps the spelled style | High |
| H7 | **Hidden fields** (metadata, front matter, comments, attributes, filenames) | Replacement runs over every string field; filenames handled as slugs | High |
| H8 | **Third parties mentioned in text** (notes, chats, memos) | Registry match first; unknown names via Colombian first-name list → new entity | Medium (see L1) |
| H9 | **Bot / agent repeats client data** in handoffs | Detection runs on every message's text, independent of author | High |
| H10 | **Quasi-identifier combos across sources** (chat says "fotógrafa, Granada, 2025" and CSV row says the same) | Generalize in table **and** text with the same mapping | Medium-high |
| H11 | **Sources grow mid-event** | Deterministic HMAC pseudonyms + persisted map loaded first | High |
| H12 | **Unseen records in `/anonymize`** (known person → same pseudonym; new person → new unique one; namesakes → two) | Same core as `/run`, using the in-memory registry and map | High for known IDs; medium for name-only mentions |
| H13 | **Cold start** — validator calls `/anonymize` before any `/run` | Service works with regex + name list alone and loads any saved map on boot | High |
| H14 | **Judge's global sweep** tolerates confusables, separators, invisible chars | Our own leak sweep uses the same normalization (reimplemented from `validar.mjs`) | High |

## 6. Known gaps — cases we are NOT fully covering, and why

Being explicit so the judges and buyer know the residual risk.

| # | Gap | Why we're not covering it | Risk / mitigation |
|---|---|---|---|
| L1 | **Rare or foreign first names of unknown third parties** (not in registry, not in name list) | A trained NER is not feasible in one day and PII libraries are banned | Possible leak in a few records. Mitigation: leak review of leftover capitalized words; Jev classifier as stretch |
| L2 | **Country-specific formats for non-Colombian IDs** (foreign tax IDs, non-+57 phones) | Each format is a new rule; nearly all data is Colombian | Generic rules (long digit runs, any email) still catch most. People are always pseudonymized regardless of nationality |
| L3 | **Namesakes in free text with no linking identifier** | Disambiguation needs cross-source evidence that is often absent in a single message; full coreference is research-grade work | Default to the person already active in that thread/case; some merges possible |
| L4 | **Nicknames and diminutives** ("Mafe" for María Fernanda, "Dani") | Requires a curated nickname dictionary; only Slack `display_name` gives some for free | Use Slack display names; others may leak |
| L5 | **Indirect identifiers beyond neighborhood + occupation + year** (e.g. rare medical condition + city, unique job title like "CEO") | Formal k-anonymity across all attributes needs more time and would over-generalize | Only the combo the brief defines is handled; job titles of employees are kept for "reports to" utility |
| L6 | **Free-text dates of birth / ages written in words** ("cumplo 40 en marzo") | Hard to detect reliably with rules | Not handled unless the date matches a known birth date |
| L7 | **Text inside images / scanned PDFs** | No OCR in scope; attachments are metadata only | Only filenames are anonymized |
| L8 | **Gender inference for unknown names** | Name list may not cover all names | Fallback gender from Spanish grammar cues or neutral pick; may cost category points |
| L9 | **Scaling beyond this corpus** (millions of messages) | Registry and map are in memory; single process | Fine for ~435k tokens; at scale would need a DB-backed map and sharded workers |
| L10 | **Adversarial obfuscation beyond the judge's list** (e.g. "m c a s t a n o") | Endless variants | Normalization covers the judge's known tricks only |
| L11 | **Perfect similarity to the ideal output** | Pseudonym choice will differ from the organizers' picks; formatting of generalized occupations may differ | Small utility loss accepted |

## 7. Out of scope (deliberate)

| Out of scope | Reason |
|---|---|
| LLM rewriting of text | Loses similarity points; may invent or delete business data |
| LLM pass over every record | 435k tokens: slow, costly, non-deterministic |
| Trained/fine-tuned NER model | Not feasible in a day; PII libraries banned |
| OCR | Not needed for this data |
| Rebuilding PDF/HTML files | Output is text + metadata by contract |
| Auth, multi-tenant, horizontal scaling, polished UI | Not scored; a simple demo page at most |

---

# Pillar 2 — Architecture and trade-offs

Full detail in `ARCHITECTURE.md`. Summary:

**Ingest → Normalize → Person registry → Detect → (optional Jev classify) → Assign pseudonyms → Replace & generalize → Write & check → HTTP API**, with a persisted **pseudonym map** shared by `/run` and `/anonymize`.

## 8. Key decisions

| Decision | Alternative rejected | Why |
|---|---|---|
| **D1. Deterministic code at request time, not a runtime agent** | Agent orchestrating each request | 60 s limit, exact-consistency requirement, a dead service burns a run, cost must be reported. Pi is used to *build* the code |
| **D2. Rules-first detection** | LLM-first detection | Organizers' benchmark: rules ≈ 88; deterministic, zero tokens, fast |
| **D3. Registry from structured tables first** | Resolve identity from chat authors | Tables carry stable IDs (needed for namesakes); author-only resolution misses third parties and handoffs (brief warns) |
| **D4. HMAC(salt, stable key) + persisted map** | Random pseudonyms / plain hash | Survives the mid-event data growth; hashes are reversible for cédulas and lose category |
| **D5. Same-category pseudonyms** (same gender name, valid cédula length/prefix, same email pattern/provider, valid +57 mobile) | `[REDACTED]`, "John Doe", hashes | Utility points for category and structure; redaction benchmark = 56 |
| **D6. In-place span replacement, longest match first** | Model rewriting | Preserves non-personal text and business data |
| **D7. Generalize quasi-identifiers in table and text** | Delete them / fix only chats | Brief: indirect leak is evaluated on the union of sources |
| **D8. One shared core for `/run` and `/anonymize`** | Separate logic | Guarantees identical pseudonyms in batch and live |
| **D9. Jev only for typed decisions on leftovers** (person / org / place / other) | Generative LLM for detection | Jev returns typed choices with probabilities, cheap and fast; "code finds, Jev decides, code replaces". Cached → deterministic. Optional: pipeline works without it |
| **D10. Python + FastAPI, single always-on container with a volume** | Serverless / free sleeping tier | Long background job, must stay up for hours, map must survive restarts |

## 9. Failure handling

| Failure | Response |
|---|---|
| Detection miss | Leak sweep flags records with known originals; manual rule fix before spending a run |
| Jev unavailable / slow | Hard timeout; fall back to rules (mask likely names); pipeline unaffected |
| Sources API rate limit / errors | Throttle < 600 req/min; retries with backoff; `/run` reports `failed` with an error instead of hanging |
| Service restart | Map and last output reloaded from disk on boot |
| Data growth | Old entities keep pseudonyms via map + HMAC; new entities get new unique ones |
| Malformed `/anonymize` input | 422 for out-of-schema records; never crash the process |

---

# Pillar 3 — Plan to measure

## 10. Test cases (defined before building)

| # | Case | Input | Expected |
|---|---|---|---|
| T1 | Schema | full output | `node validar.mjs` → OK |
| T2 | 1:1 records | output vs `record_index.json` | 0 missing, 0 extra (8,297 ids) |
| T3 | Cédula with dots | chat `968db369…` contains `32.355.343` | gone from every record, not as unsalted hash (pub_02) |
| T4 | Name in attachment | chat `e570086e…` attachment `camila_restrepo…` | slug replaced (pub_01) |
| T5 | False positive | chat `b6d50ca1…` contains `Nequi` | still present unchanged (pub_03) |
| T6 | Namesakes | `clientes:CL-12538` and the other *Camila Arango Velásquez* | two different pseudonyms (pub_04) |
| T7 | Consistency | Ana Lucía Marulanda: email, cédula ±dots, phone, full/short name | one pseudonym in all her records (pub_05) |
| T8 | All 13 sample tests | `sample/` | pass against our output on the sample |
| T9 | Memo author / front matter | sample doc author email | replaced in metadata **and** body |
| T10 | Quasi-identifier | sample "fotógrafa, Granada, 2025" | generalized in chat **and** CSV row |
| T11 | Reports-to | `reporta_a="David Mejía"` | equals short form of KP-001's pseudonym |
| T12 | Spelled email | "lmejia arroba hotmail punto com" | replaced, style kept |
| T13 | PDF metadata | `doc_09` Author "Milton Rojas", title with client name | metadata keys kept, values pseudonymized |
| T14 | Live: known person | new message naming an existing client | same pseudonym as in `output/` |
| T15 | Live: new person | new message with unseen name | new unique pseudonym, same gender |
| T16 | Live: namesakes | two different people same name with different IDs | two pseudonyms |
| T17 | Stability | run `/run` twice | identical `entities.json` pseudonyms |
| T18 | Growth simulation | run on subset, then on full set | all subset entities keep pseudonyms |
| T19 | Cold start | `/anonymize` with no prior run | valid response < 60 s |

## 11. Metrics and targets

| Metric | Target |
|---|---|
| Leak sweep: original forms found in output | **0** |
| Public + sample tests passing locally | **18 / 18** |
| Record coverage (1:1) | **100 %** |
| Pseudonym uniqueness (distinct people → distinct names) | **100 %** |
| Pseudonym stability across runs / growth | **100 %** |
| Similarity of non-personal text to sample ideal output | ≥ 0.95 (character-level ratio) |
| `/run` duration | < 10 min (limit 2 h) |
| `/anonymize` latency, 50 records | < 5 s (limit 60 s) |
| Token cost per run | 0 rules-only; Jev calls logged if enabled |

## 12. Evaluation process

1. Validate the sample ideal output to confirm tooling, then our sample output → compare with `expected_output`.
2. Local scorer for the 5 public + 13 sample tests; leak sweep on the full real output.
3. Run T17–T19 against the deployed URL (`/health`, `/run`, poll, `/output`, `/anonymize`).
4. **Spend evaluation run #1 early**, before the sources grow (protects the 30 % share).
5. Fix from the run detail; spend runs #2/#3 after the data grows (70 % share).

---

## 13. Cost estimate

| Item | Estimate |
|---|---|
| Rules pipeline | 0 tokens; ~2–5 min for ~8.3k records (mostly download time) |
| Jev (if enabled) | only leftover spans — expected a few hundred calls per run; logged in `/run` cost |
| `/anonymize` | 0 tokens on hot path |

## 14. Open questions / next steps

- Confirm Jev access and latency; keep it disabled by default until measured.
- Decide hosting (Railway/Render/Fly with persistent volume).
- Build order: ingest+normalize → registry → detect/replace on sample until 13/13 → full data → API → deploy → run #1.
