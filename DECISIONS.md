# DECISIONS

The brief asks three questions. Draft answers below, to be updated with real numbers after each run.

## 1. What did we consider PII?

**Direct identifiers** (replaced with a same-category pseudonym):
- Person names in every form: full name, first name, first name + first surname, ALL CAPS without accents (bank statements), filename slugs (`manuela_castano`), Slack display names, `<@UID>` mentions.
- Cédula (with and without dots), passport, email (including spelled-out "arroba / punto"), phone (with or without +57, spaces), postal address, date of birth, vehicle plate, bank account numbers tied to a person.

**Indirect identifiers (quasi-identifiers):** neighborhood + occupation + year that together single out one person. These are **generalized**, not deleted: occupation → sector, neighborhood → another neighborhood of the same city, year → "hace unos años". This applies to the `clientes` row **and** to every text mentioning that person.

**Hidden locations**, which get the same treatment: PDF metadata (author, title, subject, keywords), Markdown front matter, HTML comments and `<meta>`, XML attributes and comments, attachment filenames and document filenames.

## 2. What did we keep on purpose, and why?

| Kept | Why |
|---|---|
| Organizations, banks, wallets, merchants, regulators (Bancolombia, Nequi, Superfinanciera, DataCrédito, Rappi…) | Not natural persons; needed for training value; removing them is a scored false positive |
| Places and public figures (cities, Plaza Botero, Shakira) | Not personal data on their own |
| Kipu Pay, Kipi (bot) | Company and product names |
| Internal IDs (`CL-`, `TX-`, `KP-`, `PQR-`, credit numbers) | Keep joins between tables; they only identify inside Kipu's systems; the sample's ideal output keeps them |
| Amounts, business-event dates, city, `fecha_vinculacion` | Business content; city alone is not identifying |
| Employee job titles | Needed for "reports to" structure; only the name and contact data are replaced |

**Surnames that are also places** (e.g. Santander) are replaced when they are part of a person's name, and kept when they name the place.

## 3. How much did it cost to run?

| Item | Value |
|---|---|
| LLM tokens per `/run` (core pipeline) | **0**: detection, pseudonyms and replacement are deterministic code |
| Wall time per `/run` (laptop, incl. download) | **34 s** before the incident, **53–80 s** after (2 self-check passes) |
| Requests to sources API | **183** before, **213** after the incident (limit 600/min) |
| `/anonymize` latency (50 records) | **< 1 s** |
| Verification (after the run, optional) | Jev at about $0.042 per million input tokens, roughly a few cents per full run and cached afterwards; reasoning only on the ~45 flagged cases |

## Key design decisions

| Decision | Alternative rejected | Why |
|---|---|---|
| Deterministic code at request time | Runtime agent | 60 s limit, exact consistency, a dead service burns a run. Pi was used to build the code |
| Rules-first detection | LLM-first | Organizers' benchmark: rules ≈ 88; 0 tokens; deterministic |
| Person registry from structured tables first | Identity from chat authors | Stable IDs resolve namesakes; author-only resolution misses third parties |
| HMAC(salt, stable key) + persisted map | Random pseudonyms / plain hashes | Stable across the mid-event data growth; hashes are reversible and lose category |
| In-place span replacement | LLM rewriting | Keeps non-personal text and business data intact |
| One shared core for `/run` and `/anonymize` | Separate logic | Same pseudonym in batch and live |
| Jev only for typed decisions on leftovers (optional) | Generative LLM for detection | Cheap and fast; cached answers stay deterministic; pipeline works without it |

## Keeping the data trainable

- **Barrios** in tables are replaced by another barrio of the **same zone of the same city** (e.g. Granada → Santa Mónica, both north Cali), so the location distribution stays realistic. In free text a barrio is generalized to the city.
- **Occupations** map to a closed list of 17 sectors, in this order: exact table (all 35 real occupations), learned decisions, keyword rules, then **Jev** (TypeSafe decision model via OpenRouter) choosing among the existing sectors. Confident Jev decisions are stored and proposed as new keywords. A human promotes them into the versioned rules, so the next run needs no model call.
- Jev answers are cached, so decisions are deterministic across runs and after the data grows.

## Verification layer (after the main run)

| Decision | Alternative rejected | Why |
|---|---|---|
| Verify **masked** text (our replacements as placeholders) | Verify the raw output, or delete our replacements | Our fakes look like real PII, and deleting them breaks the context that reveals partial misses (`[NOMBRE_1] Patiño`) |
| Code check first; Jev only adds suspicion | Trust Jev alone | Jev can miss things or be unavailable; a code hit always goes to a human |
| Reasoning model proposes, a human decides, the next run applies | Auto-apply model fixes | Keeps the shipped output deterministic and auditable; every decision is logged in `knowledge.json` |
| Runs after `/run` is `done` | Inside the run | The judge never waits, and a model outage can't break a run |
| Dashboard protected by `REVIEW_KEY` | Open page | It shows real snippets, and the service is public |

## Known limitations

Rare or foreign names of unknown third parties may leak (no trained NER, and PII libraries are banned). Namesakes in free text with no linking identifier may merge. Nicknames, pronoun references, ages written in words and text inside images are not handled. Quasi-identifiers beyond the defined combination are not generalized. The single-process, in-memory design is sized for this corpus, not millions of records.
