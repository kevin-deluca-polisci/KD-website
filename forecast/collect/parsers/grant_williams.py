"""
Grant Williams 2026 Midterms Forecast. Publication: individual (MIT licence).

Structure verified against a live capture on 2026-08-19. Key names below are
real, not guesses.

TWO THINGS TO KNOW ABOUT THIS SOURCE

1. The JSON is published to GitHub Pages, which overwrites on every run.
   `outputs/` is gitignored in his repo, so his git history is NOT an archive
   of the forecast. Nothing preserves his daily series except our capture.

2. His per-district `pvi` field is sourced from Cook PVI (see `pvi_source`).
   We DO extract it, because it is the district baseline the class model needs,
   but every PVI row is stamped `publication="private"` so it can be used for
   teaching and modelling and can never reach the published tier. Cook's index
   is proprietary; collecting it second-hand under Grant's MIT republication is
   not the same as being free to republish it ourselves.

   aggregate.py additionally lists `pvi` in NEVER_PUBLISH, so even a future
   parser that forgot the stamp could not leak it. Two independent locks.

NOT independent: polling input comes from Silver Bulletin.
"""
from __future__ import annotations

from . import Context, LoadedArtifact, Row, NATIONAL_HOUSE, NATIONAL_SENATE, race_id

# Observed shape of forecast.json (2026-08-19):
#   summary        prob_dem_majority, median_dem_seats, mean_dem_seats,
#                  ci_90_low/high, ci_50_low/high, election_day_national_margin,
#                  national_likelihood_margin, poll_updated_current_margin,
#                  published_generic_ballot_margin, model_type
#   national_model prior / current_sentiment / election_day, each mean+std+ci
#   categories     dem{safe,likely,lean}, toss_up, rep{safe,likely,lean}
#   districts[]    id "PA-10", state, district_number, prob_dem,
#                  posterior_margin, prior_margin, mean_vote_share,
#                  credible_interval_90, category, data_quality, pvi (Cook — skip)

_CHAMBERS = {
    "house_forecast": ("house", NATIONAL_HOUSE),
    "senate_forecast": ("senate", NATIONAL_SENATE),
}


def _num(d: dict, key: str):
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    rows: list[Row] = []
    seen_any = False

    for art_name, (chamber, natl) in _CHAMBERS.items():
        art = artifacts.get(art_name)
        if art is None:
            continue                      # senate_forecast.json may not exist yet
        seen_any = True
        doc = art.json()
        if not isinstance(doc, dict):
            raise ValueError(f"{art_name}: expected an object at the top level")

        # ---- national ----------------------------------------------------
        summary = doc.get("summary") or {}

        # `election_day_national_margin` is the forecast FOR election day, which
        # is the quantity comparable to every other source in the archive.
        # The other three margins are current-state readings, kept separately so
        # the dispersion figure compares like with like.
        margin = _num(summary, "election_day_national_margin")
        if margin is not None:
            rows.append(ctx.row(art, race_id=natl, quantity="margin_D",
                                value=round(margin, 3), unit="pct"))

        prob = _num(summary, "prob_dem_majority")
        if prob is not None:
            rows.append(ctx.row(art, race_id=natl, quantity="win_prob_D",
                                value=round(prob, 4), unit="prob"))

        seats = _num(summary, "mean_dem_seats")
        if seats is None:
            seats = _num(summary, "median_dem_seats")
        if seats is not None:
            rows.append(ctx.row(art, race_id=natl, quantity="seats_D",
                                value=round(seats, 2), unit="seats"))

        # ---- per race ----------------------------------------------------
        for d in doc.get("districts") or []:
            if not isinstance(d, dict):
                continue
            state = str(d.get("state") or "").upper()
            if len(state) != 2:
                continue
            num = d.get("district_number")
            try:
                if chamber == "house":
                    if num is None:
                        continue
                    rid = race_id("house", state, str(num))
                    dist = f"{int(num):02d}"
                else:
                    rid, dist = race_id("senate", state), ""
            except (ValueError, TypeError):
                continue

            p = _num(d, "prob_dem")
            if p is not None:
                rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                                    district=dist, quantity="win_prob_D",
                                    value=round(p, 4), unit="prob"))
            m = _num(d, "posterior_margin")
            if m is not None:
                rows.append(ctx.row(art, race_id=rid, chamber=chamber, state=state,
                                    district=dist, quantity="margin_D",
                                    value=round(m, 3), unit="pct"))

            # Cook PVI, for class use only. PRIVATE — see the module docstring.
            # The explicit publication= override is the point: this source is
            # otherwise `individual`, and without the stamp these rows would be
            # published alongside his forecast.
            pvi = _num(d, "pvi")
            if pvi is not None:
                rows.append(ctx.row(art, publication="private",
                                    race_id=rid, chamber=chamber, state=state,
                                    district=dist, quantity="pvi",
                                    value=round(pvi, 2), unit="pct"))

    if not seen_any:
        raise ValueError("no forecast artifacts stored for this date")
    if not rows:
        keys = sorted({k for a in artifacts.values()
                       if isinstance(a.json(), dict) for k in a.json()})
        raise ValueError(
            "parsed 0 rows — the JSON shape has changed. "
            f"Top-level keys seen: {keys}")
    return rows
