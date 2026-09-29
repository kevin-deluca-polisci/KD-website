# Endorsements (not part of the forecast archive)

Candidate endorsements listed in Wikipedia articles for U.S. Senate, House and
governor races, 2018 to 2026. These files are kept here, outside
`forecast/data/2026/`, because they are not part of the forecast data archive
and are not used on the forecast site.

| File | What it is |
|---|---|
| `endorsements_<year>.json` | One record per endorsement, parsed from the race articles (`forecast/collect/wiki_endorsements.py`). |
| `endorsement_panel.csv` | Endorsements joined to election returns, one row per general-election candidate (`forecast/model/endorsement_join.py`). |

The race articles are still captured every day as part of the forecast
collection (source `wiki_endorsements` in `forecast/sources/2026.yaml`); the
same pages supply the race-level polling averages. The JSON files are rebuilt
by hand with `wiki_endorsements.py --archive --json`.

Source text is from Wikipedia and is available under CC BY-SA 4.0.
