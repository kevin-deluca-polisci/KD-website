"""Wire Lockerbie and LBQ onto the dated structural inputs."""
import pathlib

p = pathlib.Path("forecast/model/academic.py")
s = p.read_text()

# ------------------------------------------------------------------- Ctx ---
old = '''    generic_ballot_D: float | None = None      # D minus R margin, UNSHRUNK
    generic_ballot_source: str = ""
    notes: list[str] = field(default_factory=list)'''
new = '''    generic_ballot_D: float | None = None      # D minus R margin, UNSHRUNK
    generic_ballot_source: str = ""
    # STRUCTURAL INPUTS, AS OF `date`. These were module constants, which is
    # the whole reason Lockerbie and LBQ could only draw horizontal lines: not
    # because the models do not move but because their inputs were frozen at
    # whatever somebody read off a page one afternoon. See model/conditions.py.
    open_seats: int | None = None
    open_seats_source: str = ""
    dem_senate_retirements: int | None = None
    dem_governors: int | None = None
    structural_source: str = ""
    pct_expect_worse: float | None = None
    pct_expect_worse_source: str = ""
    notes: list[str] = field(default_factory=list)'''
assert s.count(old) == 1
s = s.replace(old, new)

# -------------------------------------------------------------- build_ctx ---
old = '''    got = fundamentals.income_from_archive(cycle)'''
new = '''    # The dated structural inputs. A failure here degrades to the published
    # constants rather than stopping the run, because a missing conditions
    # table should cost us the backfill and not the live model.
    try:
        from model import conditions as _cond
        c.open_seats, c.open_seats_source = _cond.open_seats(date, cycle)
        c.dem_senate_retirements, sen_src = _cond.dem_senate_retirements(date)
        c.dem_governors, gov_src = _cond.dem_governors(date)
        c.structural_source = f"{sen_src}; governors: {gov_src}"
        c.pct_expect_worse, c.pct_expect_worse_source = \\
            _cond.lockerbie_worse(date)
    except Exception as e:                                # noqa: BLE001
        c.notes.append(f"dated structural inputs unavailable ({e}) — the "
                       f"published constants are used instead")

    got = fundamentals.income_from_archive(cycle)'''
assert s.count(old) == 1
s = s.replace(old, new)

# -------------------------------------------------------------- lockerbie ---
old = '''    worse = LOCKERBIE_INPUTS.get("pct_expect_worse")
    opens = LOCKERBIE_INPUTS.get("open_seats")
    if worse is None or opens is None:
        return None'''
new = '''    # DATED WHERE WE HAVE IT, published constant where we do not.
    #
    # THE MONTH IS PART OF THE SPECIFICATION and that has not changed: his
    # equation reads June of the election year. Running it in March on March's
    # numbers is not his forecast, it is our estimate of what his equation
    # would have said, and the result carries `spec_date_reached` so a reader
    # can tell the two apart. Before June 2026 this line is ours; from June it
    # is his.
    worse = c.pct_expect_worse
    worse_src = c.pct_expect_worse_source
    if worse is None:
        worse = LOCKERBIE_INPUTS.get("pct_expect_worse")
        worse_src = "published constant — no dated Michigan reading for this date"
    opens = c.open_seats if c.open_seats is not None \\
        else LOCKERBIE_INPUTS.get("open_seats")
    opens_src = c.open_seats_source or "published constant"
    spec_reached = c.date >= LOCKERBIE_SPEC_DATE
    if worse is None or opens is None:
        return None'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''            "seats_before": LOCKERBIE_SEATS_BEFORE,'''
new = '''            "seats_before": LOCKERBIE_SEATS_BEFORE,
            "pct_expect_worse_source": worse_src,
            "open_seats_source": opens_src,
            "spec_date_reached": spec_reached,'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''    notes.append(LOCKERBIE_MIDTERM_RULE)'''
new = '''    if not spec_reached:
        notes.append(
            f"BEFORE THE SPECIFICATION DATE. Lockerbie's equation reads June of "
            f"the election year; this point is dated {c.date} and uses that "
            f"date's inputs instead. It is our estimate of what his equation "
            f"would have said then, not a forecast he published.")
    notes.append(LOCKERBIE_MIDTERM_RULE)'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''LOCKERBIE_SEATS_BEFORE = 220'''
new = '''LOCKERBIE_SEATS_BEFORE = 220

# The month his specification names. Points before it are ours, not his.
LOCKERBIE_SPEC_DATE = "2026-06-01"'''
assert s.count(old) == 1
s = s.replace(old, new)

# -------------------------------------------------------------------- LBQ ---
old = '''    house = _lbq_predict(LBQ_HOUSE, LBQ_INPUTS)
    senate = _lbq_predict(LBQ_SENATE, LBQ_INPUTS)'''
new = '''    # Two of these move through a cycle and one steps once; the rest are
    # institutional facts that do not change at all. Dated where we have it.
    inputs = dict(LBQ_INPUTS)
    if c.dem_senate_retirements is not None:
        inputs["dem_senate_retirements"] = c.dem_senate_retirements
    if c.dem_governors is not None:
        inputs["dem_governors"] = c.dem_governors
    spec_reached = c.date >= LBQ_SPEC_DATE
    house = _lbq_predict(LBQ_HOUSE, inputs)
    senate = _lbq_predict(LBQ_SENATE, inputs)'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''        inputs={k: v for k, v in LBQ_INPUTS.items()},'''
new = '''        inputs={**inputs, "structural_source": c.structural_source,
                "spec_date_reached": spec_reached},'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''LBQ_INPUTS: dict = {'''
new = '''# Their governor count is specified at roughly six months out. Same rule as
# Lockerbie: before this, the line is our extrapolation of their equation.
LBQ_SPEC_DATE = "2026-05-03"

LBQ_INPUTS: dict = {'''
assert s.count(old) == 1
s = s.replace(old, new)

p.write_text(s)
print("academic.py: Lockerbie and LBQ on dated inputs")
