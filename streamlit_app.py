import os
import io
import sqlite3
from collections import defaultdict
from datetime import datetime, date, timedelta, time
from typing import Tuple

import pandas as pd
import streamlit as st

# =========================
# SETUP: storage & secrets
# =========================
DB_DIR = "data"
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "rota_builder.db")

# Secrets (Streamlit Cloud → Settings → Secrets)
# [users]
# admin = ["your.email@nhs.net"]
# consultants = ["c1@nhs.net","c2@nhs.net"]
try:
    ADMIN_EMAILS = set(st.secrets["users"]["admin"])
    CONSULTANT_EMAILS = set(st.secrets["users"]["consultants"])
except Exception:
    st.error("❌ Secrets not set. Add admin/consultant emails under Settings → Secrets.")
    st.stop()

ALL_USERS = sorted(ADMIN_EMAILS | CONSULTANT_EMAILS)

# =========================
# SHIFTS (weekday pack)
# =========================
# label -> (start_time, end_time). End may cross midnight.
SHIFT_DEFS = {
    "8-16 Early":   (time(8, 0),  time(16, 0)),
    "8-16 ERA":     (time(8, 0),  time(16, 0)),
    "10-18":        (time(10, 0), time(18, 0)),
    "14-22 (1)":    (time(14, 0), time(22, 0)),
    "14-22 (2)":    (time(14, 0), time(22, 0)),
    "16-23 ERA":    (time(16, 0), time(23, 0)),
    "18-00 OnCall": (time(18, 0), time(0, 0)),  # crosses midnight
    "Admin":        (time(9, 0),  time(17, 0)), # 09:00–17:00 (changed as requested)
}
SHIFTS = list(SHIFT_DEFS.keys())
MIN_REST_HOURS = 11  # strict rule

