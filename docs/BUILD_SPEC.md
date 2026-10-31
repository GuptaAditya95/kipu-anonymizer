# BUILD SPEC: Kipu Pay anonymizer

The complete guide for building this repo with the **Pi coding agent**. Pi loads `AGENTS.md` automatically. This file is the detailed reference. Work through §6 in order: each task has a ready-to-paste prompt, the recommended model, and acceptance tests.

Contents: 1 Goal and scoring · 2 Data facts · 3 Output contract · 4 Expected behaviour (from the ideal sample) · 5 Module specs · 6 Task plan with prompts · 7 Model routing · 8 Testing strategy · 9 Incident playbook · 10 Definition of done

---

## 1. Goal and scoring

Produce `output/` = `chats.jsonl`, `docs.jsonl`, `tables.jsonl`, `entities.json`, and serve it over HTTP.

| Dimension | Points | What fails it |
|---|---|---|
| Leaks | 45 | any real form (name, cédula, email, phone, address, birth date, slug…) still present anywhere, including metadata/filenames/hashes; quasi-identifier combo left together (checked on table + text) |
| Utility | 35 | non-personal text changed; orgs/places removed; pseudonym of wrong category (gender, format); broken joins; a person's record count changed; who-talked-to-whom changed |
| Consistency | 20 | same person ≠ same pseudonym; namesakes merged; "reports to" broken; `entities.json` under-declares originals |

Benchmarks from the organizers: solid rules ≈ 88, hash everything 58, `[REDACTED]` 56, one pseudonym per gender 70, change one letter 41, detect 70% of people 76.

**Execution rubric (judged separately):** hidden-test result · incident response at 15:00 · repo quality (runs from the README, code explains itself) · resolution strategy (code does most of it; the model only decides where needed; lessons are stored in a reusable format: `knowledge/`).

---

## 2. Data facts (verified against the real API on 2026-10-31)

Sources API: `https://reto-pii-fuentes-production.up.railway.app/v1`, header `Authorization: Bearer <token>`, 600 req/min.

| Source | Facts |
|---|---|
| chats | 6 channels (`soporte-cobros`, `soporte-tarjetas`, `soporte-onboarding`, `fraude-interno`, `escalamientos`, `general`), 142 channel-days, 2,028 messages. Message: `user` (uid), `text`, `ts`, `thread_ts`, `client_msg_id`, `files[{name,title,...}]`, `subtype` (`bot_message`), `username`. Mentions `<@UID>`. Bot uid `UKIPIBOT01` named **Kipi** (keep). |
| users.json | 187 users. Employees: `real_name`, `profile.email` (`@kipupay.co`), `profile.title`, `profile.phone`, `profile.display_name`. Clients: partial profiles, **no customer_id, no phone**, only some have email. |
| docs | 26: 11 PDF, 6 HTML, 9 MD (2 in English: `vendor_agreement_veloz_2026.pdf`, `investor_memo_q3_2026.html`, `security_report_Q3_2026.pdf`). PDF metadata has `Author`/`Title` with real names. Filenames contain names (`carta_mora_luis_santander.pdf`, `paz_y_salvo_martin_jimenez.pdf`, `carta_cobro_juridico_esteban_dario_mejia.pdf`, `evaluacion_desempeno_KP-009_2026.pdf`). HTML has comments like `<!-- draft reviewed by <name> (<email>) -->`. MD has front matter (`author: <email>`). |
| clientes.csv | **Latin-1, `;`**, 149 rows. Columns: `cliente_id;nombre_completo;tipo_doc;num_doc;correo;celular;ciudad;barrio;direccion;fecha_nacimiento;fecha_vinculacion;ocupacion;notas`. Notes name other clients ("Familiar de X (CL-…)"). **Namesakes:** two rows named "Camila Arango Velásquez" (one is CL-12538). |
| transacciones.csv | UTF-8, `,`, 6,030 rows: `tx_id,fecha,cliente_id,tipo,monto,descripcion,canal,estado`. Descriptions are merchants/places (Rappi, Avianca BOG-MDE, "Carulla Castilla") → keep. |
| pqrs.xml | 28 `<pqr>`: attributes `radicado, cliente_id, cedula, ciudad, fecha, agente (username like fernanda.vargas), tipo, estado`; children `descripcion` (CDATA, free text with occupation/city), `respuesta`. Top XML comment has an employee email. |
| empleados.xml | 36 `<empleado id="KP-001" cargo ciudad ingreso reporta_a="David Mejía">` with children `nombre, correo, celular, documento(tipo=CC), fecha_nacimiento`. `reporta_a` is the **short name** of another employee. |
| record_index | `{chats:[2028 ids], docs:[26], tables:[6243]}`, 8,297 ids. Table ids: `clientes:CL-…`, `transacciones:TX-…`, `pqrs:PQR-…`, `empleados:KP-…`. |
| sample.zip | `input/`, `expected_output/` (ideal), `tests_sample.json` (13 tests), `record_index.json`. **Fictional people → safe for test fixtures.** |
| public tests | pub_01 name in attachment filename (`camila_restrepo`), pub_02 cédula with dots `32.355.343`, pub_03 keep `Nequi`, pub_04 namesakes CL-12538, pub_05 consistency across `amarulanda@gmail.com, 32355343, 32.355.343, 3008472387, Ana Lucía Marulanda Hoyos, Ana Lucía Marulanda`. |

