# INCIDENT: Kipu Pay acquires Págalo (15:00, 2026-10-31)

## What changed
The sources API started serving Págalo's migrated data. Nothing old changed or disappeared.

| | Before (run #1) | After | Change |
|---|---|---|---|
| Chat messages | 2,028 | 2,999 | +971 |
| Channels | 6 | 8 | + `migracion-pagalo`, `soporte-pagalo` |
| Documents | 26 | 34 | + doc_27 … doc_34 (4 PDF, 2 HTML, 2 MD) |
| Table rows | 6,243 | 7,823 | +1,580 (clientes 149→203, transacciones 6,030→7,540, pqrs 28→38, empleados 36→42) |
| New tables / columns | — | none | — |
| People in entities.json | 201 | ~270 | ~+70 new people |

## How we detected it
- Every `/run` saves a snapshot of the sources (counts, channels, docs, tables, columns) and diffs it with the previous one. The diff appears as `changes` in `GET /run/{id}` and in `data/state/last_run.json`.
- We also checked `GET /tables` by hand: the row counts went up at about 15:50 for our token.

## What the system handled automatically
- **Full rebuild with old + new data.** Channels, docs and tables are discovered from the API, and any id in `record_index` that can't be built is passed through, so the output is always 1:1 (2,999 / 34 / 7,823).
- **Old people keep their pseudonyms.** Pseudonyms come from HMAC(SALT, stable key), with the map persisted in `data/state/`. Entity ids are hashes of the stable key, so they don't depend on processing order.
- **New people get new, unique pseudonyms.** Uniqueness is checked against every name already used and every real name form.

## What the incident data exposed, and how we fixed it (before spending run #2)
We replayed run #1 locally with the exact code that produced it, then ran the new code on the Págalo data on top of that state, and compared `entities.json` people by people.

| Finding | Risk | Fix |
|---|---|---|
| An old fake name ("Rafael López") is now the **real name of a new Págalo person** | The validator flags it as a leak, and an invalid output burns the run | Old pseudonyms are re-checked against newly arrived real names and regenerated only on collision (1 person) |
| An old fake birth date equals a **new real birth date** | Leak (validator) | Stored fake values are re-checked against real values and regenerated only on collision (1 value) |
| The same fake phone was declared in a different format (`3265651050` vs `+57 326 565 1050`) | Looks like a pseudonym change to the stability check | `entities.json` reuses the previous run's declaration when it's still written |
| **New namesakes**: a new customer with an old customer's name made a chat user ambiguous | Old person's messages move to a new entity (consistency break) | Linking replays the previous run's decisions (thread memory + saved uid links); ambiguous name mentions prefer people who already existed |

## Result (local replay)
- Validator **OK**, leak sweep **0**, public tests **5/5**, sample **13/13**.
- **Stability 99%** (199 of 201 previous people with identical pseudonyms). The 2 changes are deliberate: each avoided a validator leak.
- 5 of 201 people have any difference at all (2 deliberate fixes, 3 that lost one or two chat messages in a three-way "Alejandra Rojas" namesake case). Known, documented, small.

## Run #2 (real evaluation) and what we learned from it
- Run #2 on the laptop: `changes` detected all the Págalo data, 2,999 / 34 / 7,823 records, **stability 99.5%**, but **1 leak**: a generated email for a new Págalo customer (`arojas@…`) reused the Slack username of an old customer, which the validator counts as a leak. Our local replay used a different SALT, so it hadn't shown up.
- Lesson: collisions between generated and real values depend on the SALT, so testing one SALT isn't enough.
- Fixes:
  1. **Self-check before finishing every `/run`:** a validator-equivalent leak sweep. Any real value found in the output is forbidden and the run is reprocessed (up to 3 passes). Only the offending values change, so stability stays intact.
  2. Stress test of the run #1 → run #2 → run #3 path with several SALTs: validator OK and 0 leaks in all; strict stability vs run #1 98.5–100%. The SALT that reproduced the laptop's run #2 leak was fixed on the 2nd self-check pass.

## What we'd do with more time
- Store uid links and declared pseudonyms explicitly from run #1 (they're stored from now on).
- A human review queue for ambiguous namesakes (iteration 2 dashboard).
