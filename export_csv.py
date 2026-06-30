#!/usr/bin/env python3
"""
export_csv.py
DBからCSVを書き出すスクリプト。GitHubにプッシュすることで
Streamlit Cloud上のアプリがDBなしで動作できるようにする。

使い方:
  python export_csv.py                  # 当日・自動判定
  python export_csv.py 20260628 05      # 日付・場コードを指定
  python export_csv.py 20260628 05 06   # 複数場コード指定

毎週:
  土曜朝: python export_csv.py  → history + entries
  日曜朝: python export_csv.py  → history更新 + entries
"""

import os
import sys
import psycopg2
import pandas as pd
from datetime import datetime, timedelta

# ── DB接続設定（app_sql.py と同じ） ────────────────────────────
DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "keiba",
    "user":     "postgres",
    "password": "jiro4211",
}

# ── 出力フォルダ ────────────────────────────────────────────────
OUTPUT_DIR = "data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 競馬場コード ────────────────────────────────────────────────
JRA_VENUES = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04",
    "東京": "05", "中山": "06", "中京": "07", "京都": "08",
    "阪神": "09", "小倉": "10",
}

def get_conn():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding('SJIS')
    return conn

def run_query(sql, params=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
        cur.close()
        return df
    finally:
        conn.close()

# ────────────────────────────────────────────────────────────────
# 1. history.csv: 直近2週間の過去レース結果
# ────────────────────────────────────────────────────────────────
def export_history():
    print("⏳ history.csv エクスポート中...")
    two_weeks_ago = (datetime.today() - timedelta(days=14)).strftime("%Y%m%d")
    nen  = two_weeks_ago[:4]
    gappi = two_weeks_ago[4:]

    query = """
        SELECT
            u.race_code,
            (r.kaisai_nen || r.kaisai_gappi)           AS "日付raw",
            r.keibajo_code                             AS "場コード",
            r.race_bango                               AS "Ｒ",
            u.umaban                                   AS "馬番",
            u.bamei                                    AS "馬名",
            u.barei                                    AS "年齢",
            u.kakutei_chakujun                         AS "着順",
            u.kishumei_ryakusho                        AS "騎手",
            u.chokyoshimei_ryakusho                    AS "調教師",
            u.tansho_odds                              AS "オッズ",
            u.tansho_ninkijun                          AS "人気",
            u.time_sa                                  AS "着差",
            u.kohan_3f                                 AS "上がり3F",
            u.kyakushitsu_hantei                       AS "脚質",
            r.kyoso_joken_code_saijakunen              AS "クラス名"
        FROM umagoto_race_joho u
        JOIN race_shosai r ON u.race_code = r.race_code
        WHERE r.kaisai_nen >= %(nen)s
          AND r.kaisai_gappi >= %(gappi)s
        ORDER BY r.kaisai_nen, r.kaisai_gappi, r.race_bango::int, u.umaban::int
    """
    df = run_query(query, {"nen": nen, "gappi": gappi})
    out = os.path.join(OUTPUT_DIR, "history.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  ✅ {out} → {len(df)}行")

# ────────────────────────────────────────────────────────────────
# 2. entries_YYYYMMDD_VV.csv: 指定日・場の出走馬
# ────────────────────────────────────────────────────────────────
def export_entries(date_str, venue_code):
    print(f"⏳ entries_{date_str}_{venue_code}.csv エクスポート中...")
    nen   = date_str[:4]
    gappi = date_str[4:]

    query = """
        SELECT
            r.race_code,
            r.race_bango                               AS "Ｒ",
            r.keibajo_code                             AS "場コード",
            r.kyori                                    AS "距離",
            r.track_code                               AS "コース",
            r.shusso_tosu                              AS "頭数",
            r.kyoso_joken_code_saijakunen              AS "クラス名",
            u.umaban                                   AS "馬番",
            u.wakuban                                  AS "枠番",
            u.ketto_toroku_bango,
            u.bamei                                    AS "馬名",
            u.barei                                    AS "年齢",
            u.seibetsu_code                            AS "性別",
            u.kishumei_ryakusho                        AS "騎手",
            u.chokyoshimei_ryakusho                    AS "調教師",
            u.banushimei_hojinkaku_nashi               AS "馬主(最新/仮想)",
            u.futan_juryo                              AS "斤量",
            u.tansho_odds                              AS "オッズ",
            u.tansho_ninkijun                          AS "人気"
        FROM race_shosai r
        JOIN umagoto_race_joho u ON r.race_code = u.race_code
        WHERE r.kaisai_nen   = %(nen)s
          AND r.kaisai_gappi = %(gappi)s
          AND r.keibajo_code = %(venue)s
        ORDER BY r.race_bango::int, u.umaban::int
    """
    df = run_query(query, {"nen": nen, "gappi": gappi, "venue": venue_code})
    if df.empty:
        print(f"  ⚠️ データなし: {date_str} 場コード={venue_code}")
        return
    out = os.path.join(OUTPUT_DIR, f"entries_{date_str}_{venue_code}.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  ✅ {out} → {len(df)}行")

# ────────────────────────────────────────────────────────────────
# 3. 当日開催場を自動検出してエクスポート
# ────────────────────────────────────────────────────────────────
def detect_today_venues(date_str):
    nen   = date_str[:4]
    gappi = date_str[4:]
    query = """
        SELECT DISTINCT keibajo_code
        FROM race_shosai
        WHERE kaisai_nen = %(nen)s AND kaisai_gappi = %(gappi)s
        ORDER BY keibajo_code
    """
    df = run_query(query, {"nen": nen, "gappi": gappi})
    return df["keibajo_code"].tolist() if not df.empty else []

# ────────────────────────────────────────────────────────────────
# メイン
# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if args:
        date_str = args[0]
        venue_codes = args[1:] if len(args) > 1 else None
    else:
        date_str = datetime.today().strftime("%Y%m%d")
        venue_codes = None

    print(f"\n🏇 競馬CSV エクスポート開始: {date_str}")
    print("=" * 50)

    # history.csv（直近2週間）
    export_history()

    # 出走馬CSV（開催場を自動検出 or 引数指定）
    if venue_codes is None:
        print("\n🔍 当日開催場を自動検出中...")
        venue_codes = detect_today_venues(date_str)
        if not venue_codes:
            print("  ⚠️ 開催場が見つかりません。場コードを引数で指定してください。")
            print("  例: python export_csv.py 20260628 05 06")
        else:
            CODE_TO_NAME = {v: k for k, v in JRA_VENUES.items()}
            for vc in venue_codes:
                name = CODE_TO_NAME.get(vc, vc)
                print(f"  検出: {name}({vc})")

    for vc in venue_codes:
        export_entries(date_str, vc)

    print("\n✅ 完了！次のステップ:")
    print("  git add data/")
    print("  git commit -m \'Update race data {date_str}\'")
    print("  git push")