Tricky things seen in the data: spelled-out emails (`lmejia arroba hotmail punto com`), passports `AR752831`, `+57 322 973 0911` in an English doc, a client "Luis Santander" (surname = department) and an employee `luis.santander@kipupay.co`.

---

## 3. Output contract

```
chats.jsonl   {"id","thread_id","author","ts","text","attachments":[{"filename"}]}
docs.jsonl    {"id","format","filename","title","body","metadata":{...same keys as input}}
tables.jsonl  {"id","table","columns":{...all original columns, values as strings}}
entities.json {"entities":[
  {"entity_id":"p_057","type":"person","originals":[...every real form...],
   "pseudonyms":{"name":"…","email":"…","phone":"…","cedula":"…","address":"…","birth_iso":"…"},
   "records":[...ids...]},
  {"entity_id":"kept_007","type":"organization_or_place","originals":["Bancolombia"],
   "pseudonyms":{},"kept":true,"reason":"organización, lugar, marca o figura pública; no es persona natural","records":[]}
]}
```
- `thread_id` = `<canal>:<ts of first message in thread>` (`thread_ts` or own `ts`).
- `author` = pseudonym name (never the uid). Bot stays "Kipi".
- Filenames stay as fields, but their text is anonymized.
- The validator also requires: every pseudonym value you declare appears in at least one of the entity's records, and no declared original (≥3 chars, not a single capitalized word) remains anywhere.

API contract: `api/participante.openapi.yaml` (already implemented in `app/main.py`).

---

## 4. Expected behaviour (taken from `sample/expected_output`)

| Input | Ideal output | Rule |
|---|---|---|
| "Hola, me llamo **Manuela Castaño**." (author uid) | author **Sofía Elena Zambrano**, same in text | full name → full pseudonym (first names + 1 surname) |
| "Hola **Manuela**, soy **Yesenia**" | "Hola **Sofía Elena**, soy **Marcela**" | first-name form → pseudonym first name(s) |
| table `nombre_completo` "Manuela Castaño Ruiz" | "Sofía Elena Zambrano Naranjo" | 2-surname form → pseudonym + 2nd surname |
| `1.107.360.039` / `1107360039` | `1.107.252.808` / `1107252808` | same length, same first 4 digits, dots kept if present |
| `mcastano@gmail.com` | `szambrano@gmail.com` | same provider, same pattern (initial + surname) |
| `kelly.castano@kipupay.co` | `doris.lozano@kipupay.co` | employees: `nombre.apellido@kipupay.co` |
| `3148729552` | `3186423850` | valid CO mobile `3[0-2]\d…`, 10 digits |
| `Diagonal 3A # 78-6 apto 1501` | `Diagonal 121B # 96-28` | plausible CO address |
| `1997-07-23` | `1998-11-11` | plausible date, ±few years |
| barrio `Granada` (Cali) | `Santa Mónica` | another barrio of the same city |
| ocupación `fotógrafa` / `periodista` | `profesional de las industrias creativas` / `profesional de medios` | occupation → sector |
| "soy fotógrafa… en Cali… entré en **2025**" | "soy profesional de las industrias creativas… en Cali… entré en **hace unos años**" | generalize the combo in text too |
| `extracto_manuela_castano_julio.pdf` | `extracto_sofia_elena_zambrano_julio.pdf` | slug: lowercase, no accents, `_` |
| front matter `author: kelly.castano@kipupay.co`, "Preparado por Kelly Castaño" | `doris.lozano@kipupay.co`, "Preparado por Doris Lozano" | metadata + body + front matter together |
| Bancolombia, Superfinanciera, PSE, Kipi | unchanged | keep-list |
| `cliente_id`, `fecha_vinculacion`, `ciudad`, amounts | unchanged | structure |