# =========================
# DB: tables
# =========================
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS availability (
    name TEXT,
    email TEXT,
    day TEXT,          -- ISO date (YYYY-MM-DD)
    shift TEXT,        -- one of SHIFTS
    unavailable INTEGER,
    UNIQUE(email, day, shift)
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS assignments (
    day TEXT,          -- ISO date
    shift TEXT,
    name TEXT,
    email TEXT
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS quotas (
    email TEXT,
    shift TEXT,
    target INTEGER,
    UNIQUE(email, shift)
)
""")
conn.commit()

def ensure_default_quotas():
    for em in ALL_USERS:
        for sh in SHIFTS:
            c.execute("INSERT OR IGNORE INTO quotas (email, shift, target) VALUES (?, ?, ?)",
                      (em, sh, 0))
    conn.commit()

ensure_default_quotas()

# =========================
# HELPERS
# =========================
def daterange(d0: date, d1: date):
    d = d0
    while d <= d1:
        yield d
        d += timedelta(days=1)

def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Sat/Sun

def dt_for(day: date, t: time) -> datetime:
    return datetime.combine(day, t)

def shift_window(day: date, shift_label: str) -> Tuple[datetime, datetime]:
    start_t, end_t = SHIFT_DEFS[shift_label]
    start_dt = dt_for(day, start_t)
    end_dt = dt_for(day, end_t)
    if end_t <= start_t and shift_label != "Admin":
        end_dt += timedelta(days=1)  # crosses midnight
    return start_dt, end_dt

def hours_between(a_end: datetime, b_start: datetime) -> float:
    return (b_start - a_end).total_seconds() / 3600.0

# =========================
# SIDEBAR LOGIN
# =========================
st.sidebar.title("Consultant Login")
your_name = st.sidebar.text_input("Your name")
your_email = st.sidebar.text_input("Email")

if not your_email:
    st.info("👈 Enter your email to continue")
    st.stop()

is_admin = your_email in ADMIN_EMAILS
is_consultant = (your_email in CONSULTANT_EMAILS) or is_admin
if not is_consultant:
    st.error("❌ This email is not authorised.")
    st.stop()

if not your_name:
    st.info("👈 Enter your name to continue")
    st.stop()

# =========================
# PERIOD (admin sets, all use)
# =========================
st.sidebar.markdown("---")
if is_admin:
    st.sidebar.subheader("Set rota period")
    start_input = st.sidebar.date_input("Start date", date.today())
    end_input = st.sidebar.date_input("End date", date.today() + timedelta(days=13))
    if start_input > end_input:
        st.sidebar.error("Start must be ≤ End")
    else:
        st.session_state["period"] = (start_input, end_input)

if "period" not in st.session_state:
    st.warning("Admin has not set the rota period yet.")
    st.stop()

period_start, period_end = st.session_state["period"]

st.title("ED Rota Builder")
st.caption(f"Period: {period_start.isoformat()} → {period_end.isoformat()} (weekdays only)")

# =========================
# CONSULTANT AVAILABILITY (UNavailability)
# =========================
st.header(f"Your availability – {your_name}")
for d in daterange(period_start, period_end):
    if is_weekend(d):
        st.markdown(f"**{d.strftime('%A %d %b %Y')}** – Weekend (no shifts)")
        continue

    st.markdown(f"**{d.strftime('%A %d %b %Y')}**")
    for sh in SHIFTS:
        row = c.execute(
            "SELECT unavailable FROM availability WHERE email=? AND day=? AND shift=?",
            (your_email, d.isoformat(), sh)
        ).fetchone()
        default = bool(row[0]) if row else False
        val = st.checkbox(f"Unavailable for {sh}", value=default, key=f"{your_email}_{d}_{sh}")
        c.execute(
            "INSERT OR REPLACE INTO availability (name,email,day,shift,unavailable) VALUES (?,?,?,?,?)",
            (your_name, your_email, d.isoformat(), sh, int(val))
        )
    conn.commit()

st.success("Saved your (un)availability ✅")

# =========================
# ADMIN TOOLS: TARGETS (per-person per-shift)
# =========================
st.markdown("---")
if is_admin:
    st.header("Admin – Per-person per-shift targets (set BEFORE generation)")
    q = pd.read_sql("SELECT email, shift, target FROM quotas", conn)
    q = q[q["email"].isin(ALL_USERS)].copy()
    q = q.pivot(index="email", columns="shift", values="target").reindex(ALL_USERS).reindex(columns=SHIFTS)
    q = q.fillna(0).astype(int)

    st.markdown("Enter target counts for each person and shift across the selected period.")
    edited_q = st.data_editor(q, use_container_width=True, num_rows="fixed", key="quota_editor")

    col_save, col_note = st.columns([1,3])
    with col_save:
        if st.button("💾 Save targets", use_container_width=True):
            melt = edited_q.reset_index().melt(id_vars=["email"], var_name="shift", value_name="target")
            for _, r in melt.iterrows():
                c.execute("INSERT OR REPLACE INTO quotas (email, shift, target) VALUES (?,?,?)",
                          (r["email"], r["shift"], int(r["target"])))
            conn.commit()
            st.success("Targets saved.")

    with col_note:
        st.caption("These targets are used during generation. Admins also appear as consultants (to include your own shifts).")

# =========================
# GENERATION (STRICT rules)
# =========================
def generate_rota_strict():
    # Clear existing assignments in this period
    c.execute("DELETE FROM assignments WHERE day BETWEEN ? AND ?", (period_start.isoformat(), period_end.isoformat()))
    conn.commit()

    # Load availability & quotas
    avail = pd.read_sql("""
        SELECT name,email,day,shift,unavailable
        FROM availability
        WHERE day BETWEEN ? AND ?
    """, conn, params=(period_start.isoformat(), period_end.isoformat()))
    quotas = pd.read_sql("SELECT email, shift, target FROM quotas", conn)

    # Availability map (who IS available)
    is_available = defaultdict(set)   # (day, shift) -> set(emails)
    name_map = {}
    for _, r in avail.iterrows():
        name_map[r["email"]] = r["name"]
        if int(r["unavailable"]) == 0:
            is_available[(r["day"], r["shift"])].add(r["email"])
    for em in ALL_USERS:
        name_map.setdefault(em, em)

    # Targets
    target_by_shift = {(row.email, row.shift): int(row.target) for row in quotas.itertuples()}
    # Total target per person = sum of their per-shift targets
    total_target = quotas.groupby("email")["target"].sum().to_dict()

    # Track counts
    done_per_shift = defaultdict(int)  # (email, shift) -> count
    done_total = defaultdict(int)      # email -> total assigned
    last_end_dt = {}                   # email -> last assignment end (datetime)
    warnings = []

    # Helper fns
    def shift_window(day: date, shift_label: str):
        start_t, end_t = SHIFT_DEFS[shift_label]
        start_dt = datetime.combine(day, start_t)
        end_dt = datetime.combine(day, end_t)
        if end_t <= start_t and shift_label != "Admin":
            end_dt += timedelta(days=1)  # crosses midnight
        return start_dt, end_dt

    def hours_between(a_end: datetime, b_start: datetime) -> float:
        return (b_start - a_end).total_seconds() / 3600.0

    # Iterate all weekdays/shifts; STRICT: 1 shift/day; ≥11h rest; DO NOT exceed targets
    for d in daterange(period_start, period_end):
        if is_weekend(d):
            continue
        day_iso = d.isoformat()
        already_today = set()

        for sh in SHIFTS:
            need = 1  # one person per shift per weekday
            assigned_here = 0
            start_dt, end_dt = shift_window(d, sh)

            # Candidates: authorised & available & still have remaining SHIFT and TOTAL targets
            cands = []
            for em in is_available.get((day_iso, sh), []):
                if em not in ALL_USERS:
                    continue
                # remaining shift & total
                rem_shift = target_by_shift.get((em, sh), 0) - done_per_shift[(em, sh)]
                rem_total = total_target.get(em, 0) - done_total[em]
                if rem_shift <= 0 or rem_total <= 0:
                    continue  # can't give this person more of this shift or any shift
                # one shift per day
                if em in already_today:
                    continue
                # rest rule
                if em in last_end_dt and hours_between(last_end_dt[em], start_dt) < MIN_REST_HOURS:
                    continue
                cands.append(em)

            # Rank by: more remaining shift target first, then fewer total assigned
            def rem_shift_count(em):
                return target_by_shift.get((em, sh), 0) - done_per_shift[(em, sh)]
            cands_sorted = sorted(cands, key=lambda e: (-rem_shift_count(e), done_total[e]))

            picked = cands_sorted[0] if cands_sorted else None

            if picked is None:
                # STRICT: leave unfilled if nobody fits all constraints and targets
                warnings.append(f"{day_iso} {sh}: unfilled (targets/constraints blocked assignment).")
            else:
                c.execute(
                    "INSERT INTO assignments (day, shift, name, email) VALUES (?,?,?,?)",
                    (day_iso, sh, name_map.get(picked, picked), picked)
                )
                done_total[picked] += 1
                done_per_shift[(picked, sh)] += 1
                last_end_dt[picked] = end_dt
                already_today.add(picked)
                assigned_here += 1

            if assigned_here < need:
                # logged above
                pass

    conn.commit()
    return warnings

# =========================
# BUTTON + EXCEL EXPORT  (ADMIN ONLY)
# =========================
if is_admin:
    st.markdown("---")
    gen_col, info_col = st.columns([1, 2])
    with gen_col:
        click_generate = st.button("🧮 Generate rota (strict) & download Excel", use_container_width=True)
    with info_col:
        st.caption("Enforces **1 shift/day** and **≥11h rest**. Uses saved **per-person per-shift targets**. Leaves slots unfilled (with warnings) if rules block coverage.")

    if click_generate:
        warnings = generate_rota_strict()

        rota_df = pd.read_sql(
            "SELECT day, shift, name, email FROM assignments WHERE day BETWEEN ? AND ? ORDER BY day, shift",
            conn, params=(period_start.isoformat(), period_end.isoformat())
        )

        # Build Excel
        import io
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            grid = rota_df.pivot_table(index="day", columns="shift", values="name",
                                       aggfunc=lambda x: ", ".join(sorted(set(x)))).reset_index()
            grid.to_excel(writer, sheet_name="Rota", index=False)
            rota_df.to_excel(writer, sheet_name="Assignments (raw)", index=False)

            q_now = pd.read_sql("SELECT email, shift, target FROM quotas", conn)
            q_piv = q_now.pivot(index="email", columns="shift", values="target").reindex(ALL_USERS).reindex(columns=SHIFTS)
            q_piv.to_excel(writer, sheet_name="Targets", index=True)

            if not rota_df.empty:
                counts = rota_df.groupby(["email", "shift"]).size().reset_index(name="assigned")
                merged = q_now.merge(counts, on=["email", "shift"], how="left").fillna({"assigned": 0})
                merged["unmet_target"] = (merged["target"] - merged["assigned"]).clip(lower=0)
                merged.sort_values(["email", "shift"], inplace=True)
                merged.to_excel(writer, sheet_name="Targets vs Assigned", index=False)

        excel_bytes = output.getvalue()
        fname = f"rota_{period_start.isoformat()}_{period_end.isoformat()}_STRICT.xlsx"
        st.download_button(
            label="⬇️ Download Excel rota",
            data=excel_bytes,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

        if warnings:
            with st.expander("Warnings (strict rules prevented full coverage) — open to review"):
                for w in warnings:
                    st.write("⚠️", w)

# =========================
# PREVIEW + DEBUG
# =========================
st.header("Draft rota (preview)")
prev = pd.read_sql(
    "SELECT day, shift, name FROM assignments WHERE day BETWEEN ? AND ? ORDER BY day, shift",
    conn, params=(period_start.isoformat(), period_end.isoformat())
)
if prev.empty:
    st.info("No assignments yet.")
else:
    grid_prev = prev.pivot_table(index="day", columns="shift", values="name",
                                 aggfunc=lambda x: ", ".join(sorted(set(x)))).reset_index()
    st.dataframe(grid_prev, use_container_width=True)

if is_admin:
    st.markdown("---")
    st.subheader("Raw availability (debug)")
    raw = pd.read_sql(
        "SELECT name,email,day,shift,unavailable FROM availability WHERE day BETWEEN ? AND ? ORDER BY day, shift, email",
        conn, params=(period_start.isoformat(), period_end.isoformat())
    )
    st.dataframe(raw, use_container_width=True)


