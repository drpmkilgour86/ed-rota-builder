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
    email TEXT,
    date TEXT,
    shift TEXT,
    unavailable INTEGER,
    UNIQUE(name, email, date, shift)
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

# Get roles from secrets
try:
    admin_emails = st.secrets["users"]["admin"]
    consultant_emails = st.secrets["users"]["consultants"]
except Exception as e:
    st.error("❌ Secrets not set up. Please add admin and consultant emails in app settings.")
    st.stop()

if not email:
    st.info("👈 Please enter your email in the sidebar to continue")
    st.stop()

# Role detection
is_admin = email in admin_emails
is_consultant = email in consultant_emails or is_admin  # Admin is also a consultant

if not is_consultant and not is_admin:
    st.error("❌ This email is not authorised to use the app.")
    st.stop()

# ---------------------------
# Admin sets rota period
# ---------------------------
if is_admin:
    st.sidebar.markdown("---")
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
        key = f"{email}_{day}_{shift}"
        unavailable = st.checkbox(
            f"Unavailable for {shift}",
            key=key
        )
        c.execute("""
            INSERT OR REPLACE INTO availability (name, email, date, shift, unavailable)
            VALUES (?, ?, ?, ?, ?)
        """, (name, email, str(day), shift, int(unavailable)))
    conn.commit()

st.success("Your availability has been saved ✅")

# ---------------------------
# Admin rota builder
# ---------------------------
if is_admin:
    st.header("Admin – View Raw Availability Data")
    df = pd.read_sql("SELECT * FROM availability", conn)
    st.dataframe(df)

    # Placeholder for rota algorithm
    st.info("⚙️ Rota builder not implemented yet – raw data shown above.")
