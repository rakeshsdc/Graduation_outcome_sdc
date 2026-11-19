import pandas as pd
import streamlit as st

import outcome_logic as L

# ------------------------------------------------------------------
# CONFIG: your Google Sheet (must be shared as "Anyone with the link: Viewer")
# ------------------------------------------------------------------
SHEET_ID = "1LNHrVooUxwzmO9lQ3w8tlbZTtHQbCbMXemk8qGcycLQ"
GID = "772688619"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

st.set_page_config(page_title="Student Outcome Dashboard", layout="wide")


@st.cache_data(ttl=60)  # re-reads the sheet at most once a minute
def load_data() -> pd.DataFrame:
    return pd.read_csv(CSV_URL, dtype=str).fillna("")


def letter_index(letter: str) -> int:
    """'A' -> 0, 'C' -> 2, 'U' -> 20 ..."""
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def guess(cols, keywords, fallback=0):
    for i, c in enumerate(cols):
        if any(k in c.lower() for k in keywords):
            return i
    return fallback


def label(cols, i):
    return cols[i] if i < len(cols) else cols[0]


def highlight(row):
    d = str(row.get("Duplicate?", ""))
    if d.startswith("Yes - ignored"):
        return ["background-color: #f8cbad"] * len(row)
    if d.startswith("Yes"):
        return ["background-color: #fff2a8"] * len(row)
    return [""] * len(row)


def show(df: pd.DataFrame):
    fmt = {"Salary": "{:,.0f}"} if "Salary" in df.columns else {}
    st.dataframe(df.style.apply(highlight, axis=1).format(fmt, na_rep=""),
                 use_container_width=True, hide_index=True)


st.title("🎓 Student Outcome Dashboard")
if st.button("🔄 Refresh data from Google Sheet"):
    load_data.clear()

try:
    raw = load_data()
except Exception as e:
    st.error(
        "Could not read the Google Sheet. In Google Sheets click Share → General access → "
        f"'Anyone with the link' → Viewer, then refresh.\n\nDetails: {e}"
    )
    st.stop()

cols = list(raw.columns)
NONE = "(none)"
opt = [NONE] + cols

# ------------------------------------------------------------------
# Sidebar: column mapping (defaults follow your sheet layout) + rules
# ------------------------------------------------------------------
with st.sidebar:
    st.header("Column mapping")
    st.caption("Defaults follow the sheet layout: C Department, D Program, E Year, "
               "F Passed in time, G Status, U Admission year. Change only if the sheet changes.")
    c_dept = st.selectbox("C – Department name", cols, index=letter_index("C") if len(cols) > 2 else 0)
    c_prog = st.selectbox("D – Program (UG/PG)", cols, index=letter_index("D") if len(cols) > 3 else 0)
    c_year = st.selectbox("E – Year of graduation", cols, index=letter_index("E") if len(cols) > 4 else 0)
    c_pass = st.selectbox("F – Passed within stipulated time (Yes/No)", cols,
                          index=letter_index("F") if len(cols) > 5 else 0)
    c_stat = st.selectbox("G – Status", cols, index=letter_index("G") if len(cols) > 6 else 0)
    c_adm = st.selectbox("U – Admission year (higher studies)", cols,
                         index=letter_index("U") if len(cols) > 20 else 0)
    st.markdown("**Other columns**")
    c_name = st.selectbox("Student name", opt,
                          index=1 + guess(cols, ["student name", "name of the student", "your name", "full name", "candidate"], 1)
                          if len(cols) > 1 else 0)
    id_keys = ["register", "reg no", "reg. no", "roll no", "roll number", "email", "e-mail"]
    id_hits = [i for i, c in enumerate(cols) if any(k in c.lower() for k in id_keys)]
    c_sid = st.selectbox("Register no. / Email – optional (used to find duplicates)", opt,
                         index=1 + id_hits[0] if id_hits else 0,
                         help="Leave as (none) to treat entries with the same name + department + program "
                              "as the same student. Pick a column only if it holds a truly unique ID per student.")
    c_sal = st.selectbox("M – Annual CTC (₹ per year)", opt, index=1 + letter_index("M") if len(cols) > 12 else 0)
    c_enr = st.selectbox("Q – Program enrolled after UG/PG", opt, index=1 + letter_index("Q") if len(cols) > 16 else 0)

    st.header("Rules")
    keep_label = st.radio("When a student has several entries, count the", ["latest entry", "first entry"])

