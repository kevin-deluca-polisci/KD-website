"""
Ray Fair's mid-term House vote-share equation. Publication: individual.

WHAT THIS SOURCE IS. Fair estimates three vote-share equations — presidential,
on-term House, mid-term House — on data back to 1916, and publishes a running
prediction for the current cycle. The mid-term House equation (3a in Table 2 of
the November 2022 update) predicts Vcc, the DEMOCRATIC SHARE OF THE TWO-PARTY
HOUSE VOTE, from two economic variables and three non-economic ones:

    Vcc = 49.04 - 3.36*I                     party in power (I = -1 here)
              + 0.695*(Vc_-2 - 50)           House incumbency
              - 0.228*(Vp_-2 - 50)           balance, from the last presidential vote
              - 0.370*Pcc*I                  inflation, GDP deflator, first 7 quarters
              + 0.528*Zcc*I                  strong-growth quarters

WE TAKE THE PUBLISHED NUMBER RATHER THAN RE-ESTIMATING IT. The equation is
reproducible; its INPUTS are not. Pcc and Zcc for a cycle still in progress come
from Fair's own quarterly macro forecasts of GDP and the deflator, which are a
separate model we do not have and could not stand behind. Recomputing the
equation with our own guesses at those would produce a number that is not Fair's
forecast while carrying his name. So the parser reads what he publishes.

WHY IT BELONGS IN THE FUNDAMENTALS CATEGORY, AND WHY IT IS WORTH HAVING. It
predicts from economic conditions and knows nothing about polls or candidates,
which is the definition this site uses. It is also the only other pure
fundamentals forecast we have found for 2026, and it disagrees with ours
sharply — Fair has the race almost tied while our model has a substantial
Democratic margin. A category average of one model hides that; an average of
two shows it.

THE UNITS TRAP. Fair publishes a VOTE SHARE (50.89 means the Democrats take
50.89% of the two-party vote). Everything in this archive is a MARGIN. The
conversion is margin = 2*(share - 50), and getting it wrong by dropping the 2
would halve his forecast and make him look closer to us than he is.
"""
from __future__ import annotations

import re

from . import Context, LoadedArtifact, NATIONAL_HOUSE, Row

# The predictions table is a <pre> block:
#
#                          Pcc   Zcc    Vcc
#   July 31, 2026          4.05  4.286  50.89
#   April 30, 2026         3.43  4.286  50.66
#
# Matched on the shape of a row rather than on its position in the document, so
# a new prediction appearing at the top — which is how this page updates — is
# picked up without touching this file.
_ROW = re.compile(
    r"([A-Z][a-z]+\s+\d{1,2},\s+(?:19|20)\d{2})"      # December 23, 2025
    r"\s+(-?\d+(?:\.\d+)?)"                            # Pcc
    r"\s+(-?\d+(?:\.\d+)?)"                            # Zcc
    r"\s+(-?\d+(?:\.\d+)?)"                            # Vcc
)
_TAG = re.compile(r"<[^>]+>")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}

# A two-party vote share has to be near 50. Anything outside this is a Pcc or a
# coefficient that the row regex has lined up in the wrong column.
_SHARE_LO, _SHARE_HI = 35.0, 65.0


def _text(raw: str) -> str:
    s = raw.replace("&nbsp;", " ").replace("&#160;", " ")
    return _TAG.sub(" ", s)


def _iso(datestr: str) -> str | None:
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+((?:19|20)\d{2})", datestr.strip())
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    if not artifacts:
        raise ValueError("no Fair artifacts stored for this date")

    rows: list[Row] = []
    seen: set[str] = set()
    found_any = False

    for art in artifacts.values():
        text = _text(art.text())
        for m in _ROW.finditer(text):
            date_iso = _iso(m.group(1))
            share = float(m.group(4))
            if date_iso is None or not (_SHARE_LO <= share <= _SHARE_HI):
                continue
            found_any = True
            # Every published prediction, not just the newest, each carried on
            # the date Fair made it. The page keeps its own history, so one
            # capture backfills the whole series — and a forecast recorded
            # against the day it was issued is the only version of it that can
            # honestly be scored later.
            #
            # Backdating is capped at today by Context.row, so a page listing a
            # future revision cannot push a row into the future.
            if date_iso in seen or date_iso > ctx.snapshot_date:
                continue
            seen.add(date_iso)
            rows.append(ctx.row(
                art, snapshot_date=date_iso, race_id=NATIONAL_HOUSE,
                chamber="national", state="", district="",
                quantity="margin_D",
                # share -> margin. See THE UNITS TRAP above.
                value=round(2.0 * (share - 50.0), 3), unit="margin"))

    if not rows:
        raise ValueError(
            "found no prediction rows on Fair's page. The table is a <pre> "
            "block of 'Month D, YYYY  Pcc  Zcc  Vcc' lines; either the layout "
            "changed or the capture stored something other than the "
            f"prediction page. Matched-shape rows seen: {found_any}")
    return rows
