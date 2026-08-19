# Phase 2: parsers

One module per source. Each reads from `data/<cycle>/raw/<source_id>/` and emits
rows in the shared long format. **Parsers never fetch.**

Target schema, identical for every source and every quantity:

    snapshot_date, source_id, race_id, chamber, state, district,
    quantity, value, unit, captured_at, raw_path

`quantity` is one of: `win_prob_R`, `vote_share_R`, `seats_R`, `rating_ordinal`,
`rating_numeric`, `margin`, `turnout_pct`

Race IDs carry the map vintage: `SEN_GA_2026`, `GOV_MI_2026`, `HOU_PA_07_2026`.
This is the same convention students use in their submission CSVs, so the class
row merges in without translation and needs no special-casing anywhere.

Because parsers read from stored raw captures rather than the network, they can
be written at any time and re-run over the whole history. That is the entire
reason phase 1 refuses to parse. Write these in September against captures that
already exist; nothing is lost by waiting.