`originals` in the sample include: name variants, cédula both forms, phone, email, address, birth date, slug (`manuela_castano`).

---

## 5. Module specs

### 5.1 `pipeline/text.py` (new)
- `norm(s)`: port of `norm()` in `scripts/validar.mjs` (NFKC, confusables map, drop `\p{Cf}`, unify spaces, strip accents, casefold).
- `digits_compact(s)`: drop separators between digits (port `RX_SEPARADOR_DIGITOS`).
- `contains(haystack, needle)`: port of `contiene()`, used by the leak sweep and tests.
- `slug(name)`: "Manuela Castaño" → `manuela_castano`.
- `name_variants(full)`: full, first name(s), first + first surname, 2 surnames, CAPS, accentless, slug.
- `find_all(text, forms)`: accent- and case-insensitive spans with word boundaries, longest first, returning **original-text offsets** (so the replacement keeps surrounding text intact).

### 5.2 `pipeline/normalize.py` (tolerant)
- Tables discovered from `raw/tables/tables.json` (`name, format, charset, separator`); key column inferred from `record_index` (prefix before `:`, value matches a column).
- XML: rows = repeated child elements; columns = attributes + children (+ `child_attr`). Keep comments in a side channel for detection.
- Unknown doc format → plain text. **Any id in record_index not built → pass-through placeholder + logged** (never missing).

### 5.3 `pipeline/snapshot.py` (new)
- `snapshot(raw)` → counts per source, channels, docs, tables, columns, formats.
- `diff(prev, cur)` → `changes` dict; save both in `data/state/`. Exposed in `GET /run/{id}`.
- `stability(prev_entities, cur_entities)` → % of previous entities whose pseudonyms are unchanged (target 100%).

### 5.4 `pipeline/registry.py`
- `Person(key, kind, full_name, gender, forms, ids, records)`.
- Build: clientes (`cl:<cliente_id>`) → empleados (`kp:<id>`) → users: employees linked by `@kipupay.co` email/name to `kp:`; clients by email or exact full name (unique) to `cl:`; otherwise `uid:<uid>`. Bot excluded.
- Forms: all `name_variants`, cédula (raw + dotted), phone (raw, +57, spaced 3-3-4), email, address, birth date, Slack `name`/`display_name`, pqrs `agente` username for employees.
- Indexes: `by_form[norm(form)] -> [keys]`, `by_id[digits or email] -> key`.
- Gender: first-name lookup in `resources/names_female.json`/`names_male.json`; fallback by trailing `a` + Spanish cues.

