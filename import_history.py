"""Import historical data from all 11 team sheets into SQLite database."""
import sqlite3
import httpx
import csv
import io
import time
import os

DB_PATH = os.environ.get("DATA_DIR", "data") + "/ecoteam.db"

TEAMS = {
    "Deelat": "19w3gqsL7vNh_XyBuBf-ZMhvEBFWiQGSV0yY2BBzOYxM",
    "Aswaq": "1OiBgM6b_Y8bcrlhRsdC3o2lelZL3kz-aIeP7Pnnfnyo",
    "Flash": "1AcsEcnhgPnvrWJJXu7sEvWiHD38szgJhTrjtA-lmXjs",
    "Meeven": "13SYsxvgLVDkVlZ1y1UnwngDVxn6wr91xwI_9eG3FZE4",
    "Khosomaat": "1kEo6lwJvlzE1EB24Qu763xVOMphAOytlqFVZ4vTBut8",
    "Fordeal": "1ckXTIE5P0POiOmeDSnGHPlJqHOF9a9LiGOLwu8XHMxo",
    "Click Cart": "1C1TodG0bEXB_xgAyqtgFMaXilOdUD24vMbjx690Qipo",
    "Matajer": "1Dr4JirGRML_R1APFt6yIky0QRQgLxnm3fMrLthzGmqU",
    "Bazaar": "1HhLDRdP_CU0S335022XzkZIS5p2F_SMK4ogsoAWPxvI",
    "Minimarket": "18ax0CSvFlID7Iy885szdGwm7fe2HCV07uZXSFjneYp4",
    "Blinken": "1kd7ckJB46dDG99wn8XAyhxqnZuydC0OVcWHrjPeHHcw",
}

TAB = "March-2026"


def safe_float(v):
    if not v or v == "-" or str(v).startswith("#"):
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except Exception:
        return 0.0


def fetch_sheet(sheet_id, tab):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={tab}"
    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"  Error fetching: {e}")
    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    total_rows = 0

    for team, sheet_id in TEAMS.items():
        print(f"Reading {team}...")
        csv_text = fetch_sheet(sheet_id, TAB)
        if not csv_text:
            print(f"  FAILED to fetch {team}")
            continue

        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)

        # Find header row (contains "Date")
        header_idx = -1
        for i, row in enumerate(rows):
            if row and row[0].strip().lower() == "date":
                header_idx = i
                break

        if header_idx == -1:
            print(f"  No header found for {team}")
            continue

        team_rows = 0
        prev_spend = 0

        for row in rows[header_idx + 1:]:
            if not row or not row[0] or not row[0].strip():
                continue

            date_val = row[0].strip()
            if not any(ch.isdigit() for ch in date_val):
                continue

            spend = safe_float(row[1]) if len(row) > 1 else 0
            new_orders = safe_float(row[2]) if len(row) > 2 else 0
            yesterday_new = safe_float(row[3]) if len(row) > 3 else 0
            delivered = safe_float(row[4]) if len(row) > 4 else 0
            cancel = safe_float(row[5]) if len(row) > 5 else 0
            hold = safe_float(row[6]) if len(row) > 6 else 0

            cpo = spend / new_orders if new_orders > 0 else 0
            cpa = prev_spend / delivered if delivered > 0 and prev_spend > 0 else 0
            cancel_rate = (cancel / yesterday_new * 100) if yesterday_new > 0 else 0

            try:
                c.execute(
                    "INSERT OR REPLACE INTO daily_performance "
                    "(date, team, spend, new_orders, delivered, cancel, hold, cpo, cpa, cancel_rate, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (date_val, team, spend, new_orders, delivered, cancel, hold,
                     round(cpo, 1), round(cpa, 1), round(cancel_rate, 1), "sheet_import"),
                )
                team_rows += 1
            except Exception as e:
                print(f"  Insert error: {e}")

            prev_spend = spend

        print(f"  {team}: {team_rows} days imported")
        total_rows += team_rows
        time.sleep(1)

    conn.commit()

    # Show summary
    c.execute("SELECT team, COUNT(*) as days, ROUND(AVG(cpo),1) as avg_cpo, ROUND(AVG(cpa),1) as avg_cpa FROM daily_performance GROUP BY team ORDER BY avg_cpo")
    print("\n=== Summary ===")
    print(f"{'Team':<15} {'Days':<6} {'Avg CPO':<10} {'Avg CPA':<10}")
    print("-" * 41)
    for row in c.fetchall():
        print(f"{row[0]:<15} {row[1]:<6} {row[2]:<10} {row[3]:<10}")

    c.execute("SELECT COUNT(*) FROM daily_performance")
    total = c.fetchone()[0]
    print(f"\nTotal: {total} rows for {len(TEAMS)} teams")

    conn.close()


if __name__ == "__main__":
    main()
