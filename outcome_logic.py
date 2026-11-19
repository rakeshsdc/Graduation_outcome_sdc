"""Pure-pandas logic for the Student Outcome Dashboard (no Streamlit imports)."""
import io
import re

import pandas as pd

OVERALL = "Overall (all departments)"

# The four dropdown answers of column G (Status) -> short category names
PLACED = "Placed / Employed"
HIGHER = "Higher Studies"
ENTREP = "Entrepreneurship / Self-employed"
UNPLACED = "Unplaced / Preparing / Others"
UNCLASSIFIED = "Unrecognised"
STATUS_ORDER = [PLACED, HIGHER, ENTREP, UNPLACED]

LIST_COLUMNS = [
    "Name", "Student ID", "Department", "Program", "Graduation year",
    "Passed in time", "Status", "Salary", "Salary (as entered)", "Salary note",
    "Admission year", "Admitted in graduation year", "Duplicate?", "Sheet row",
]
SALARY_EXTRA = ["Salary", "Salary (as entered)", "Salary note"]

# ---- salary interpretation
N_OK = "OK"
N_CLEAN = "Cleaned (commas/symbols removed)"
N_CONV = "Converted from words/units"
N_LPA = "Assumed LPA (value below 100)"
N_MONTH = "Converted from monthly (x12)"
N_MISS = "Missing"
N_UNREAD = "Unreadable - needs manual check"
N_RANGE = "Out of range - excluded"
MIN_ANNUAL = 100_000        # below this a yearly CTC is implausible (probably monthly / typo)
MAX_ANNUAL = 50_000_000     # above 5 crore is implausible

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SCALES = {"thousand": 1_000, "k": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
           "lacs": 100_000, "lpa": 100_000, "l": 100_000, "crore": 10_000_000,
           "crores": 10_000_000, "cr": 10_000_000, "million": 1_000_000}
_SKIP = {"and", "rupees", "rupee", "rs", "inr", "only", "per", "annum", "pa", "year", "yearly",
         "annual", "ctc", "of", "a", "approx", "approximately", "about", "around", "month",
         "monthly", "pm", "p", "m", "approximate"}


def _words_to_number(text: str):
    """'four lakhs fifty thousand' -> 450000 ; '4.5 lpa' -> 450000 ; None if not understood."""
    total, current, seen = 0.0, 0.0, False
    for t in re.findall(r"\d+(?:\.\d+)?|[a-z]+", text):
        if t in _SKIP:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", t):
            current += float(t)
        elif t in _UNITS:
            current += _UNITS[t]
        elif t in _TENS:
            current += _TENS[t]
        elif t == "hundred":
            current = (current or 1) * 100
        elif t in _SCALES:
            total += (current or 1) * _SCALES[t]
            current = 0.0
        else:
            return None
        seen = True
    return total + current if seen else None


def parse_salary(raw_text) -> tuple:
    """Return (annual rupees or NaN, note)."""
    nan = float("nan")
    s = str(raw_text).strip()
    low = s.lower()
    if low in ("", "nan", "na", "n/a", "nil", "none", "-", "--", "not disclosed", "not applicable"):
        return nan, N_MISS
    if re.fullmatch(r"\d+(\.\d+)?", s):
        value, note = float(s), N_OK
    else:
        cleaned = re.sub(r"(?<=\d),(?=\d)", "", low)           # 4,30,000 -> 430000
        cleaned = cleaned.replace("₹", " ").replace("/-", " ")
        value = _words_to_number(cleaned)
        if value is None:
            return nan, N_UNREAD
        plain = re.fullmatch(r"\s*(?:rs\.?|inr)?\s*\d+(\.\d+)?\s*(?:/-)?\s*", cleaned) is not None
        note = N_CLEAN if plain else N_CONV
        if re.search(r"month|\bpm\b|p\.m|/\s?m\b", low):
            value, note = value * 12, N_MONTH
    if note in (N_OK, N_CLEAN) and 0 < value < 100:
        value, note = value * 100_000, N_LPA
    if not (MIN_ANNUAL <= value <= MAX_ANNUAL):
        return nan, N_RANGE
    return value, note


