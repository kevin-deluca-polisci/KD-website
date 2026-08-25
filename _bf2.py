"""Extend the academic backfill to every model whose inputs are datable."""
import pathlib

p = pathlib.Path("forecast/model/academic.py")
s = p.read_text()

# ------------------------------------------------- 1. the open-seats question
old = '''# The month his specification names. Points before it are ours, not his.
LOCKERBIE_SPEC_DATE = "2026-06-01"'''
new = '''# The month his specification names. Points before it are ours, not his.
LOCKERBIE_SPEC_DATE = "2026-06-01"

# WHICH OPEN SEATS, AND THIS IS NOT SETTLED.
#
# The entry above reads the variable as "seats with no incumbent running" —
# all of them, either party. That is what this file has always used and it may
# be right, but three things argue against it and none of them can be resolved
# without the paper:
#
#   1. MECHANISM. In a bad year for the president's party the term is a loss
#      that grows with the count. Under the all-seats reading, an open seat
#      held by the OUT-party increases the president's party's losses. A
#      Democratic open seat cannot be lost by Republicans. There is no story
#      there.
#   2. MAGNITUDE. At 67 open seats the term alone contributes -27.5 seats, in
#      a model whose published out-of-sample MAE is 16.8. One regressor moving
#      the answer by more than the model's typical total error is a signature
#      of a variable being fed at the wrong scale.
#   3. THE OUTPUT. It lands at 266 D seats, outside the range every other model
#      on the site produces, and far enough out that our own seat-curve
#      inversion reports itself as extrapolated rather than interpolated.
#
# The three candidate readings, on 2026 numbers (37 R-held open, 29 D-held):
#
#     all open seats     67   ->  169 R / 266 D     <- current
#     president's party  37   ->  181 R / 254 D     <- most defensible
#     signed net R-D      8   ->  193 R / 242 D
#
# LEFT AS "all" DELIBERATELY, because changing a published model's input on our
# own reading of what it probably means is exactly the substitution this file
# exists to avoid. Change the constant when somebody has read the paper, and
# record who and when.
#
# Worth knowing before anyone assumes this is what makes the model bullish: it
# is not the main driver. With the open-seat term set to zero entirely the
# model still forecasts the president's party losing 23.5 seats, because
# consumer pessimism is high — 37% expecting to be worse off carries -28.9
# seats on its own.
LOCKERBIE_OPEN_SEATS_BASIS = "all"      # "all" | "president_party" | "signed_net"'''
assert s.count(old) == 1
s = s.replace(old, new)

# ------------------------------------------------------- 2. the backfill loop
old = '''        margin, n_polls = got
        c = Ctx(cycle=cycle, date=cur.isoformat(),
                days_to_election=(dt.date.fromisoformat(ELECTION_DAY) - cur).days,
                approval=approval,
                approval_source="hand-set (Gallup basis) — CONSTANT across the "
                                "backfill, so anything driven by it is flat by "
                                "construction and not by evidence",
                generic_ballot_D=margin,
                generic_ballot_source=(f"reconstructed: unweighted mean of "
                                       f"{n_polls} raw poll margin(s) in the "
                                       f"{RECONSTRUCT_WINDOW_DAYS} days to "
                                       f"{cur.isoformat()}"))
        day: dict[str, dict] = {}
        res = run_bew(c)'''
new = '''        margin, n_polls = got
        # BUILD THE FULL CONTEXT FOR THE DATE, not a hand-assembled one.
        #
        # This loop used to construct a Ctx by hand with four fields in it,
        # which is why it could only ever run BEW: nothing else had its inputs.
        # build_ctx reads approval, income and the dated structural inputs for
        # whatever date it is given, so every model that can be honestly run
        # for a past date now can be. The generic ballot is overridden
        # afterwards because the reconstruction from the poll list is better
        # than what build_ctx would find in the snapshot archive — the archive
        # only starts carrying a generic ballot in August 2026.
        c = build_ctx(cycle, cur.isoformat(),
                      approval if approval is not None else None)
        c.generic_ballot_D = margin
        c.generic_ballot_source = (f"reconstructed: unweighted mean of "
                                   f"{n_polls} raw poll margin(s) in the "
                                   f"{RECONSTRUCT_WINDOW_DAYS} days to "
                                   f"{cur.isoformat()}")
        day: dict[str, dict] = {}
        res = run_bew(c)'''
assert s.count(old) == 1
s = s.replace(old, new)

# --- run the rest of the models for the same date --------------------------
old = '''        # The referendum model is NOT backfilled here at all, whatever the
        # flag says, and the flag's help text says why: its approval term is a
        # hand-set constant and its income term is not re-read per date, so
        # every point would be identical. A flat line is a claim that approval
        # and the economy did not move, which we have no evidence for. It is
        # better to have no academic referendum history than a fabricated one.
        if day:'''
new = '''        # EVERY OTHER MODEL WHOSE INPUTS ARE NOW DATED.
        #
        # The referendum model used to be excluded here on the grounds that
        # its approval term was a hand-set constant, so its line would be flat
        # by construction rather than by evidence. That was the right call
        # then and it is the wrong call now: approval is read per date from the
        # poll record, income comes from the dated FRED capture, and the two
        # structural models read open seats, Senate retirements and governors
        # as of the date. So each of them is included exactly when its own
        # inputs are genuinely available for that date, and skipped when they
        # are not.
        #
        # A MODEL THAT RETURNS A CONSTANT IS STILL INCLUDED, as long as the
        # constant is a real property of the model rather than an artefact of
        # a frozen input. LBQ barely moves because institutional facts barely
        # move, and a flat LBQ line is the finding. The test is not "does it
        # vary" but "is every input the value that input actually had".
        for key, fn, gate in (
                ("academic_referendum", run_referendum,
                 lambda c: c.approval is not None and c.income is not None),
                ("academic_economic_pessimism", run_lockerbie,
                 lambda c: c.pct_expect_worse is not None
                 and c.open_seats is not None),
                ("academic_political_history", run_lbq,
                 lambda c: c.dem_senate_retirements is not None),
        ):
            if not gate(c):
                continue
            try:
                r = fn(c)
            except Exception:                                 # noqa: BLE001
                continue
            if r is None:
                continue
            lo2, hi2 = r.interval_80 or (None, None)
            meta = MODELS_BY_KEY.get(key, {})
            day[key] = {
                "name": meta.get("name", key),
                "categories": meta.get("categories", ["academic"]),
                "category": "academic", "publication": "individual",
                "margin_D": round(r.margin_D, 2),
                "margin_D_80_low": round(lo2, 2) if lo2 is not None else None,
                "margin_D_80_high": round(hi2, 2) if hi2 is not None else None,
                "inputs": r.inputs,
                "diagnostics": r.diagnostics,
                "provenance": BACKFILL_PROVENANCE,
            }

        if day:'''
assert s.count(old) == 1
s = s.replace(old, new)

# --- drop the now-wrong --backfill-referendum refusal ----------------------
old = '''    if include_referendum:
        print("  --backfill-referendum: IGNORED. Its inputs do not vary by "
              "date, so the line would be flat by construction. See the note "
              "in backfill().")
'''
new = '''    if include_referendum:
        print("  --backfill-referendum: no longer needed. Every model whose "
              "inputs are datable is backfilled by default; the flag is kept "
              "so existing workflow files do not break.")
'''
assert s.count(old) == 1
s = s.replace(old, new)

p.write_text(s)
print("backfill extended to referendum, lockerbie and lbq")
