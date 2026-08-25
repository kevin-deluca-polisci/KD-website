"""Swap the monthly approximation for Ballotpedia's exact dated list."""
import pathlib

p = pathlib.Path("forecast/model/conditions.py")
s = p.read_text()

a = s.index("# BALLOTPEDIA'S COUNT, WHICH IS THE ONE HIS VARIABLE ACTUALLY TRACKS.")
b = s.index("def ballotpedia_open_seats(")
c = s.index("def dem_senate_retirements(")

new = '''# BALLOTPEDIA'S COUNT, WHICH IS THE ONE HIS VARIABLE ACTUALLY TRACKS.
#
# Their series counts U.S. House incumbents NOT SEEKING RE-ELECTION, which is
# the construction the MEDSL reconstruction pointed at without being able to
# name. Set against Lockerbie's |OpenInt| for the three midterms that overlap:
#
#     2014   his 46   ballotpedia 41
#     2018   his 56   ballotpedia 52
#     2022   his 49   ballotpedia 49
#
# Within three seats on average and exact in 2022, against a reconstruction
# that ran 9 to 27 high in every year tested. Same variable.
#
# THE FULL DATED LIST, not the monthly summary. An earlier version of this held
# their month-by-month counts, which forced two compromises: the series could
# only step once a month, and the months summed to 59 against a published total
# of 60, so a phantom "one announcement before 2025" had to be carried to
# reconcile them. The dated list dissolves both. The 60th is Chuck Edwards on
# 2026-08-05, which the monthly table had not yet picked up — the two were
# snapshots of the same page taken at different times, and the difference was
# never a pre-2025 announcement at all.
#
# It validates: 60 rows, 23 D and 37 R, matching their own summary exactly.
#
# ENTERED BY HAND, DELIBERATELY. ballotpedia.org disallows this path in
# robots.txt, and the registry's position is that working round a bot filter is
# not something this project can do and still describe its own methods
# honestly. A person reading a page they are entitled to view and typing it out
# is a different act, and the same one cook_pvi already depends on.
#
#   source: List_of_U.S._House_incumbents_who_are_not_running_for_re-election
#           _in_2026, read 2026-08-25
BALLOTPEDIA_FILE = COND / "ballotpedia_not_seeking_2026.csv"

# Their history, for the record and for anyone checking the claim above.
BALLOTPEDIA_HISTORY = {           # cycle: (D, R, total not seeking re-election)
    2026: (23, 37, 60), 2024: (24, 21, 45), 2022: (31, 18, 49),
    2020: (9, 26, 36), 2018: (18, 34, 52), 2016: (16, 24, 40),
    2014: (16, 25, 41), 2012: (23, 20, 43),
}
LOCKERBIE_OPENINT = {2014: 46, 2018: 56, 2022: 49}

_BP_CACHE: list[dict] | None = None


def ballotpedia_events() -> list[dict]:
    global _BP_CACHE
    if _BP_CACHE is None:
        if BALLOTPEDIA_FILE.exists():
            _BP_CACHE = sorted(csv.DictReader(BALLOTPEDIA_FILE.open()),
                               key=lambda r: r["date"])
        else:
            _BP_CACHE = []
    return _BP_CACHE


def ballotpedia_open_seats(asof: str | None = None) -> tuple[int, str]:
    """Incumbents who had announced they were not seeking re-election by `asof`.

    Exact to the day, from their dated list. Lockerbie reads a June figure and
    June's announcements are part of it, so the comparison is inclusive.
    """
    ev = ballotpedia_events()
    if not ev:
        return 0, "no Ballotpedia list on disk"
    end = asof or dt.date.today().isoformat()
    n = sum(1 for r in ev if r["date"] <= end)
    return n, (f"{n} House incumbents had announced they were not seeking "
               f"re-election by {end} — Ballotpedia's dated list, entered by "
               f"hand, read 2026-08-25")


'''
s = s[:a] + new + s[c:]
p.write_text(s)
print("conditions.py: exact dated list wired in")
