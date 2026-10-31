# 5-minute demo script (draft)

1. **The problem (30 s).** Kipu Pay wants to sell its support corpus. Two conditions: it stays useful, and nobody is exposed.
2. **Before / after (60 s).** Show one chat thread, one PDF and one `clientes` row side by side. Point out what changed: names, cédula, email, filename, PDF author, generalized occupation. Then what stayed: Bancolombia, amounts, city.
3. **Consistency (45 s).** Show one person in `entities.json` with all their original forms → one pseudonym in chat, doc and table. Then show the two namesakes with two different pseudonyms.
4. **Live `/anonymize` (60 s).** Type a message naming a known client. They come back with the same pseudonym as in the output. Then type a new person, who gets a new unique name.
5. **Architecture and trade-offs (60 s).** The diagram, plus three decisions: deterministic code instead of a runtime agent, registry first, HMAC + persisted map, which survived the 15:00 data growth.
6. **Measurement and honesty (45 s).** Scores from the runs, leak sweep result, cost (tokens and time), known gaps.