mapping = {
    "dept": c_dept, "prog": c_prog, "year": c_year, "passed": c_pass, "status": c_stat, "adm": c_adm,
    "name": None if c_name == NONE else c_name,
    "sid": None if c_sid == NONE else c_sid,
    "salary": None if c_sal == NONE else c_sal,
    "enrolled": None if c_enr == NONE else c_enr,
}
frame = L.build_frame(raw, mapping, keep="last" if keep_label.startswith("latest") else "first")
counted = frame[frame["counted"]]

# ------------------------------------------------------------------
# Selections
# ------------------------------------------------------------------
f1, f2, f3 = st.columns(3)
depts = [L.OVERALL] + sorted(d for d in counted["Department"].unique() if d and not L.is_pg_department(d))
dept_sel = f1.selectbox("Department", depts)
prog_sel = f2.selectbox("Program", ["UG only", "PG only", "UG and PG"])
years = sorted(counted["Graduation year"].dropna().unique(), reverse=True)
if not years:
    st.warning("No graduation years found. Check the 'E – Year of graduation' column mapping.")
    st.stop()
year_sel = f3.selectbox("Year of graduation", years)

view = L.select(counted, dept_sel, prog_sel, year_sel)
s = L.summarise(view)
lst = L.lists(view)
dups = L.duplicate_table(frame, view)
sal_chk = L.salary_check(view)
n_ignored = int((dups["Duplicate?"] == "Yes - ignored").sum()) if len(dups) else 0

st.subheader(f"{dept_sel} | {prog_sel} | Graduation year {year_sel}")
if prog_sel == "UG only":
    st.caption("“Passed” = passed UG within 3 years (column F = Yes).")
elif prog_sel == "PG only":
    st.caption("“Passed” = passed PG within 2 years (column F = Yes).")
else:
    st.caption("“Passed” = UG students who passed within 3 years + PG students who passed within 2 years (column F = Yes).")

if mapping["sid"] and L.id_conflicts(frame):
    st.error(f"The column '{mapping['sid']}' looks wrong as a student ID: {L.id_conflicts(frame)} groups share the "
             "same value but have different student names, so different students may be treated as duplicates. "
             "Set it to (none) in the sidebar unless it is a real register number / email.")
if n_ignored:
    st.warning(f"{n_ignored} duplicate entr{'y' if n_ignored == 1 else 'ies'} found and excluded from the counts "
               f"(only the {keep_label} of each student is counted). See the 'Duplicates' tab.")
unknown_prog = int((counted["Program"] == "").sum())
if unknown_prog and prog_sel != "UG and PG":
    st.info(f"{unknown_prog} entries have a Program value that is neither UG nor PG, so they are left out "
            "of 'UG only' / 'PG only' (they appear under 'UG and PG').")
if s["salary_unusable"] or s["salary_converted"]:
    st.warning(
        f"Salary: {s['salary_converted']} placed student(s) entered salary in words/units/monthly and it was "
        f"converted to yearly rupees; {s['salary_unusable']} could not be used (missing, unreadable or out of range) "
        f"and are left out of the median. See the 'Salary check' tab.")
if s["unrecognised"]:
    st.info(f"{s['unrecognised']} entries in this selection have a Status that matches none of the four options "
            "(they are counted in 'entered' but not in any status). See 'All entries'.")

# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------
m = st.columns(4)
m[0].metric("Students entered data", s["entered"])
m[1].metric("Students passed (in time)", s["passed"], help=f"Not passed in time: {s['not_passed']}")
m[2].metric("Placed / Employed", s["placed"])
m[3].metric("Median salary", f"{s['median_salary']:,.0f}" if s["median_salary"] is not None else "No salary data",
            help=f"Median of {s['salary_count']} placed students who entered a salary (of {s['placed']} placed).")

m2 = st.columns(4)
m2[0].metric("Higher studies", s["higher"])
m2[1].metric("↳ admitted in graduation year", s["higher_same_year"],
             help="Higher studies AND admission year (column U) equals graduation year.")
