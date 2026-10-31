# Build plan

The **incident is at 15:00**: sources are expected to grow, or something else changes. Scoring: the **last run before the change = 30 %**, the **last run after = 70 %**. If no run happens before the change, the 30 % is zero.

## Timeline

| When | Goal | Done when |
|---|---|---|
| **now → 13:15** | Registry + detection + replacement working on `data/sample` | Our sample output passes `validar.mjs` and most of the 13 sample tests |
| **13:15 → 14:00** | Run on real data; leak sweep; fix the biggest leaks | `validar.mjs` OK on real output; leak sweep near 0; 5/5 public tests locally |
| **14:00 → 14:30** | Deploy (always-on + volume + SALT); test the live URL end to end | `/health`, `/run`, poll, `/output`, `/anonymize` all work on the public URL |
| **14:30 → 14:50** | **Spend evaluation run #1** (scores 60 % of hidden tests) | Score shown in `/evento/reto` — protects the 30 % share |
| **15:00** | Incident. Do not touch `SALT`; keep the map volume | — |
| **after 15:00** | Re-run on the grown data; check old entities keep pseudonyms; fix from run #1 details | Run #2 (75 %), later run #3 (100 %) |
| before 19:00 | Service must stay up from 19:00 to demo day end. Fill in `DECISIONS.md` costs; demo | — |

## Build order (one Pi task each)

1. `registry.py`: build people from `clientes.csv` + `empleados.xml`; link `users.json`; all forms per person.
2. `pseudonyms.py`: email/cédula/phone/address/birth generators in the same format; bigger name lists.
3. `detect.py` + `core.py`: regex + registry match + keep-list; resolve (strong IDs first); replace in all fields keeping the form.
4. `generalize.py`: occupation and barrio tables from `clientes.csv`; apply in the table and in text.
5. `entities.json`: originals (every form found), pseudonyms actually used, records, kept orgs.
6. `output.leak_sweep`: port the judge's `contiene()` normalization from `scripts/validar.mjs`.
7. Stretch: `jev.py` for leftover capitalized spans.

## Rules to never break

- Each record exactly once, with the same id. Never empty a record, never drop a key or column.
- Never change `SALT` or delete `data/state/` after run #1.
- Don't spend a run on the pass-through core: it leaks everything.
- Validate locally before pressing VALIDAR ENTRYPOINT. Invalid output burns a run with no score.
