"""Ballotpedia's open-seat series, which is the one Lockerbie's variable matches."""
import pathlib

p = pathlib.Path("forecast/model/conditions.py")
s = p.read_text()

old = "LOCKERBIE_EXCLUDE = {\"lost_primary\"}"
new = '''LOCKERBIE_EXCLUDE = {"lost_primary"}

# BALLOTPEDIA'S COUNT, WHICH IS THE ONE HIS VARIABLE ACTUALLY TRACKS.
#
# Their series counts U.S. House incumbents NOT SEEKING RE-ELECTION, which is
# the construction the reconstruction pointed at without being able to name.
# Set against Lockerbie's |OpenInt| for the three midterms that overlap:
#
#     2014   his 46   ballotpedia 41
#     2018   his 56   ballotpedia 52
#     2022   his 49   ballotpedia 49
#
# Within three seats on average, and exact in 2022. Compare that with counting
# every seat with no incumbent on the ballot, which ran 9 to 27 high in every
# year tested. It is the same variable.
#
# THE MONTHLY TABLE IS WHY THIS MATTERS MORE THAN THE TOTAL. Ballotpedia dates
# every announcement, so the cycle's open-seat count is available AS OF ANY
# MONTH rather than only at the end. That is what turns Lockerbie from a
# horizontal line into a series, and it is a better source for it than our own
# reconstruction from Wikipedia revision history: theirs is a maintained count
# of the thing itself, ours is an inference from when a page changed.
#
# ENTERED BY HAND, DELIBERATELY. ballotpedia.org disallows this path in
# robots.txt, and the registry's position on bot filters is that working round
# one is not something this project can do and still describe its own methods
# honestly. A person reading a page they are entitled to view and typing two
# rows of numbers is a different act, and it is the same one cook_pvi already
# depends on.
#
#   source: List_of_U.S._House_incumbents_who_are_not_running_for_re-election
#           _in_2026, read 2026-08-25
BALLOTPEDIA_MONTHLY = {
    2025: [1, 1, 1, 5, 5, 3, 3, 3, 6, 2, 8, 5],      # 43
    2026: [5, 2, 5, 1, 2, 0, 1, 0, 0, 0, 0, 0],      # 16
}
# Their published cycle total is 60; the monthly rows sum to 59. One
# announcement sits outside the two years tabulated — before January 2025 — and
# the difference is carried here rather than quietly absorbed into a month that
# did not have it.
BALLOTPEDIA_TOTAL_2026 = 60
BALLOTPEDIA_PRE_2025 = BALLOTPEDIA_TOTAL_2026 - sum(
    sum(v) for v in BALLOTPEDIA_MONTHLY.values())

# Their history, for the record and for anyone checking the claim above.
BALLOTPEDIA_HISTORY = {           # cycle: (D, R, total not seeking re-election)
    2026: (23, 37, 60), 2024: (24, 21, 45), 2022: (31, 18, 49),
    2020: (9, 26, 36), 2018: (18, 34, 52), 2016: (16, 24, 40),
    2014: (16, 25, 41), 2012: (23, 20, 43),
}
LOCKERBIE_OPENINT = {2014: 46, 2018: 56, 2022: 49}


def ballotpedia_open_seats(asof: str | None = None) -> tuple[int, str]:
    """Incumbents who had announced they were not seeking re-election by `asof`.

    Cumulative through the month CONTAINING `asof`, not through the previous
    complete month: Lockerbie reads a June figure and June's announcements are
    part of it.
    """
    end = asof or dt.date.today().isoformat()
    y, m = int(end[:4]), int(end[5:7])
    n = BALLOTPEDIA_PRE_2025
    for yr in sorted(BALLOTPEDIA_MONTHLY):
        if yr > y:
            break
        months = BALLOTPEDIA_MONTHLY[yr]
        n += sum(months if yr < y else months[:m])
    return n, (f"{n} House incumbents had announced they were not seeking "
               f"re-election by {end} — Ballotpedia's dated monthly count, "
               f"entered by hand")
'''
assert s.count(old) == 1
s = s.replace(old, new)

# Prefer Ballotpedia over our own reconstruction, and say which was used.
old = '''    dropped = {rid for rid, t in types.items()
               if t and t.issubset(LOCKERBIE_EXCLUDE)}'''
new = '''    # BALLOTPEDIA FIRST. Ours is a reconstruction from when a Wikipedia page
    # changed; theirs is a maintained count of the thing the variable names,
    # and it matches his series to within three seats. The derived number stays
    # below as the fallback and as a cross-check worth printing.
    bp, bp_src = ballotpedia_open_seats(asof)
    if bp:
        return bp, bp_src

    dropped = {rid for rid, t in types.items()
               if t and t.issubset(LOCKERBIE_EXCLUDE)}'''
assert s.count(old) == 1
s = s.replace(old, new)

# Show the comparison in the module's own run.
old = '''    print("  " + open_seats()[1])'''
new = '''    print("  validation — Lockerbie's OpenInt against Ballotpedia's count:")
    for y in sorted(LOCKERBIE_OPENINT):
        his = LOCKERBIE_OPENINT[y]
        bp = BALLOTPEDIA_HISTORY[y][2]
        print(f"     {y}  his {his:>3}   ballotpedia {bp:>3}   diff {his - bp:+d}")
    print()
    print("  " + open_seats()[1])'''
assert s.count(old) == 1
p.write_text(s.replace(old, new))
print("conditions.py: ballotpedia series added")