m2[2].metric("Entrepreneurship / Self-employed", s["entrep"])
m2[3].metric("Unplaced / Preparing / Others", s["unplaced"])

if dept_sel == L.OVERALL:
    st.markdown("**Department-wise breakdown**")
    bd = L.dept_breakdown(view)
    st.dataframe(bd.style.format({"Median salary": "{:,.0f}"}, na_rep=""),
                 use_container_width=True, hide_index=True)
else:
    bd = pd.DataFrame()

# ------------------------------------------------------------------
# Lists
# ------------------------------------------------------------------
st.caption("🟨 student has more than one entry (this one is counted)   🟥 older duplicate entry (not counted)")
tab_names = ["Passed", "Placed", "Higher studies", "Entrepreneurship", "Unplaced-Others"]
tabs = st.tabs([f"{n} ({len(lst[n])})" for n in tab_names]
               + [f"Salary check ({len(sal_chk)})", f"Duplicates ({len(dups)})", f"All entries ({len(view)})"])
for tab, n in zip(tabs, tab_names):
    with tab:
        if n == "Placed":
            st.markdown(f"**Median salary: {s['median_salary']:,.0f}**" if s["median_salary"] is not None
                        else "**Median salary: no data**")
        if n == "Higher studies":
            st.markdown(f"**Admitted in graduation year: {s['higher_same_year']}** · "
                        f"Other years: {s['higher_other']}")
        show(lst[n])
with tabs[5]:
    st.caption("Placed students whose salary was not plain digits. Rules: words/units (e.g. 'four lakhs', "
               "'4.5 LPA', 'Rs. 4,30,000') are converted; monthly amounts are x12; a bare number below 100 is "
               "taken as LPA; yearly values below ₹1,00,000 or above ₹5 crore, blanks and unreadable text are "
               "excluded from the median. Please verify with the student.")
    if len(sal_chk):
        st.dataframe(sal_chk.style.format({"Salary (interpreted)": "{:,.0f}"}, na_rep=""),
                     use_container_width=True, hide_index=True)
    else:
        st.success("All placed students entered a valid salary in digits.")
with tabs[6]:
    if len(dups):
        show(dups)
    else:
        st.success("No duplicate entries for this selection.")
with tabs[7]:
    show(view[L.list_cols(view)].reset_index(drop=True))

# ------------------------------------------------------------------
# Excel download
# ------------------------------------------------------------------
summary_rows = [
    ("Department", dept_sel), ("Program", prog_sel), ("Year of graduation", year_sel),
    ("Students entered data", s["entered"]),
    ("Passed within stipulated time (UG 3 yrs / PG 2 yrs)", s["passed"]),
    ("Not passed within stipulated time", s["not_passed"]),
    (L.PLACED, s["placed"]),
    ("Placed students with salary entered", s["salary_count"]),
    ("Median salary", s["median_salary"] if s["median_salary"] is not None else "n/a"),
    ("Salaries converted from words/units/monthly", s["salary_converted"]),
    ("Placed students with unusable salary (excluded)", s["salary_unusable"]),
    (L.HIGHER, s["higher"]),
    ("  - admitted in graduation year", s["higher_same_year"]),
    ("  - admitted in other years", s["higher_other"]),
    (L.ENTREP, s["entrep"]),
    (L.UNPLACED, s["unplaced"]),
    ("Status not recognised", s["unrecognised"]),
    ("Duplicate entries excluded", n_ignored),
    ("Duplicate rule", f"count the {keep_label} of each student"),
]
sheets = {
    "Passed": lst["Passed"], "Placed": lst["Placed"], "Higher studies": lst["Higher studies"],
    "Entrepreneurship": lst["Entrepreneurship"], "Unplaced-Others": lst["Unplaced-Others"],
    "Salary check": sal_chk, "Duplicates": dups, "All entries": view[L.list_cols(view)].reset_index(drop=True),
}
if len(bd):
    sheets = {"Dept-wise": bd, **sheets}

safe = lambda x: "".join(ch if ch.isalnum() else "_" for ch in str(x))[:30]
st.download_button(
    "⬇️ Download Excel report",
    data=L.to_excel(summary_rows, sheets),
    file_name=f"outcomes_{safe(dept_sel)}_{safe(prog_sel)}_{year_sel}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
