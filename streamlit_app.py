import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import os

# ---------------------------
# Database setup
# ---------------------------
DB_DIR = "data"
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "rota_builder.db")

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS availability (
    name TEXT,
    date TEXT,
    shift TEXT,
    unavailable INTEGER,
    UNIQUE(name, date, shift)
)
""")
conn.commit()

# ---------------------------
# Shifts & helpers
# ---------------------------
SHIFTS = [
    "8-16 Early",
    "8-16 ERA",
    "10-18",
    "14-22 (1)",
    "14-22 (2)",
    "16-23 ERA",
    "18-00 OnCall",
    "Admin"
]

def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)

# ---------------------------
# Sidebar login
# ---------------------------
st.sidebar.title("Consultant Login")
name = st.sidebar.text_input("Your name")
email = st.sidebar.text_input("Email")

if not name:
    st.info("👈 Please enter your name in the sidebar to continue")
    st.stop()

# ---------------------------
# Admin sets rota period
# ---------------------------
st.sidebar.markdown("---")
is_admin = st.sidebar.checkbox("I am admin")

if is_admin:
    st.sidebar.subheader("Set rota period")
    start = st.sidebar.date_input("Start date", datetime.today())
    end = st.sidebar.date_input("End date", datetime.today() + timedelta(days=13))

    if start > end:
        st.sidebar.error("Start date must be before end date")
    else:
        st.session_state["rota_period"] = (start, end)

if "rota_period" not in st.session_state:
    st.warning("Admin has not set a rota period yet.")
    st.stop()

start_date, end_date = st.session_state["rota_period"]

# ---------------------------
# Consultant availability
# ---------------------------
st.header(f"Availability for {name}")
st.write(f"Rota period: {start_date} → {end_date}")

for day in daterange(start_date, end_date):
    if day.weekday() >= 5:  # Saturday/Sunday
        st.markdown(f"**{day.strftime('%A %d %b %Y')}** – Weekend (no shifts)")
        continue

    st.markdown(f"**{day.strftime('%A %d %b %Y')}**")
    for shift in SHIFTS:
        key = f"{name}_{day}_{shift}"
        unavailable = st.checkbox(
            f"Unavailable for {shift}",
            key=key
        )
        c.execute("""
            INSERT OR REPLACE INTO availability (name, date, shift, unavailable)
            VALUES (?, ?, ?, ?)
        """, (name, str(day), shift, int(unavailable)))
    conn.commit()

st.success("Your availability has been saved ✅")

# ---------------------------
# Admin rota builder
# ---------------------------
if is_admin:
    st.header("Admin – View Raw Availability Data")
    df = pd.read_sql("SELECT * FROM availability", conn)
    st.dataframe(df)

    # (placeholder for rota-building algorithm)
    st.info("Rota builder not implemented yet – raw data shown above.")