### 5.5 `pipeline/pseudonyms.py`
- Stable key → seed `HMAC_SHA256(SALT, key#counter)`.
- `name`: same gender, 1–2 first names (match the original's count), 1 surname (+2nd for table full names). Unique across all entities.
- `email`: detect pattern (`{i}{surname}`, `{first}.{surname}`, `{first}{surname}`, other) and provider; rebuild with pseudonym parts.
- `cedula`: same length, same first 4 digits, rest from seed, must differ from the original in ≥4 positions.
- `phone`: `3` + one of `0,1,2,5` + 8 digits; keep formatting (spaces, `+57`).
- `address`, `birth_iso`, `plate` (`ABC123`), `passport` (`AR` + 6 digits).
- `get_or_create(key)`: map first; save atomically.

### 5.6 `pipeline/detect.py` + `pipeline/resolve.py`
Order per string field:
1. Regex (see `PATTERNS`): email, spelled email, cédula dotted, long digit runs (classify cédula vs phone by length/prefix/context words: "cédula", "CC", "celular", "cel", "WhatsApp"), phone, plate, passport, address, ISO date (only if equal to a known birth date), `<@UID>` (already resolved in normalize).
2. Registry forms (longest first).
3. Unknown capitalized sequences whose first token is in the first-name list (and not in the keep-list) → new person `txt:<norm(name)>`.
4. Keep-list hits are skipped, **unless** inside a matched person span ("Luis Santander").
Resolution: a strong id in the same record (cédula/email/phone/cliente_id/radicado→cliente_id) wins; else a unique name match; else the person active in the same thread/case; else a new entity. **Same name + different strong id → different entity.**

### 5.7 `pipeline/replace.py` + `pipeline/generalize.py`
- Apply spans right-to-left, so offsets stay valid.
- Keep the form: first-name → pseudonym first names; full → full; CAPS → CAPS; accentless → accentless; slug → slug; dotted cédula → dotted; spelled email → spelled.
- Fields: chats `author, text, attachments[].filename`; docs `filename, title, body, metadata values`; tables every column except ids/business columns (`cliente_id, tx_id, radicado, id, fecha, fecha_vinculacion, monto, tipo, canal, estado, ciudad, cargo`).
- `reporta_a` → short form (first + first surname) of the referenced employee's pseudonym.
- Generalize (**already implemented in `pipeline/generalize.py`, with tests**; wire it in):
  - Table `barrio` → `Barrios().nearby(barrio, ciudad, seed)`: another barrio of the **same zone** of the same city (`resources/barrios_by_city.json`), so the geography stays trainable (Granada → Santa Mónica, both north Cali).
  - Free-text barrio → `barrio_in_text()` → the **city** ("en Granada" → "en Cali"), as in the sample.
  - `ocupacion` (table and text) → `Occupations().to_sector()`: exact map → learned map → keyword rules → **Jev choice among existing sectors** → fallback "trabajador independiente". Confident Jev answers are saved to `DATA_DIR/state/learned_occupations.json` with a proposed keyword; `scripts/promote_learned.py --accept` adds them to the versioned JSON after human review.
  - Years in identifying phrases → `generalize_years()` → "hace unos años".

### 5.8 `pipeline/core.py`
`anonymize_records(source, records, state)` → `(records, entities)`. It's used by `/run` for all records and by `/anonymize` per batch. It must work with no registry (cold start: regex + name list).

### 5.9 `pipeline/output.py`
- `entities.json`: persons with `originals` = **every form actually found in the input** (not just registry forms), `pseudonyms` = only values actually written, `records` = ids where it appears. Kept orgs/places from the keep-list hits, with `kept: true`.
- `leak_sweep`: use `text.contains` (judge-equivalent) for all originals across all output → list of `(entity, original, record ids)`.

### 5.9b Jev through OpenRouter (implemented in `pipeline/jev.py`)
- Endpoint `POST https://openrouter.ai/api/v1/systemone`, auth `Bearer $OPENROUTER_API_KEY`, model `jev-1.13` (or `jev-latest`). No TypeSafe account needed. Output tokens are free; you pay per input token.
- Question types used: `choice` (with `criteria` = option → description) and `noul` (yes probability).
- Uses: (1) unknown occupation → choose among `occupation_sectors.json`; (2) leftover capitalized span → person/organization/place/other (`classify_span`); (3) optional namesake tie-break (choose between candidate profiles).
- Guarantees: every answer cached in `DATA_DIR/state/jev_cache.json` (same question is never asked twice → deterministic); timeout 4 s; confidence threshold 0.6; any failure → rules fallback; disabled when no key. Calls, input tokens and cost are counted for `/run` cost and DECISIONS.md.
- First step: `python scripts/jev_probe.py` to confirm the key and the exact `choice` answer shape (adjust `_parse_choice` if needed).

### 5.10 `knowledge/` (new) and the review loop (after run #1)
- `knowledge/interface_map.json` (written by ingest: endpoints, fields, encodings, counts), `knowledge/rules/` (move `resources/*.json` here or symlink), `knowledge/learned_from_runs.md` (run → failed tests → hypothesis → rule → result), `knowledge/jev_cache.json`.
- `scripts/review.py`: reads the leak sweep, leftover capitalized words, low-confidence Jev answers and `changes`; sends **only span + sentence** (max 100) to an LLM via OpenRouter; writes `reports/review_<job>.md` with grouped issues, hypothesis, proposed rule (as a JSON diff to `knowledge/rules`) and a test case. **Humans approve; never auto-apply.**

---

## 6. Task plan (paste each prompt into Pi)

Format: **Model tier** (see §7) · prompt · done when.

**T0. Setup** · Fast
> Read AGENTS.md and docs/BUILD_SPEC.md. Create .env from .env.example (I'll fill in the token). Run `python -m pipeline.run` to download data, then the validator. Report record counts. Don't change code.
Done when: validator OK, 8,297 records.

**T1. Tolerant normalize** · Strong
> Implement BUILD_SPEC §5.2. Discover tables from tables.json (charset, separator), infer key columns from record_index, handle unknown doc formats as text, and add a pass-through placeholder for any record_index id that wasn't built (log it). Add tests in tests/test_normalize.py using the sample dataset. Keep current outputs identical for the 4 known tables.
Done when: tests pass, validator OK, same 8,297 records.

**T2. Snapshot, diff, stability** · Fast
> Implement BUILD_SPEC §5.3 in pipeline/snapshot.py; call it from run.py; include `changes` and `stability` in the run summary and in GET /run/{id}. Test with two fake snapshots.

**T3. Text utilities** · Strong
> Implement pipeline/text.py per §5.1, porting norm/contiene/digitosCompactos from scripts/validar.mjs exactly. Write tests covering accents, CAPS, confusables (Cyrillic а), digit separators (1.107.360.039 vs 1107360039, +57), slugs and name_variants("Manuela Castaño Ruiz").

**T4. Registry** · Strongest
> Implement §5.4. Use the sample input as the test fixture: Manuela Castaño Ruiz (CL-88627) must have forms including "Manuela", "Manuela Castaño", "manuela_castano", "1.107.360.039", "1107360039", "mcastano@gmail.com", "3148729552". Employees from empleados.xml; link chat users. Two clients with the same name must be two Persons. Print stats on the real data (people by kind, unlinked chat users).

**T5. Pseudonym generators** · Strong
> Implement §5.5 with the format rules in §4. Grow resources/names_female.json, names_male.json and surnames.json to ≥150 / ≥150 / ≥200 common Colombian names (no accents lost). Tests: determinism (same key → same output across two PseudonymMap instances with the same SALT), uniqueness over 1,000 keys, the cédula keeps its length and first 4 digits, email keeps provider and pattern, gender kept.

**T6. Detect and resolve** · Strongest
> Implement §5.6 (detect.py + new resolve.py). Tests from the sample: every span in the 13 sample tests is detected; Bancolombia/Superfinanciera/Nequi are not; "Luis Santander" is detected as a person while "Santander" alone is kept; the spelled email is detected; namesakes resolve to different keys when strong ids differ.

**T7. Replace and generalize** · Strong
> Implement §5.7 and wire everything in core.py (§5.8). Run on the sample and diff our chats/docs/tables against sample/expected_output: non-personal text must be identical (report a similarity ratio per record; target ≥ 0.95 average). reporta_a must equal the referenced employee's pseudonym short form.

**T8. Entities and leak sweep** · Strong
> Implement §5.9. The leak sweep must use text.contains. Make the validator pass on the sample and on the real data. Print the top 20 leaks with record ids.

**T9. Sample gate** · Strongest (debug)
> Extend scripts/score_local.py to implement all test families (directa, indirecta, oculta, falso_positivo, homonimos, consistencia using lookup_forms). Make 13/13 sample tests pass. Fix root causes, not the tests.

**T10. Real data gate** · Strongest (debug)
> Run on the real data. Targets: validator OK, 5/5 public tests, leak sweep 0 (or each remaining one explained in knowledge/learned_from_runs.md). Summarize what you changed.

**T11. Live endpoint** · Fast
> Add tests/test_anonymize.py: (a) a new message naming a known sample client returns the same pseudonym as output/; (b) an unseen name returns a new unique pseudonym; (c) two people with the same name but different cédulas return two pseudonyms; (d) cold start works; (e) 50 records < 5 s.

**T12. Deploy** · Fast
> Check the Dockerfile works locally (`docker build` if available) and that run.sh starts the server. Write the exact Railway steps in README (volume at /data, SALT, generate domain). Don't change the pipeline.

**T7b. Jev check** · Fast
> Run `python scripts/jev_probe.py` with my OPENROUTER_API_KEY. If the choice answer shape differs from `_parse_choice`, fix the parser and its test. Then wire `jev.classify_span` into detect for leftover capitalized spans (person → new entity, organization/place → keep, low confidence → mask if name-like). Use a JevCache at DATA_DIR/state/jev_cache.json and add its calls/tokens/cost to the run summary.

**T13–T16.** After 15:00: incident (§9), review loop (§5.10), promote learned occupations (`scripts/promote_learned.py`), DECISIONS.md costs.

---

## 7. Model routing (which model for which task)

Pi switches models mid-session with `/model` (or Ctrl+L) and cycles favourites with Ctrl+P. With `OPENROUTER_API_KEY` set, `/model` lists every OpenRouter model. **Check the exact names in `/model`.** The tiers below are what matters; names change quickly.

| Tier | Use for | Suggested models (pick what you have) | Thinking level |
|---|---|---|---|
| **Strongest** | design-heavy, cross-module logic, debugging failing tests: T4 registry, T6 detect/resolve, T9/T10 gates | Claude Opus 5.5, or another top reasoning model | high |
| **Strong** | well-specified modules with tests: T1, T3, T5, T7, T8 | Claude Sonnet 5.5 (best price/quality for coding), or similar | medium |
| **Fast / cheap** | mechanical work: T0, T2, T11, T12, resource lists, README/docs, small refactors | Claude Haiku 5.5, DeepSeek Flash-class, or similar | low/off |
| **Auto** | when unsure | an OpenRouter auto-router (e.g. Jev Router) picks model and effort per request | — |
| **Reviewer** | second opinion before an evaluation run | a **different** model from the one that wrote the code: "review pipeline/ for rule violations in AGENTS.md and for leaks" | high |
| **Runtime: decisions** | inside the service | **Jev via OpenRouter** (`jev-1.13`): only for unknown occupations and leftover spans, cached, 4 s timeout (§5.9b) | — |
| **Runtime: review report** | offline after each run | a strong chat model via OpenRouter writes the human report (§5.10) | medium |

Cost and context tips:
- Start each task in a fresh context (or `/compact` between tasks). AGENTS.md is reloaded and keeps the rules.
- Point Pi at **files and line ranges**, not the whole repo. Never ask it to read `data/raw/` in full (435k tokens); use `head`, `jq`, `grep`.
- Have tests written before code (cheap model), then implement (strong model). A failing test is cheaper to debug than prose.
- Watch the cost in Pi's footer. Note the per-task cost for DECISIONS.md.

---

## 8. Testing strategy

| Layer | Tool | Gate |
|---|---|---|
| Unit | `pytest tests/` | every module has tests, using the sample's fictional people as fixtures |
| Contract | `tests/test_smoke.py`, `tests/test_anonymize.py` | API shape, 422s, latency |
| Golden | `scripts/score_local.py` on `data/sample` vs `expected_output` | 13/13 + similarity ≥ 0.95 |
| Real data | validator + public tests + leak sweep | OK + 5/5 + 0 leaks |
| Stability | run twice; run on a subset then on the full set | 100% unchanged pseudonyms |
| Deployed | curl `/health`, `/run`, poll, `/output`, `/anonymize` on the public URL | all green before pressing VALIDAR ENTRYPOINT |

One command for everything (add as `scripts/check_all.sh` in T10): pytest → pipeline offline → validator → score_local → leak sweep summary.

---

## 9. Incident playbook (15:00)

Expected: the sources grow (new dates, channels, docs, rows, bigger record_index). Nothing old changes. Possibly something else (schema/encoding/format/rate-limit changes).
1. Don't push for the first minutes. Inspect the API by hand: `record_index` counts, `/chats/channels`, `/docs`, `/tables`.
2. Trigger `/run` on the deployed service. Read `changes`, `stability` (must be 100%), the leak sweep and the validator.
3. Fix locally → `scripts/check_all.sh` → push (never during an evaluation run) → run #2.
4. Write `INCIDENT.md`: what changed, when it was detected, what the system handled automatically, what was fixed by hand, scores before/after.

## 10. Definition of done
- All TODO.md items up to T12 ticked before 15:00, run #1 spent.
- `./run.sh` starts the service from a fresh clone following the README.
- Validator OK, 5/5 public, 13/13 sample, leak sweep 0, stability 100%.
- DECISIONS.md and INCIDENT.md filled in. Demo rehearsed.
