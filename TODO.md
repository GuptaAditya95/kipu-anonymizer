# TODO (tick as you go; see docs/BUILD_SPEC.md §6 for each task)

## Before 15:00 (run #1 must happen before the incident)
- [ ] T0 Setup: .env, download data, baseline validator OK
- [ ] T1 Tolerant normalize: discover tables/charset/separator/keys; pass-through fallback; never drop an id
- [ ] T2 Snapshot + diff (`changes`) + stability check in the run summary
- [ ] T3 Text utils: judge-equivalent normalization + name variants + slugs
- [ ] T4 Person registry (clientes, empleados, chat users) with all forms
- [ ] T5 Pseudonym generators (name/email/cedula/phone/address/birth/plate/passport) + uniqueness
- [ ] T6 Detection (regex + registry + name list + keep-list) and resolution (strong ids first)
- [ ] T7 Replacement in every field, keeping the form + generalization of quasi-identifiers
- [ ] T8 entities.json builder + judge-equivalent leak sweep
- [ ] T9 Sample passes: 13/13 sample tests, validator OK, similarity check
- [ ] T10 Full data: 5/5 public tests, leak sweep ≈ 0, validator OK
- [ ] T11 /anonymize live tests (known / new / namesake / cold start) < 5 s
- [ ] T12 Deploy on Railway (volume /data, SALT) → end-to-end check → **evaluation run #1**

## After 15:00
- [ ] T13 Incident: read `changes`, verify stability = 100%, fix, write INCIDENT.md, run #2
- [ ] T14 Review loop: misses collector + scripts/review.py report
- [ ] T15 Optional Jev classification of leftovers
- [ ] T16 Fill DECISIONS.md costs, demo, run #3
