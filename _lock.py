"""Replace speculation about OpenInt with what the author's own code shows."""
import pathlib, re

p = pathlib.Path("forecast/model/academic.py")
s = p.read_text()

start = s.index("# WHICH OPEN SEATS, AND THIS IS NOT SETTLED.")
end = s.index('LOCKERBIE_OPEN_SEATS_BASIS = "all"')
end = s.index("\n", end) + 1

new = '''# WHICH OPEN SEATS — SETTLED, from the author's own replication code.
#
# `forecastEq.R` fits the House model as
#
#     lm(Hchange ~ NYWorseJ + OpenInt)
#
# and the leave-one-out blocks carry the OpenInt value for all 36 elections
# from 1952 to 2022. Two things fall straight out of them.
#
# IT IS NOT PARTY-SPECIFIC. The midterm magnitudes run 28 to 56 with a mean of
# 41, which is far too large to be one party's open seats and matches total
# open seats closely — 1994 comes in at 52 and 2018 at 56. An earlier version
# of this comment argued at length that the variable had to be the president's
# party's open seats, on the grounds that an out-party open seat increasing the
# president's party's losses has no mechanism behind it. That reasoning was
# tidy and it was wrong: the model does not have a mechanism there, it has a
# correlation, and open-seat totals are themselves higher in years when the
# exposed party is shedding members. Reading a coefficient as if it were a
# causal story is how you end up correcting an author's specification into
# something they did not write.
#
# THE SIGN IS THE YEAR DIRECTION, confirmed rather than inferred: OpenInt is
# negative in all eighteen midterms in the sample and is 0 or either sign in
# presidential years. That is exactly the `opens * direction` this file
# already implements.
#
# So the "all open seats" reading stands and nothing here needed changing.
LOCKERBIE_OPEN_SEATS_BASIS = "all"      # verified against forecastEq.R, 2026-08-25

# WHAT IS ACTUALLY WRONG WITH THE 2026 RUN, AND IT IS NOT THE DEFINITION.
#
# Both inputs are outside the range the model was fitted on:
#
#     NYWorseJ    sample 3 to 32  (max 2022)   ours 37
#     |OpenInt|   sample 0 to 56  (max 2018)   ours 66
#
# Consumer pessimism is at a record and open seats are at a record, and the
# model is a linear extrapolation into territory it has never seen on BOTH
# regressors at once. That is the whole reason it lands at 266 D seats. Capping
# both inputs at their sample maxima gives 258 instead, so the off-scale part
# is worth about eight seats; the rest is the model genuinely reading a very
# bad year for the president's party.
#
# WORTH KNOWING BEFORE TREATING THIS AS A BUG. Run on 2022's own inputs the
# equation predicts the president's party losing 39.7 seats. They lost 9. A
# 31-seat miss on the most recent midterm, in the same direction as its 2026
# forecast. Running hot against the incumbent party is characteristic of this
# model rather than a fault in our implementation of it, and the published
# out-of-sample MAE of 16.8 seats is the author saying so.
LOCKERBIE_SAMPLE_MAX = {"pct_expect_worse": 32.0, "open_seats": 56}
'''
s = s[:start] + new + s[end:]

# Flag the extrapolation in the model's own output rather than only in a comment.
old = '''    if not spec_reached:'''
new2 = '''    off = [f"{k} = {v:g}, above the sample maximum of "
           f"{LOCKERBIE_SAMPLE_MAX[k]:g}"
           for k, v in (("pct_expect_worse", worse), ("open_seats", opens))
           if v > LOCKERBIE_SAMPLE_MAX[k]]
    if off:
        notes.append(
            "OUTSIDE THE FITTED RANGE on " + " and ".join(off) +
            ". The model was fit on 1952-2022 and this is a linear "
            "extrapolation beyond anything in that sample, which is most of "
            "why it is the most Democratic forecast on this site.")
    if not spec_reached:'''
assert s.count(old) == 1
s = s.replace(old, new2)

old = '''            "spec_date_reached": spec_reached,'''
new3 = '''            "spec_date_reached": spec_reached,
            "outside_fitted_range": bool(off),'''
assert s.count(old) == 1
s = s.replace(old, new3)

p.write_text(s)
print("lockerbie: speculation replaced with the replication code's answer")
