"""
Dave's Redistricting district composites, exported by hand.

WHAT THIS IS AND WHY IT IS NOT COOK PVI. Dave's Redistricting computes, for
every district on a plan, a composite of recent statewide election results
inside those boundaries. It is a better object than a single presidential
result for the same reason Cook's index is -- it averages several contests
rather than betting the baseline on one candidate -- and it has one property
Cook's does not: it can be published. That is the whole reason this source
exists. The redistricting page needs a district partisanship number readers can
check, and Cook's cannot be one.

THE UNITS ARE NOT PVI AND THIS PARSER DOES NOT PRETEND THEY ARE.

Cook's PVI is defined as a deviation: how much more Democratic a district is
than the nation, in share points. DRA's composite is an absolute two-party
Democratic share inside the district, on whatever set of elections that state's
plan selected. Those are different quantities, and emitting DRA under the
`pvi` name would put two incompatible scales in one column for anything reading
the archive by quantity.

So the rows go out as `composite_share` and `composite_share_prior`, in
percent, exactly as DRA computed them. Turning a share into a deviation
requires choosing what to centre on, and that is a modelling decision with
consequences -- it decides what the national tide MEANS when it is added to a
district -- so it happens once, visibly, in model/polling.py, and not quietly
here. See dra_baseline() there.

WHAT IS DROPPED. DRA's export carries a statewide summary row with a blank ID
and, for most states, per-district demographics. The statewide row is not a
district and is not emitted as one; the demographics are real data we hold but
have no model for yet, so they stay in raw/ rather than becoming rows nobody
reads. Both are one small change away if the redistricting page wants them.

MAP VINTAGE. The stored filename is <state>-<current|prior>, written by
dra_import.py --snapshot from the CONTAINING DIRECTORY, never the filename:
DRA names an export after the map's vintage, so Arkansas's current lines arrive
called "AR-2022-...". Ten states have a prior map; the other forty do not,
because their 2022 lines and their 2026 lines are the same lines.

PUBLICATION. Set in the registry, and it is `private` today. Not because DRA
forbid republication -- they do not -- but because their data is assembled from
state partners (VEST, the Redistricting Data Hub and others, differing by
state) and this archive has not yet written their attribution table into the
methods page. Publishing a derived margin before the attribution is right would
be taking the permission without honouring its condition. Relax the registry
field when that table lands, and not before.
"""
from __future__ import annotations

import csv
import io

from . import Context, LoadedArtifact, Row, race_id

# A district's ID in a DRA congressional export is the district number, and
# the statewide summary row carries an empty one.
_STATEWIDE_ID = ""


def _districts(art: LoadedArtifact) -> list[tuple[str, float]]:
    """[(district, two_party_D_pct)] from one export. Statewide row excluded."""
    text = art.body.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"{art.name}: empty export")
    for need in ("ID", "Dem", "Rep"):
        if need not in rows[0]:
            raise ValueError(
                f"{art.name}: no {need!r} column — this is not a DRA district "
                f"statistics export. Re-download with the district table "
                f"selected rather than the plan summary.")
    out = []
    for r in rows:
        rid = (r.get("ID") or "").strip().strip('"')
        if rid == _STATEWIDE_ID:
            continue                     # the statewide summary, not a district
        try:
            d, rp = float(r["Dem"]), float(r["Rep"])
        except (TypeError, ValueError):
            continue
        if d + rp <= 0:
            # DRA writes "Un" for a district with no contest in the composite,
            # and an empty row for a plan still being drawn. Neither is a zero.
            continue
        out.append((rid, 100.0 * d / (d + rp)))
    return out


def parse(artifacts: dict[str, LoadedArtifact], ctx: Context) -> list[Row]:
    if not artifacts:
        raise ValueError(
            "no DRA exports stored — run:  python3 forecast/collect/"
            "dra_import.py --dir forecast/data/DRA --snapshot")

    rows: list[Row] = []
    seen: set[tuple[str, str, str]] = set()
    for name, art in sorted(artifacts.items()):
        st_ver = name.rsplit("-", 1)
        if len(st_ver) != 2 or st_ver[1] not in ("current", "prior"):
            # Not ours to read. Refusing quietly is right here: the raw
            # directory is allowed to hold things this parser does not model.
            continue
        state, version = st_ver[0].upper(), st_ver[1]
        quantity = ("composite_share" if version == "current"
                    else "composite_share_prior")
        for dist, share in _districts(art):
            try:
                d2 = f"{int(dist):02d}"
                rid = race_id("house", state, d2)
            except (ValueError, TypeError):
                continue
            key = (state, d2, quantity)
            if key in seen:
                # Two files claiming the same state and vintage. Silently
                # taking the last one would make the baseline depend on
                # filesystem order, which is not a property a baseline may
                # have.
                raise ValueError(
                    f"{state}-{d2} appears twice for the {version} map — two "
                    f"exports for one state and vintage are in raw/dra/. "
                    f"Remove one and re-snapshot.")
            seen.add(key)
            rows.append(ctx.row(art, race_id=rid, chamber="house", state=state,
                                district=d2, quantity=quantity,
                                value=round(share, 4), unit="pct"))
    if not rows:
        raise ValueError("DRA exports stored but no district rows read from "
                         "them — check the ID/Dem/Rep columns")
    return rows