# ------------------------------------------------------------------ helpers
def to_year(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(r"((?:19|20)\d{2})")[0]


def program_of(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.upper()
    ug = s.str.contains(r"\bUG\b|UNDER|\bB\.?\s?TECH\b", regex=True, na=False)
    pg = s.str.contains(r"\bPG\b|POST|\bM\.?\s?TECH\b|\bMBA\b|\bMCA\b", regex=True, na=False)
    return pd.Series(
        ["UG" if u and not p else "PG" if p and not u else "" for u, p in zip(ug, pg)],
        index=series.index,
    )


def yes_no(series: pd.Series) -> pd.Series:
    """'Yes' / 'No' / '' from column F answers."""
    s = series.astype(str).str.strip().str.lower()
    return pd.Series(["Yes" if x.startswith("y") else "No" if x.startswith("n") else "" for x in s],
                     index=series.index)


def status_of(series: pd.Series) -> pd.Series:
    """Map column G text to one of the four categories.

    Order matters: 'Unplaced' contains 'placed' and 'Self-employed' contains
    'employed', so the more specific answers are tested first.
    """
    out = []
    for x in series.astype(str).str.lower():
        if "unplaced" in x or "preparing" in x or "other" in x:
            out.append(UNPLACED)
        elif "higher" in x:
            out.append(HIGHER)
        elif "entrepreneur" in x or "self" in x:
            out.append(ENTREP)
        elif "placed" in x or "employed" in x:
            out.append(PLACED)
        else:
            out.append(UNCLASSIFIED)
    return pd.Series(out, index=series.index)


def normalise_id(series: pd.Series) -> pd.Series:
    return series.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)


# ------------------------------------------------------------ build frame
def build_frame(raw: pd.DataFrame, m: dict, keep: str = "last") -> pd.DataFrame:
    """Turn raw sheet rows into a clean frame and mark duplicates.

    m maps: name, sid, dept, prog, year, passed, status, salary, adm
    (name, sid, salary, adm may be None).  keep = 'last' or 'first' entry survives.
    Returns ALL rows; column `counted` is True for the row used in the counts.
    """
    blank = pd.Series("", index=raw.index)
    get = lambda k: raw[m[k]].astype(str).str.strip() if m.get(k) else blank

    df = pd.DataFrame(index=raw.index)
    df["Sheet row"] = raw.index + 2  # +1 header, +1 for 1-based rows
    df["Name"] = get("name")
    df["Student ID"] = get("sid")
    df["Department"] = get("dept")
    df["Program"] = program_of(get("prog"))
    df["Graduation year"] = to_year(get("year"))
    df["Passed in time"] = yes_no(get("passed"))
    df["Status"] = status_of(get("status"))
    parsed = get("salary").apply(parse_salary)
    df["Salary (as entered)"] = get("salary")
    df["Salary"] = pd.to_numeric(parsed.apply(lambda t: t[0]), errors="coerce")
    df["Salary note"] = parsed.apply(lambda t: t[1])
    df["Admission year"] = to_year(get("adm"))

    # drop completely empty rows
    df = df[(df["Department"] != "") | df["Graduation year"].notna()
            | (df["Name"] != "") | (df["Student ID"] != "")].copy()

    df["is_passed"] = df["Passed in time"] == "Yes"
    df["is_placed"] = df["Status"] == PLACED
    df["is_higher"] = df["Status"] == HIGHER
    df["is_entrep"] = df["Status"] == ENTREP
    df["is_unplaced"] = df["Status"] == UNPLACED
    df.loc[~df["is_placed"], "Salary note"] = ""   # salary only matters for placed students

    same = df["is_higher"] & df["Admission year"].notna() & (df["Admission year"] == df["Graduation year"])
    df["Admitted in graduation year"] = ""
    df.loc[df["is_higher"], "Admitted in graduation year"] = "No"
    df.loc[same, "Admitted in graduation year"] = "Yes"
    df["is_higher_same_year"] = same

    # ---- duplicate detection
    # Same student = same ID (or name+department when no ID) AND same program,
    # so a student who did UG and later PG is not flagged.
    id_key = normalise_id(df["Student ID"])
    fallback = (df["Name"].str.lower().str.replace(r"\s+", " ", regex=True)
                + "|" + df["Department"].str.lower())
    base = id_key.where(id_key != "", fallback)
    key = base + "|" + df["Program"]
    key = key.where(base.str.strip("|") != "", "")  # nothing to match on -> unique
    df["dup_key"] = key

    sized = key.where(key != "")
    df["entries"] = sized.groupby(sized).transform("size").fillna(1).astype(int)
    df["is_dup"] = df["entries"] > 1
    order = sized.groupby(sized).cumcount(ascending=(keep == "first"))
    df["counted"] = (~df["is_dup"]) | (order == 0)
    df["Duplicate?"] = ""
    kept = "Yes - kept (latest)" if keep == "last" else "Yes - kept (first)"
    df.loc[df["is_dup"] & df["counted"], "Duplicate?"] = kept
    df.loc[df["is_dup"] & ~df["counted"], "Duplicate?"] = "Yes - ignored"
    return df


# ------------------------------------------------------------- selections
def select(df: pd.DataFrame, dept: str, prog: str, year) -> pd.DataFrame:
    v = df
    if dept != OVERALL:
        v = v[v["Department"] == dept]
    if prog == "UG only":
        v = v[v["Program"] == "UG"]
    elif prog == "PG only":
        v = v[v["Program"] == "PG"]
    if year:
        v = v[v["Graduation year"] == year]
    return v


def summarise(view: pd.DataFrame) -> dict:
    """view = counted rows only."""
    placed = view[view["is_placed"]]
    sal = placed["Salary"].dropna()
    return {
        "salary_unusable": int(placed["Salary"].isna().sum()),
        "salary_converted": int(placed["Salary note"].isin([N_CONV, N_LPA, N_MONTH]).sum()),
        "entered": len(view),
        "passed": int(view["is_passed"].sum()),
        "not_passed": int((view["Passed in time"] == "No").sum()),
        "placed": int(view["is_placed"].sum()),
        "higher": int(view["is_higher"].sum()),
        "higher_same_year": int(view["is_higher_same_year"].sum()),
        "higher_other": int((view["is_higher"] & ~view["is_higher_same_year"]).sum()),
        "entrep": int(view["is_entrep"].sum()),
        "unplaced": int(view["is_unplaced"].sum()),
        "unrecognised": int((view["Status"] == UNCLASSIFIED).sum()),
        "salary_count": int(len(sal)),
        "median_salary": float(sal.median()) if len(sal) else None,
    }


def lists(view: pd.DataFrame) -> dict:
    cols = LIST_COLUMNS
    no_sal = SALARY_EXTRA + ["Admission year", "Admitted in graduation year"]
    out = {
        "Passed": view[view["is_passed"]][cols].drop(columns=no_sal),
        "Placed": view[view["is_placed"]][cols]
        .drop(columns=["Admission year", "Admitted in graduation year"])
        .sort_values("Salary", ascending=False, na_position="last"),
        "Higher studies": view[view["is_higher"]][cols].drop(columns=SALARY_EXTRA)
        .sort_values("Admitted in graduation year", ascending=False),
        "Entrepreneurship": view[view["is_entrep"]][cols].drop(columns=no_sal),
        "Unplaced-Others": view[view["is_unplaced"]][cols].drop(columns=no_sal),
    }
    return {k: v.reset_index(drop=True) for k, v in out.items()}


def salary_check(view: pd.DataFrame) -> pd.DataFrame:
    """Placed students whose salary was converted, missing, unreadable or out of range."""
    p = view[view["is_placed"] & (view["Salary note"] != N_OK)].copy()
    p["Included in median"] = p["Salary"].notna().map({True: "Yes", False: "No"})
    cols = ["Name", "Student ID", "Department", "Program", "Graduation year",
            "Salary (as entered)", "Salary", "Salary note", "Included in median", "Sheet row"]
    return p[cols].rename(columns={"Salary": "Salary (interpreted)"}).reset_index(drop=True)


def dept_breakdown(view: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d, g in view.groupby("Department"):
        s = summarise(g)
        rows.append({
            "Department": d, "Entered": s["entered"], "Passed": s["passed"],
            PLACED: s["placed"], HIGHER: s["higher"],
            "Higher studies (same-year admission)": s["higher_same_year"],
            ENTREP: s["entrep"], UNPLACED: s["unplaced"],
            "Median salary": s["median_salary"],
        })
    return pd.DataFrame(rows)


def duplicate_table(all_rows: pd.DataFrame, view: pd.DataFrame) -> pd.DataFrame:
    """Every entry (kept + ignored) of any student whose counted entry is in view."""
    keys = view.loc[view["is_dup"], "dup_key"].unique()
    d = all_rows[all_rows["dup_key"].isin(keys)].sort_values(["dup_key", "Sheet row"])
    return d[LIST_COLUMNS].reset_index(drop=True)


# ------------------------------------------------------------------ excel
def to_excel(summary_rows: list, sheets: dict) -> bytes:
    """summary_rows = [(label, value)], sheets = {name: DataFrame}."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        wb = xw.book
        head = wb.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1})
        yellow = wb.add_format({"bg_color": "#FFF2A8"})
        red = wb.add_format({"bg_color": "#F8CBAD"})
        bold = wb.add_format({"bold": True})

        pd.DataFrame(summary_rows, columns=["Item", "Value"]).to_excel(
            xw, sheet_name="Summary", index=False)
        ws = xw.sheets["Summary"]
        ws.set_column(0, 0, 48)
        ws.set_column(1, 1, 24)

        for name, df in sheets.items():
            sheet = name[:31]
            df.to_excel(xw, sheet_name=sheet, index=False)
            ws = xw.sheets[sheet]
            for i, col in enumerate(df.columns):
                width = max([len(str(col))] + [len(str(x)) for x in df[col].head(300)]) + 2
                ws.set_column(i, i, min(width, 40))
                ws.write(0, i, col, head)
            ws.freeze_panes(1, 0)
            if "Duplicate?" in df.columns and len(df):
                c = df.columns.get_loc("Duplicate?")
                letter = chr(ord("A") + c)
                rng = f"A2:{chr(ord('A') + len(df.columns) - 1)}{len(df) + 1}"
                ws.conditional_format(rng, {"type": "formula",
                                            "criteria": f'=LEFT(${letter}2,13)="Yes - ignored"',
                                            "format": red})
                ws.conditional_format(rng, {"type": "formula",
                                            "criteria": f'=LEFT(${letter}2,3)="Yes"',
                                            "format": yellow})
            if sheet == "Placed" and "Salary" in df.columns:
                s = df["Salary"].dropna()
                s = s[s > 0]
                r = len(df) + 2
                ws.write(r, 0, "Median salary", bold)
                ws.write(r, df.columns.get_loc("Salary"), float(s.median()) if len(s) else "n/a", bold)
    return buf.getvalue()
