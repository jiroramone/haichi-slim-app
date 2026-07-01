import psycopg2
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import requests
import re
import time
import textwrap
import unicodedata
import os
import io
import uuid
from datetime import date
from bs4 import BeautifulSoup
import logging

# ロギング設定（例外握りつぶし対策）
logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# PostgreSQL (My Keiba DB) 接続設定
# ============================================================
# ========================================================
# DB接続設定 ── パスワードをここに直接書いてください
# ========================================================
DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "keiba",
    "user":     "postgres",
    "password": "jiro4211",
}

def get_db_connection():
    """psycopg2接続を返す"""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding('SJIS')
    return conn

def run_query(sql, params=None):
    """SQLを実行してDataFrameを返す共通関数"""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
        cur.close()
        return df
    except Exception as e:
        logger.error(f"run_query error: {e}\nSQL: {sql[:200]}\nParams: {params}")
        raise
    finally:
        conn.close()

st.set_page_config(layout="wide", page_title="配置判定システム（CSV/DB両対応版）")

# -------------------------------------------------------------------------
# 🎮 ゲーム風アニメーション & スタイリング用 CSS インジェクション
# -------------------------------------------------------------------------
st.html("""
<style>
/* カードコンテナの基本アニメーション */
.game-card-container {
    transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
    border-radius: 12px !important;
    border: 1px solid #E0E0E0 !important;
    background-color: #FAFAFA;
    box-sizing: border-box;
    white-space: normal !important;
    height: 100%;
    min-width: 0 !important;
    word-break: break-word;
}

/* ホバー時に浮き上がるアニメーション */
.game-card-container:hover {
    transform: translateY(-5px) scale(1.01);
    box-shadow: 0 12px 24px rgba(0,0,0,0.12) !important;
    border-color: #4CAF50 !important;
}

/* 印エフェクト */
.active-honmei { box-shadow: 0 0 12px rgba(255, 87, 34, 0.4) !important; border: 2px solid #FF5722 !important; background-color: #FBE9E7 !important; }
.active-taikou { box-shadow: 0 0 12px rgba(33, 150, 243, 0.4) !important; border: 2px solid #2196F3 !important; background-color: #E3F2FD !important; }
.active-tanana { box-shadow: 0 0 12px rgba(76, 175, 80, 0.4) !important; border: 2px solid #4CAF50 !important; background-color: #E8F5E9 !important; }
.active-renka { box-shadow: 0 0 12px rgba(255, 193, 7, 0.4) !important; border: 2px solid #FFC107 !important; background-color: #FFF8E1 !important; }
.active-hoshi { box-shadow: 0 0 12px rgba(156, 39, 176, 0.4) !important; border: 2px solid #9C27B0 !important; background-color: #F3E5F5 !important; }
.active-keshi { opacity: 0.4 !important; transform: scale(0.98); filter: grayscale(100%); background-color: #ECEFF1 !important; box-shadow: none !important; border: 1px solid #B0BEC5 !important; }

/* スクロールバーのデザイン共通化 */
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar {
    height: 12px;
}
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar-track {
    background: #E0E0E0;
    border-radius: 6px;
}
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar-thumb {
    background: #90A4AE;
    border-radius: 6px;
    border: 2px solid #E0E0E0;
}
div[data-testid="stHorizontalBlock"]::-webkit-scrollbar-thumb:hover {
    background: #607D8B;
}
</style>
""")

# --- 専用メモリの初期化 ---
# 【改修】セッション初期化を辞書で一括管理
_SESSION_DEFAULTS = {
    'saved_chaku': {},
    'ignored_horses': {},
    'user_markers': {},
    'partner_cache': {},
    'fully_processed_df': pd.DataFrame(),
    'cached_owabi_riders': set(),
}
for _k, _v in _SESSION_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v
st.title("🏇 配置判定システム（CSV/DB両対応版）")
st.markdown("出馬表の**配置（カラー判定・ペア・対称）**と、**黄金比能力指数・調教ラップ**を統合したスリム版検討システムです。")

JRA_VENUES = {'札幌': '01', '函館': '02', '福島': '03', '新潟': '04', '東京': '05', '中山': '06', '中京': '07', '京都': '08', '阪神': '09', '小倉': '10'}

# -------------------------------------------------------------------------
# ヘルパー関数
# -------------------------------------------------------------------------
def parse_date(val):
    val_str = str(val)
    match = re.search(r'\d+', val_str)
    if match:
        nums = match.group()
        if len(nums) == 6:
            return pd.to_datetime(nums, format='%y%m%d', errors='coerce')
        elif len(nums) >= 8:
            return pd.to_datetime(nums[:8], format='%Y%m%d', errors='coerce')
    return pd.NaT

def clean_horse_name(name):
    if pd.isna(name) or name is None:
        return ""
    name_str = unicodedata.normalize('NFKC', str(name))
    name_str = name_str.strip().replace(" ", "").replace(" ", "").replace("$", "").replace("*", "").replace("＊", "")
    name_str = re.sub(r'[\(（\[［〇○□][外地父抽][\)）\]］]?', '', name_str)
    name_str = re.sub(r'^[〇○□]+', '', name_str)
    return name_str

def normalize_rank(val):
    norm_str = unicodedata.normalize('NFKC', str(val))
    match = re.search(r'\d+', norm_str)
    if match:
        return int(match.group())
    return pd.NA

def clean_time_diff(val):
    if pd.isna(val) or str(val).strip() == "":
        return np.nan
    cleaned = str(val).replace('秒', '').replace('+', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return np.nan

def format_lap_time(val):
    try:
        return f"{float(val):.1f}"
    except (ValueError, TypeError):
        return "-"

@st.cache_data(ttl=1800)
def get_master_history_data():
    """過去レース結果取得: DB優先、失敗時はCSVフォールバック
    CSVファイル名: data/history.csv
    """
    import os
    csv_path = os.path.join("data", "history.csv")

    try:
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
            WHERE r.kaisai_nen >= '2024'
            ORDER BY u.bamei, r.kaisai_nen, r.kaisai_gappi
        """
        df = run_query(query)
        if not df.empty:
            df["date"] = pd.to_datetime(df["日付raw"], format="%Y%m%d", errors="coerce")
            df["Ｒ"]   = pd.to_numeric(df["Ｒ"], errors="coerce")
            df["馬番"] = pd.to_numeric(df["馬番"], errors="coerce")
            df["馬名"] = df["馬名"].apply(clean_horse_name)
            # tansho_oddsは4桁整数格納(例:0030=3.0倍) → /10
            if "オッズ" in df.columns:
                df["オッズ"] = (pd.to_numeric(df["オッズ"], errors="coerce") / 10.0).round(1)
            logger.info(f"DB履歴取得: {len(df)}件")
            return df
    except Exception as e:
        logger.warning(f"DB履歴取得失敗 → CSVフォールバック: {e}")

    # CSVフォールバック
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path, dtype=str)
            df["date"] = pd.to_datetime(df.get("日付raw", df.get("date", "")), errors="coerce")
            df["Ｒ"]   = pd.to_numeric(df.get("Ｒ", pd.Series()), errors="coerce")
            df["馬番"] = pd.to_numeric(df.get("馬番", pd.Series()), errors="coerce")
            if "馬名" in df.columns:
                df["馬名"] = df["馬名"].apply(clean_horse_name)
            # tansho_oddsは4桁整数格納(例:0030=3.0倍) → /10
            if "オッズ" in df.columns:
                df["オッズ"] = (pd.to_numeric(df["オッズ"], errors="coerce") / 10.0).round(1)
            logger.info(f"CSV履歴読み込み: {csv_path} ({len(df)}行)")
            return df
        except Exception as e:
            logger.error(f"CSV履歴読み込みエラー: {e}")
    else:
        logger.warning(f"履歴CSVなし: {csv_path}")
    return pd.DataFrame()

def get_manual_history_data(file_bytes_content):
    f = io.BytesIO(file_bytes_content)
    try:
        try: 
            df = pd.read_csv(f, encoding='utf-8')
        except UnicodeDecodeError: 
            f.seek(0)
            df = pd.read_csv(f, encoding='cp932')
            
        df['date'] = df['日付'].apply(parse_date)
        if 'レースID(新)' in df.columns: 
            df['race_id'] = df['レースID(新)'].astype(str).str.strip()
        else: 
            df['race_id'] = df['日付'].astype(str) + df['場所'].astype(str) + df['Ｒ'].astype(str)
            
        df['rank'] = df['着順'].apply(normalize_rank)
        df['馬名'] = df['馬名'].apply(clean_horse_name)
        df = df.dropna(subset=['date']).sort_values(by=['馬名', 'date']).reset_index(drop=True)
        return df
    except Exception:
        return None
# ============================================================
# 当日出馬表・全レース一括取得（2段階キャッシュ戦略）
# ============================================================
@st.cache_data(ttl=300)
def get_all_entries_of_day(target_date_str: str, keibajo_code: str) -> pd.DataFrame:
    """
    出走馬一覧取得: DB優先、失敗時はCSVフォールバック
    CSVファイル名: data/entries_YYYYMMDD_VV.csv
    """
    import os
    csv_path = os.path.join("data", f"entries_{target_date_str}_{keibajo_code}.csv")
    db_ok = False

    try:
        nen   = target_date_str[:4]
        gappi = target_date_str[4:]
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
            WHERE r.kaisai_nen = %(nen)s
              AND r.kaisai_gappi = %(gappi)s
              AND r.keibajo_code = %(venue)s
            ORDER BY r.race_bango::int, u.umaban::int
        """
        df = run_query(query, params={"nen": nen, "gappi": gappi, "venue": keibajo_code})
        if not df.empty:
            db_ok = True
    except Exception as e:
        logger.warning(f"DB接続失敗 → CSVフォールバック: {e}")
        df = pd.DataFrame()

    # CSVフォールバック
    if not db_ok:
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path, dtype=str)
                logger.info(f"CSV読み込み: {csv_path} ({len(df)}行)")
            except Exception as e:
                logger.error(f"CSV読み込みエラー: {e}")
                return pd.DataFrame()
        else:
            logger.warning(f"CSVなし: {csv_path}")
            return pd.DataFrame()

    if df.empty:
        return df

    CODE_TO_VENUE = {v: k for k, v in JRA_VENUES.items()}
    df["場所"] = df["場コード"].map(CODE_TO_VENUE).fillna(df.get("場コード", df.get("場所", "")))
    df["日付S"] = target_date_str
    df["馬名"]  = df["馬名"].apply(clean_horse_name)
    for _col in ["馬番", "枠番", "Ｒ", "頭数"]:
        if _col in df.columns:
            df[_col] = pd.to_numeric(df[_col], errors="coerce").fillna(0).astype(int)
    for _col in ["斤量", "人気"]:
        if _col in df.columns:
            df[_col] = pd.to_numeric(df[_col], errors="coerce")
    # DBのtansho_oddsは4桁ゼロ埋め整数格納(例:0030=3.0倍, 0148=14.8倍) → 常に/10
    if "オッズ" in df.columns:
        _o = pd.to_numeric(df["オッズ"], errors="coerce")
        df["オッズ"] = (_o / 10.0).round(1)
    return df

def get_hanro_from_db(bango_tuple: tuple, race_date_str: str) -> pd.DataFrame:
    return pd.DataFrame()
def get_latest_odds_from_db(target_date_str: str, keibajo_code: str) -> pd.DataFrame:
    """オッズ取得: DB優先（jikeiretsu最新行）、失敗時はCSVフォールバック
    CSVファイル名: data/odds_YYYYMMDD_VV.csv
    カラム: race_num, umaban, odds_val, ninki_val
    """
    import os
    csv_path = os.path.join("data", f"odds_{target_date_str}_{keibajo_code}.csv")
    nen   = target_date_str[:4]
    gappi = target_date_str[4:]

    try:
        col_check = run_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'odds1_tansho_jikeiretsu' ORDER BY ordinal_position"
        )
        cols = col_check["column_name"].tolist() if not col_check.empty else []
        time_col = next((c for c in ["toku_code","hassou_jikoku","toroku_nengappi","data_nengappi"] if c in cols), None)

        if time_col:
            q = (
                "SELECT o.race_code,"
                " CAST(r.race_bango AS integer) AS race_num,"
                " CAST(o.umaban AS integer) AS umaban,"
                " CASE WHEN TRIM(o.odds) ~ '^[0-9]+$' THEN CAST(o.odds AS numeric)/10.0 END AS odds_val,"
                " CASE WHEN TRIM(o.ninki) ~ '^[0-9]+$' THEN CAST(o.ninki AS integer) END AS ninki_val"
                " FROM odds1_tansho_jikeiretsu o"
                " JOIN race_shosai r ON o.race_code = r.race_code"
                " JOIN (SELECT race_code, umaban, MAX(" + time_col + ") AS lt"
                "       FROM odds1_tansho_jikeiretsu GROUP BY race_code, umaban) x"
                "  ON o.race_code=x.race_code AND o.umaban=x.umaban AND o." + time_col + "=x.lt"
                " WHERE r.kaisai_nen=%s AND r.kaisai_gappi=%s AND r.keibajo_code=%s"
            )
            df = run_query(q, params=(nen, gappi, keibajo_code))
        else:
            q = (
                "SELECT o.race_code, CAST(r.race_bango AS integer) AS race_num,"
                " CAST(o.umaban AS integer) AS umaban,"
                " CASE WHEN TRIM(o.odds)~'^[0-9]+$' THEN CAST(o.odds AS numeric)/10.0 END AS odds_val,"
                " CASE WHEN TRIM(o.ninki)~'^[0-9]+$' THEN CAST(o.ninki AS integer) END AS ninki_val"
                " FROM odds1_tansho o JOIN race_shosai r ON o.race_code=r.race_code"
                " WHERE r.kaisai_nen=%s AND r.kaisai_gappi=%s AND r.keibajo_code=%s"
            )
            df = run_query(q, params=(nen, gappi, keibajo_code))

        if not df.empty:
            logger.info(f"DBオッズ取得: {len(df)}件")
            return df
    except Exception as e:
        logger.warning(f"DBオッズ取得失敗 → CSVフォールバック: {e}")

    # CSVフォールバック
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path, dtype=str)
            for c in ["race_num","umaban","ninki_val"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            if "odds_val" in df.columns:
                _ov = pd.to_numeric(df["odds_val"], errors="coerce")
                # DBと同じ4桁整数形式(例:0030=3.0倍) → 常に/10
                df["odds_val"] = (_ov / 10.0).round(1)
            logger.info(f"CSVオッズ読み込み: {csv_path} ({len(df)}行)")
            return df
        except Exception as e:
            logger.error(f"CSVオッズ読み込みエラー: {e}")
    return pd.DataFrame()

def apply_performance_levels(curr_df, history_df, global_target_datetime):
    return curr_df
def escape_html(text: str) -> str:
    """【改修】HTMLインジェクション対策：特殊文字をエスケープ"""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))

def get_waku_info(umaban, tousu):
    if pd.isna(tousu) or tousu <= 0: 
        return 1, "#FFFFFF", "#000000", "#CCCCCC"
        
    tousu = int(tousu)
    umaban = int(umaban)
    
    if tousu <= 8: 
        waku = umaban
    else:
        base = tousu // 8
        rem = tousu % 8
        waku_sizes = [base] * 8
        for i in range(rem): 
            waku_sizes[7 - i] += 1
            
        cumulative = 0
        waku = 8
        for w_idx, size in enumerate(waku_sizes):
            cumulative += size
            if umaban <= cumulative: 
                waku = w_idx + 1
                break
                
    w_c = {
        1: ("#FFFFFF", "#000000", "#CCCCCC"), 
        2: ("#1A1A1A", "#FFFFFF", "#1A1A1A"), 
        3: ("#E53935", "#FFFFFF", "#E53935"), 
        4: ("#1E88E5", "#FFFFFF", "#1E88E5"), 
        5: ("#FDD835", "#000000", "#FDD835"), 
        6: ("#43A047", "#FFFFFF", "#43A047"), 
        7: ("#FB8C00", "#000000", "#FB8C00"), 
        8: ("#F06292", "#FFFFFF", "#F06292")
    }
    bg, fg, border = w_c.get(waku, ("#FFFFFF", "#000000", "#CCCCCC"))
    return waku, bg, fg, border

def has_previous_pair_race(pair_str, current_r_num, my_venue):
    if not pair_str or pd.isna(pair_str) or str(pair_str).strip() in ["", "ー", "nan"]: 
        return False
        
    items = [x.strip() for x in str(pair_str).split(',') if x.strip()]
    for item in items:
        m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
        if m:
            race_key = m.group(1)
            m_r = re.search(r'(\d+)R', race_key)
            if m_r:
                r_val = int(m_r.group(1))
                venue_part = re.sub(r'\d+R', '', race_key)
                is_same_venue = (not venue_part) or (venue_part == my_venue)
                if is_same_venue and r_val < current_r_num: 
                    return True
        else:
            m_r = re.search(r'(\d+)R', item)
            if m_r:
                r_val = int(m_r.group(1))
                venue_part = re.sub(r'\d+R', '', item)
                is_same_venue = (not venue_part) or (venue_part == my_venue)
                if is_same_venue and r_val < current_r_num: 
                    return True
    return False

def split_by_comma_outside_parentheses(text):
    if not text: 
        return []
    items = []
    current = []
    depth = 0
    for char in text:
        if char == '(': 
            depth += 1
        elif char == ')': 
            depth -= 1
            
        if char == ',' and depth == 0: 
            items.append("".join(current).strip())
            current = []
        else: 
            current.append(char)
            
    if current: 
        items.append("".join(current).strip())
    return [x for x in items if x]

def format_condensed_pairs(pair_str):
    if not pair_str or pd.isna(pair_str) or str(pair_str).strip() in ["", "ー", "nan"]: 
        return ""
        
    race_map = {}
    for item in [x.strip() for x in str(pair_str).split(',') if x.strip()]:
        m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
        if m:
            race_key = m.group(1)
            sig_part = m.group(2)
            if race_key not in race_map: 
                race_map[race_key] = set()
            if sig_part:
                for s in sig_part.split(','):
                    if s.strip(): 
                        race_map[race_key].add(s.strip())
        else:
            if item not in race_map: 
                race_map[item] = set()
                
    def sort_key(x):
        race_name = x[0]
        venue = re.sub(r'\d+R', '', race_name)
        m_r = re.search(r'(\d+)R', race_name)
        r_num = int(m_r.group(1)) if m_r else 99
        return (venue, r_num)
        
    condensed_items = []
    for k, v in sorted(race_map.items(), key=sort_key):
        if v:
            condensed_items.append(f"{k}({','.join(sorted(list(v)))})")
        else:
            condensed_items.append(k)
            
    return ",".join(condensed_items)

def make_badge_html(label_type, text):
    if pd.isna(text) or text is None or str(text).strip() in ["", "ー"]: 
        return ""
        
    text_str = str(text).strip()
    bg = "#f0f0f0"
    color = "#333333"
    
    if "〇" in text_str: 
        bg = "#FFD2D2"
        color = "#800000"
    elif "▲" in text_str: 
        bg = "#FFE4C4"
        color = "#8B4513"
    elif "△" in text_str: 
        bg = "#FFF9C4"
        color = "#6D4C41"
    elif "◆" in text_str: 
        bg = "#D6E4FF"
        color = "#002D80"
    elif "✖" in text_str: 
        bg = "#ECEFF1"
        color = "#37474F"
    elif "✨🟦" in text_str: 
        bg = "#E3F2FD"
        color = "#0D47A1"
    elif "🔄" in text_str: 
        bg = "#F3E5F5"
        color = "#4A148C"
    
    if label_type == "騎手":
        icon = "👤"
    elif label_type == "調教":
        icon = "🏠"
    elif label_type == "馬主":
        icon = "👑"
    else:
        icon = "🔖"
        
    return f"""
    <div style="background-color: {bg}; color: {color}; font-size: 13px; font-weight: bold; padding: 6px 10px; border-radius: 6px; margin-top: 4px; display: flex; align-items: center; gap: 8px; border: 1px solid {bg};">
        <span>{icon}</span><span>{text_str}</span>
    </div>
    """

def scrape_yahoo_odds_jra(date_str, venue_name, r_num):
    """JRA公式サイトから単勝オッズをスクレイプ"""
    VENUE_TO_CODE = {
        '札幌': '01', '函館': '02', '福島': '03', '新潟': '04', '東京': '05',
        '中山': '06', '中京': '07', '京都': '08', '阪神': '09', '小倉': '10'
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ja,en;q=0.9",
    }
    try:
        parts = str(date_str).replace('-', '').replace('/', '')[:8]
        venue_code = VENUE_TO_CODE.get(venue_name, '')
        if not venue_code or len(parts) < 8:
            return None
        year   = parts[:4]
        month  = parts[4:6]
        day    = parts[6:8]
        # JRA公式オッズURL (単勝・複勝)
        # 開催識別子の推定: 年+場コード+回+日 → 当日出馬表から取得が理想だが、
        # 簡易版として netkeiba の JSON API を使用
        # netkeiba オッズJSON: https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=...
        # race_id形式: YYYY+場コード+回開催(01)+日開催(01)+レース番号
        # 例: 2026年6月28日 阪神9R → 202609010109
        # 回・日は推定困難なため、jra.go.jpのtraceページから取得

        # まず jra.go.jp のトップから当日の開催情報を取得
        url_top = f"https://www.jra.go.jp/keiba/today/schedule/"
        res = requests.get(url_top, headers=headers, timeout=8)
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, 'html.parser')
        odds_map = {}

        # 単勝オッズテーブルを探す
        for table in soup.find_all('table'):
            text = table.get_text()
            if '単勝' in text or 'オッズ' in text:
                rows = table.find_all('tr')
                for row in rows:
                    tds = row.find_all('td')
                    if len(tds) >= 2:
                        num_txt = re.sub(r'\D', '', tds[0].get_text().strip())
                        for td in tds[1:]:
                            o_txt = td.get_text().strip().replace(',', '').replace('\n', '').strip()
                            if num_txt.isdigit() and re.match(r'^\d+\.\d$', o_txt):
                                odds_map[int(num_txt)] = float(o_txt)
                                break
        if odds_map:
            return odds_map
    except Exception as e:
        logger.warning(f"scrape_odds: {e}")
    return None


def preprocess_and_calculate_haichi(df):
    if df.empty: 
        return df
        
    df = df.reset_index(drop=True)
    
    if 'Ｒ' in df.columns: 
        df['Ｒ'] = df['Ｒ'].astype(str).str.extract(r'(\d+)')[0].astype(int)
        
    if '場所' in df.columns: 
        df['場所'] = df['場所'].astype(str).str.extract(r'([^\d\s\(\)]+)')[0].str.strip()
        
    for col in ['騎手', '調教師', '馬主(最新/仮想)']:
        if col in df.columns: 
            df[col] = df[col].apply(clean_horse_name)
            
    df['頭数'] = df.groupby(['日付S', '場所', 'Ｒ'])['馬番'].transform('max')
    df['正番'] = df['馬番'].astype(int)
    df['逆番'] = df['頭数'].astype(int) - df['正番'] + 1
    df['正循環'] = df['正番'] + df['頭数'].astype(int)
    df['逆循環'] = df['逆番'] + df['頭数'].astype(int)
    
    return df.sort_values(by=['日付S', '場所', 'Ｒ', '馬番']).reset_index(drop=True)

def calculate_haichi_features(df):
    df = df.copy()
    # 馬番・枠番・Ｒを確実にint化（DB由来の文字列対応）
    for _c in ["馬番", "枠番", "Ｒ"]:
        if _c in df.columns:
            df[_c] = pd.to_numeric(df[_c], errors="coerce").fillna(0).astype(int)
    if df.empty: 
        return df
        
    df['is_blue_jockey'] = df['騎手_青塗'].astype(float)
    df['next_to_blue'] = 0.0
    df['is_symmetry'] = 0.0
    df['next_to_symmetry'] = 0.0
    
    for (date, loc, r), group in df.groupby(['日付S', '場所', 'Ｒ']):
        blue_nums = group[group['is_blue_jockey'] == 1.0]['馬番'].tolist()
        for b_num in blue_nums:
            target = (df['日付S']==date) & (df['場所']==loc) & (df['Ｒ']==r) & ((df['馬番']==b_num+1)|(df['馬番']==b_num-1))
            df.loc[target, 'next_to_blue'] = 1.0
            
        for t_col in ['調教師']:
            if t_col not in df.columns: 
                continue
                
            for name, t_group in group.groupby(t_col):
                name_str = str(name).strip()
                if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                    continue
                    
                if len(t_group) > 1:
                    for idx, row in t_group.iterrows():
                        other_gyaku = t_group[t_group['馬番'] != row['馬番']]['逆番'].tolist()
                        if row['正番'] in other_gyaku:
                            target_base = (df['日付S']==date) & (df['場所']==loc) & (df['Ｒ']==r)
                            df.loc[target_base & (df['馬番']==row['馬番']), 'is_symmetry'] = 1.0
                            
                            target_next = target_base & ((df['馬番']==row['馬番']+1)|(df['馬番']==row['馬番']-1)) & (df['is_symmetry']==0)
                            df.loc[target_next, 'next_to_symmetry'] = 1.0
                            
    return df

def judge_yellow_and_pairs(combined_df, target_col='騎手'):
    combined_df[f'{target_col}_黄塗'] = False
    combined_df[f'{target_col}_ペア'] = ""
    combined_df[f'{target_col}_厩舎同ペア'] = False
    
    if combined_df.empty or target_col not in combined_df.columns: 
        return combined_df

    pair_map = {
        ('正番', '正番'): 'A', ('正番', '逆番'): 'B', ('正番', '正循環'): 'C', ('正番', '逆循環'): 'D', 
        ('逆番', '正番'): 'E', ('逆番', '逆番'): 'F', ('逆番', '正循環'): 'G', ('逆番', '逆循環'): 'H', 
        ('正循環', '正番'): 'I', ('正循環', '逆番'): 'J', ('正循環', '正循環'): 'K', ('正循環', '逆循環'): 'L', 
        ('逆循環', '正番'): 'M', ('逆循環', '逆番'): 'N', ('逆循環', '正循環'): 'O', ('逆循環', '逆循環'): 'P'
    }

    for (date, loc), loc_group in combined_df.groupby(['日付S', '場所']):
        for name, group in loc_group.groupby(target_col):
            name_str = str(name).strip()
            if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                continue
            
            race_nums = sorted(group['Ｒ'].unique())
            if len(race_nums) < 2:
                continue
                
            for i in range(len(race_nums) - 1):
                prev_r = race_nums[i]
                curr_r = race_nums[i + 1]
                
                prev_rows = group[group['Ｒ'] == prev_r]
                curr_rows = group[group['Ｒ'] == curr_r]
                
                for idx_prev, prev_row in prev_rows.iterrows():
                    for idx_curr, curr_row in curr_rows.iterrows():
                        match_found = False
                        detected_pairs_curr = []
                        detected_pairs_prev = []
                        
                        info_for_curr = f"{prev_r}R"
                        info_for_prev = f"{curr_r}R"
                        
                        is_stable_match = False
                        if '騎手' in prev_row and '調教師' in prev_row:
                            if prev_row['騎手'] == curr_row['騎手'] and prev_row['調教師'] == curr_row['調教師']:
                                is_stable_match = True
                        
                        for p1 in ['正番', '逆番', '正循環', '逆循環']:
                            for p2 in ['正番', '逆番', '正循環', '逆循環']:
                                if prev_row[p1] == curr_row[p2]:
                                    match_found = True
                                    sig = pair_map[(p1, p2)]
                                    detected_pairs_curr.append(f"{info_for_curr}({sig})")
                                    detected_pairs_prev.append(f"{info_for_prev}({sig})")
                        
                        p_prev = prev_row['正番']
                        p_curr = curr_row['正番']
                        if (p_prev % 10 == p_curr % 10) and (p_prev != p_curr):
                            match_found = True
                            sig = 'Q' if p_prev < p_curr else 'R'
                            detected_pairs_curr.append(f"{info_for_curr}({sig})")
                            detected_pairs_prev.append(f"{info_for_prev}({sig})")
                        
                        if match_found:
                            combined_df.at[idx_curr, f'{target_col}_黄塗'] = True
                            combined_df.at[idx_prev, f'{target_col}_黄塗'] = True
                            
                            existing_curr = combined_df.at[idx_curr, f'{target_col}_ペア']
                            ex_list_curr = [x.strip() for x in existing_curr.split(',')] if existing_curr else []
                            for item in set(detected_pairs_curr):
                                if item not in ex_list_curr:
                                    ex_list_curr.append(item)
                            combined_df.at[idx_curr, f'{target_col}_ペア'] = ",".join(ex_list_curr)
                            
                            existing_prev = combined_df.at[idx_prev, f'{target_col}_ペア']
                            ex_list_prev = [x.strip() for x in existing_prev.split(',')] if existing_prev else []
                            for item in set(detected_pairs_prev):
                                if item not in ex_list_prev:
                                    ex_list_prev.append(item)
                            combined_df.at[idx_prev, f'{target_col}_ペア'] = ",".join(ex_list_prev)
                            
                            if is_stable_match: 
                                combined_df.at[idx_curr, f'{target_col}_厩舎同ペア'] = True
                                combined_df.at[idx_prev, f'{target_col}_厩舎同ペア'] = True

    return combined_df

def judge_blue_coating(df, target_col='騎手'):
    df[f'{target_col}_青塗'] = False
    blue_names = set()
    
    if df.empty or target_col not in df.columns: 
        return df, blue_names
        
    for (date, loc), loc_group in df.groupby(['日付S', '場所']):
        for name, group in loc_group.groupby(target_col):
            name_str = str(name).strip()
            if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                continue
            
            if group['Ｒ'].nunique() >= 2:
                for p in ['正番', '逆番', '正循環', '逆循環']:
                    if group[p].nunique() == 1:
                        df.loc[group.index, f'{target_col}_青塗'] = True
                        blue_names.add((date, name))
                        break 
                        
    return df, blue_names

def extract_owabi_riders(prev_df):
    owabi_riders = set()
    if prev_df.empty or '着順' not in prev_df.columns: 
        return owabi_riders
        
    prev_df, blue_date_riders = judge_blue_coating(prev_df, target_col='騎手')
    
    for date, rider in blue_date_riders:
        rider_races = prev_df[(prev_df['日付S'] == date) & (prev_df['騎手'] == rider)]
        has_hit = False
        for chaku in rider_races['着順']:
            try:
                chaku_int = int(float(str(chaku).strip()))
                if chaku_int in [1, 2, 3]: 
                    has_hit = True
                    break
            except Exception: 
                continue
                
        if not has_hit: 
            owabi_riders.add(rider)
            
    return owabi_riders

def calculate_placement_points(row):
    pts = 0.0
    if row.get('騎手_青塗') or row.get('調教師_青塗') or row.get('馬主(最新/仮想)_青塗', False): 
        pts += 3.0
    elif row.get('騎手_黄塗') or row.get('調教師_黄塗') or row.get('馬主(最新/仮想)_黄塗', False): 
        pts += 1.0
        
    if row.get('騎手_厩舎同ペア') or row.get('調教師_厩舎同ペア'): 
        pts += 2.0
        
    pair_str = str(row.get('騎手_ペア', '')) + str(row.get('調教師_ペア', '')) + str(row.get('馬主(最新/仮想)_ペア', ''))
    if any(p in pair_str for p in ['(C)', '(D)', '(G)', '(H)']): 
        pts += 1.5
        
    if row.get('is_symmetry', 0.0) == 1.0: 
        pts += 1.5
    elif row.get('next_to_symmetry', 0.0) == 1.0: 
        pts += 0.5
        
    odds_val = row.get('temp_odds', 0.0)
    if 10.0 <= odds_val < 20.0: 
        pts += 1.5
    elif 1.0 < odds_val < 50.0: 
        pts += 0.5
    elif odds_val >= 100.0: 
        pts -= 1.0
        
    return round(pts, 1)

def find_all_pair_partners_detailed(row, full_df):
    r_num = int(row['Ｒ'])
    my_venue = row['場所']
    date = row['日付S']
    partners_info = []
    targets = []
    
    if pd.notnull(row.get('騎手')): 
        targets.append(('騎手', row.get('騎手'), '騎手_ペア', 0))
    if pd.notnull(row.get('調教師')): 
        targets.append(('調教師', row.get('調教師'), '調教師_ペア', 1))
    if '馬主(最新/仮想)' in row.index and pd.notnull(row.get('馬主(最新/仮想)')): 
        targets.append(('馬主(最新/仮想)', row.get('馬主(最新/仮想)', 'ー'), '馬主(最新/仮想)_ペア', 2))
        
    for col_name, val, pair_col, cat_idx in targets:
        val_str = str(val).strip()
        if not val_str or val_str in ["", "ー", "nan", "None", "不明"]: 
            continue
            
        pair_text = str(row.get(pair_col, ''))
        if not pair_text or pair_text == "nan" or pair_text == "ー": 
            continue
            
        for item in split_by_comma_outside_parentheses(pair_text):
            m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
            if m:
                race_key = m.group(1)
                pair_sig = f" ({m.group(2)})" if m.group(2) else ""
                m_r = re.search(r'(\d+)R', race_key)
                if m_r:
                    tgt_r_int = int(m_r.group(1))
                    tgt_venue_part = re.sub(r'\d+R', '', race_key)
                    tgt_venue = tgt_venue_part if tgt_venue_part else my_venue
                    
                    match_rows = full_df[
                        (full_df['日付S'] == date) & 
                        (full_df['場所'] == tgt_venue) & 
                        (full_df['Ｒ'] == tgt_r_int) & 
                        (full_df[col_name] == val) & 
                        ~((full_df['場所'] == my_venue) & (full_df['Ｒ'] == r_num) & (full_df['馬番'] == row['馬番']))
                    ]
                
                    for _, m_row in match_rows.iterrows():
                        m_odds = m_row.get('オッズ')
                        m_pop = m_row.get('人気')
                        m_num = int(m_row.get('馬番'))
                        m_name = m_row.get('馬name', m_row.get('馬名', '不明'))
                        
                        c1 = st.session_state['saved_chaku'].get(f"c1_{tgt_venue}_{tgt_r_int}")
                        c2 = st.session_state['saved_chaku'].get(f"c2_{tgt_venue}_{tgt_r_int}")
                        c3 = st.session_state['saved_chaku'].get(f"c3_{tgt_venue}_{tgt_r_int}")
                        
                        chaku_status = "未確定"
                        if c1 is not None or c2 is not None or c3 is not None:
                            if m_num == c1: 
                                chaku_status = "<span style='color:#FFD700; font-weight:bold;'>🏆 1着好走</span>"
                            elif m_num == c2: 
                                chaku_status = "<span style='color:#C0C0C0; font-weight:bold;'>🥈 2着好走</span>"
                            elif m_num == c3: 
                                chaku_status = "<span style='color:#CD7F32; font-weight:bold;'>🥉 3着好走</span>"
                            else: 
                                chaku_status = "<span style='color:#888;'>凡走(4着以下)</span>"
                        
                        odds_txt = f"単勝 {m_odds}倍" if pd.notnull(m_odds) and str(m_odds).strip() not in ["", "nan"] else "単勝 未取得"
                        pop_txt = f"{int(m_pop)}人気" if pd.notnull(m_pop) and str(m_pop).strip() not in ["", "nan"] else "未設定"
                        
                        if col_name == "騎手":
                            category_label = "騎手"
                        elif col_name == "調教師":
                            category_label = "調教"
                        else:
                            category_label = "馬主"
                            
                        venue_label = f"{tgt_venue}" if tgt_venue != my_venue else ""
                        formatted_str = f"🔗 {venue_label}{tgt_r_int}R {m_num}番 {m_name} ({category_label}{pair_sig}) 【{odds_txt} / {pop_txt} / {chaku_status}】"
                        
                        partners_info.append((cat_idx, tgt_venue, tgt_r_int, m_num, formatted_str))
                    
    unique_partners = list(set(partners_info))
    unique_partners.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    
    result_list = []
    for item in unique_partners:
        result_list.append(item[4])
        
    return result_list

# -------------------------------------------------------------------------
# 🌟 カード描画用の共通関数 (1頭分)
# -------------------------------------------------------------------------
def render_single_horse_card(row_data, selected_venue, curr_df):
    r_num = int(row_data['Ｒ'])
    num_val = int(row_data['馬番']) if pd.notnull(row_data['馬番']) else 0
    name_val = row_data.get('馬name', row_data.get('馬名', '不明'))
    tousu_val = row_data.get('頭数', 16)
    w_num, waku_bg, waku_fg, waku_border = get_waku_info(num_val, tousu_val)
    
    raw_points = row_data.get('配置ポイント', 0.0)
    star_count = min(5, max(1, int(raw_points // 1.5))) if raw_points > 0 else 0
    stars = "★" * star_count if star_count > 0 else ""
    
    horse_key = f"{selected_venue}_{r_num}_{num_val}"
    marker_val = st.session_state['user_markers'].get(horse_key, "未設定")
    
    card_class = "game-card-container"
    if marker_val == "◎": 
        card_class += " active-honmei"
    elif marker_val == "○": 
        card_class += " active-taikou"
    elif marker_val == "▲": 
        card_class += " active-tanana"
    elif marker_val == "△": 
        card_class += " active-renka"
    elif marker_val == "☆": 
        card_class += " active-hoshi"
    elif marker_val == "✖": 
        card_class += " active-keshi"
    
    if marker_val == "未設定" and raw_points > 0: 
        border_left_style = "5px solid #4CAF50"
    else: 
        border_left_style = "1px solid #E0E0E0" if marker_val == "未設定" else "none"
    
    text_color_style = "color: #111111;"
    badge_opacity = "opacity: 1.0;"
    points_style = "color: #FF9800;"
    stars_txt = f" ({stars})" if stars else ""
    
    if marker_val == "✖": 
        text_color_style = "color: #888888;"
        badge_opacity = "opacity: 0.35; filter: grayscale(100%);"
        points_style = "color: #90A4AE;"
        stars_txt = ""
    
    o_val = row_data.get('オッズ', None)
    p_val = row_data.get('人気', None)
    try:
        o_f = float(o_val)
        odds_txt = f"{o_f}倍" if o_f > 0 else "未取得"
    except (ValueError, TypeError):
        odds_txt = "未取得"
    try:
        p_f = float(p_val)
        pop_txt = f"{int(p_f)}人気" if p_f > 0 else "未設定"
    except (ValueError, TypeError):
        pop_txt = "未設定"
    
    jockey_name = row_data.get('騎手', 'ー')
    j_pair = row_data.get('騎手_ペア', '')
    jockey_pair_txt = f"(ペア: {j_pair})" if pd.notnull(j_pair) and j_pair != "" else ""
    
    stable_name = row_data.get('調教師', 'ー')
    t_pair = row_data.get('調教師_ペア', '')
    stable_pair_txt = f"(ペア: {t_pair})" if pd.notnull(t_pair) and t_pair != "" else ""
    
    owner_name = row_data.get('馬主(最新/仮想)', 'ー') if '馬主(最新/仮想)' in row_data.index else 'ー'
    o_pair = row_data.get('馬主(最新/仮想)_ペア', '') if '馬主(最新/仮想)_ペア' in row_data.index else ''
    owner_pair_txt = f"(ペア: {o_pair})" if pd.notnull(o_pair) and o_pair != "" else ""
    
    owner_section_html = ""
    if pd.notnull(owner_name) and owner_name != "ー":
        owner_section_html = f"<div>👑 馬主: <strong style='font-size:14px; color:#111;'>{owner_name}</strong> <span style='color: #4CAF50; font-size: 11px; font-weight:bold; margin-left:4px;'>{owner_pair_txt}</span></div>"

    haichi_elements_html = f"""
    <div style="font-size: 13px; color: #333333; background-color: #FAFAFA; padding: 8px 12px; border-radius: 8px; margin-bottom: 8px; line-height: 1.5; border: 1px solid #ECEFF1; {badge_opacity}">
        <div style="margin-bottom: 2px;">👤 騎手: <strong style="font-size:14px; color:#111;">{jockey_name}</strong> <span style="color:#D500F9; font-size: 11px; font-weight:bold; margin-left:4px;">{jockey_pair_txt}</span></div>
        <div style="margin-bottom: 2px;">🏠 厩舎: <strong style="font-size:14px; color:#111;">{stable_name}</strong> <span style="color:#00B0FF; font-size: 11px; font-weight:bold; margin-left:4px;">{stable_pair_txt}</span></div>
        {owner_section_html}
    </div>"""
    
    perf_score = row_data.get('総合指数', 0.0)
    perf_rank = row_data.get('前走着順')
    perf_rank_txt = f"{int(perf_rank)}着" if (pd.notnull(perf_rank) and not pd.isna(perf_rank)) else "-"
    perf_interval = row_data.get('レース間隔', '-')
    perf_level = row_data.get('レベル点', 0.0)
    perf_jiri = row_data.get('自力点', 0.0)
    perf_bonus = row_data.get('ボーナス減点', 0.0)
    perf_hoso = row_data.get('好走/次走あり', '-')
    perf_diff = row_data.get('前走着差', '-')
    perf_leg = row_data.get('前走脚質', '-')
    perf_kyuyo = row_data.get('長期休養フラグ', '-')
    
    kyuyo_html = ""
    if "🚩" in str(perf_kyuyo): 
        kyuyo_html = f"""<div style="background-color: #E8F5E9; color: #2E7D32; font-size: 11px; font-weight: bold; padding: 4px; border-radius: 4px; margin-top: 4px; border: 1px solid #A5D6A7;">{perf_kyuyo}</div>"""
    elif perf_kyuyo != "-": 
        kyuyo_html = f"""<div style="background-color: #F5F5F5; color: #616161; font-size: 11px; padding: 4px; border-radius: 4px; margin-top: 4px; border: 1px solid #E0E0E0;">{perf_kyuyo}</div>"""

    perf_section_html = f"""
    <div style="margin-top: 8px; margin-bottom: 8px; padding: 8px 12px; background-color: #FFFDE7; border: 1px solid #FFF59D; border-radius: 8px; {badge_opacity}">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-weight: bold; font-size: 13px; color: #F57F17; display: flex; align-items: center; gap: 4px;"><span>⚡</span> <span>黄金比指数:</span></span>
            <span style="font-weight: bold; font-size: 14px; color: #E65100;">{perf_score if pd.notnull(perf_score) else 0.0} 点</span>
        </div>
        <div style="font-size: 11.5px; color: #5D4037; display: flex; flex-wrap: wrap; row-gap: 2px; column-gap: 8px;">
            <span>前走: <strong>{perf_rank_txt}</strong> ({perf_diff})</span>
            <span>間隔: <strong>{perf_interval}</strong></span>
            <span>脚質: <strong>{perf_leg}</strong></span>
            <span>相手: <strong>{perf_level if pd.notnull(perf_level) else 0.0}点</strong></span>
            <span>自力: <strong>{perf_jiri if pd.notnull(perf_jiri) else 0.0}点</strong></span>
            <span>加減: <strong>{perf_bonus if pd.notnull(perf_bonus) else 0.0}点</strong></span>
            <div style="width: 100%; border-top: 1px dashed #CCC; margin-top: 4px; padding-top: 4px; font-size: 10.5px;">
                {perf_hoso}
            </div>
        </div>
        {kyuyo_html}
    </div>"""
    
    hanro_html = ""

    badges_html_list = []
    checks = [
        ("騎手", row_data.get('騎手判定', 'ー')), 
        ("調教", row_data.get('調教師判定', 'ー')), 
        ("馬主", row_data.get('馬主判定', 'ー') if '馬主判定' in row_data.index else 'ー'), 
        ("サイン", row_data.get('配置サイン', 'ー'))
    ]
    for l_t, dec in checks:
        b_h = make_badge_html(l_t, dec)
        if b_h: badges_html_list.append(b_h)
    
    dec_badges_section = f"""<div style="display: flex; flex-direction: column; gap: 2px; margin-top: 4px; {badge_opacity}">{"".join(badges_html_list)}</div>""" if badges_html_list else ""
    
    if horse_key not in st.session_state['partner_cache']:
        st.session_state['partner_cache'][horse_key] = find_all_pair_partners_detailed(row_data, curr_df)
        
    detailed_partners = st.session_state['partner_cache'][horse_key]
    future_partner_html = ""
    if detailed_partners:
        p_list = [f"""<div style="margin-top: 4px; font-size: 11px; line-height: 1.4; background-color: #FFFFFF; padding: 4px 8px; border-radius: 4px; border-left: 3px solid #0066CC; box-shadow: 0 1px 2px rgba(0,0,0,0.05); color: #333333;">{p}</div>""" for p in detailed_partners]
        future_partner_html = f"""
        <div style="margin-top: 8px; padding: 8px; background-color: #EBF3FC; border: 1px solid #B3D4FF; border-radius: 8px; {badge_opacity}">
            <div style="font-weight: bold; font-size: 12px; color: #0052CC; display: flex; align-items: center; gap: 4px; margin-bottom: 4px;">
                <span style="font-size: 14px;">🔮</span><span>同期ペア情報:</span>
            </div>
            <div style="display: flex; flex-direction: column; gap: 2px;">{"".join(p_list)}</div>
        </div>"""
    
    r_num_badge = f"<span style='background-color:#000; color:#FFF; font-size:12px; padding:2px 6px; border-radius:4px; margin-right:4px;'>{r_num}R</span>"
    
    card_html = textwrap.dedent(f"""
    <div class="{card_class}" style="border-left: {border_left_style} !important; padding: 12px; box-shadow: 1px 1px 4px rgba(0,0,0,0.05); font-family: sans-serif; {text_color_style} display: flex; flex-direction: column; justify-content: space-between; overflow: hidden;">
        <div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; align-items: center; gap: 4px;">
                    {r_num_badge}
                    <span style="background-color: {waku_bg}; color: {waku_fg}; border: 2px solid {waku_border}; font-size: 14px; font-weight: bold; padding: 2px 6px; border-radius: 50%; box-shadow: 1px 1px 3px rgba(0,0,0,0.2); display: inline-block; min-width: 26px; text-align: center;">{num_val}</span>
                    <span style="font-size: 17px; font-weight: bold; margin-left:2px;">{name_val}</span>
                </div>
                <div style="text-align: right; {text_color_style}">
                    <span style="font-size: 13px; font-weight: bold;">{odds_txt} <span style="font-size:11px; font-weight:normal; color:#666;">({pop_txt})</span></span>
                    <div style="{points_style} font-size: 14px; font-weight: bold; margin-top: 2px;">{raw_points}点{stars_txt}</div>
                </div>
            </div>
            {haichi_elements_html}
            {perf_section_html}
            {hanro_html}
            {dec_badges_section}
        </div>
        {future_partner_html}
    </div>
    """).strip()
    
    with st.container(border=False):
        try: st.html(card_html)
        except AttributeError: st.markdown(card_html, unsafe_allow_html=True)
        
        b_cols1 = st.columns(4)
        with b_cols1[0]:
            if st.button("◎本命", key=f"btn_honmei_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "◎"
                st.rerun()
        with b_cols1[1]:
            if st.button("○対抗", key=f"btn_taikou_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "○"
                st.rerun()
        with b_cols1[2]:
            if st.button("▲単穴", key=f"btn_tanana_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "▲"
                st.rerun()
        with b_cols1[3]:
            if st.button("△連下", key=f"btn_renka_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "△"
                st.rerun()
        
        b_cols2 = st.columns(3)
        with b_cols2[0]:
            if st.button("☆穴馬", key=f"btn_hoshi_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "☆"
                st.rerun()
        with b_cols2[1]:
            if st.button("✖消す", key=f"btn_keshi_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "✖"
                st.rerun()
        with b_cols2[2]:
            if st.button("⚪戻す", key=f"btn_clear_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "未設定"
                st.rerun()

# 🌟 JSを利用した「絶対潰れない＆横スクロール」のレンダリング関数 🌟
def render_horse_cards_carousel(h_list, selected_venue, curr_df, cards_per_row=3, block_key=None):
    """3枚グリッド＋ページネーション（コールバック方式・rerun不使用）"""
    if not h_list:
        return

    if isinstance(h_list, pd.DataFrame):
        h_list = [row for _, row in h_list.iterrows()]

    total = len(h_list)
    if total == 0:
        return

    # block_key が未指定の場合は馬名ハッシュで固定
    if block_key is None:
        try:
            names = tuple(str(r.get('馬名', i)) if hasattr(r, 'get') else str(i) for i, r in enumerate(h_list[:5]))
            block_key = "cp_" + str(abs(hash(names)))[:10]
        except Exception:
            block_key = "cp_default"

    page_key = f"carousel_page_{block_key}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0

    # 1ページ = 3列 × 6行 = 最大18頭（1レース分）
    page_size   = cards_per_row * 6
    total_pages = max(1, -(-total // page_size))

    # コールバック関数（rerun不要・ボタン押下時に即座にページ番号を更新）
    def go_prev():
        st.session_state[page_key] = max(0, st.session_state[page_key] - 1)

    def go_next():
        st.session_state[page_key] = min(total_pages - 1, st.session_state[page_key] + 1)

    current_page = min(st.session_state[page_key], total_pages - 1)
    start_idx    = current_page * page_size
    end_idx      = min(start_idx + page_size, total)
    page_items   = h_list[start_idx:end_idx]

    # ページナビ（複数ページある場合のみ）
    if total_pages > 1:
        nav_cols = st.columns([1, 4, 1])
        with nav_cols[0]:
            st.button("◀ 前",
                      key=f"prev_{block_key}",
                      disabled=(current_page == 0),
                      on_click=go_prev,
                      use_container_width=True)
        with nav_cols[1]:
            st.markdown(
                f"<div style='text-align:center;color:#666;font-size:13px;padding-top:6px;'>"
                f"{start_idx+1}〜{end_idx} / {total}頭　"
                f"({current_page+1}/{total_pages}ページ)</div>",
                unsafe_allow_html=True
            )
        with nav_cols[2]:
            st.button("次 ▶",
                      key=f"next_{block_key}",
                      disabled=(current_page >= total_pages - 1),
                      on_click=go_next,
                      use_container_width=True)

    # cards_per_row 枚ずつ行に並べる
    for row_start in range(0, len(page_items), cards_per_row):
        row_items = page_items[row_start:row_start + cards_per_row]
        cols = st.columns(len(row_items))
        for col, row_data in zip(cols, row_items):
            with col:
                render_single_horse_card(row_data, selected_venue, curr_df)
        st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)


# -------------------------------------------------------------------------
# メイン処理ブロック（DB版）
# -------------------------------------------------------------------------
st.sidebar.markdown("### 🧬 黄金比能力データベース")
history_df = get_master_history_data()

if history_df is not None and not history_df.empty:
    st.sidebar.success(f"自動ロード完了\n({len(history_df)}件のレコード)")
    min_d = history_df["date"].min()
    max_d = history_df["date"].max()
    st.sidebar.caption(f"DB収録期間: {min_d.strftime('%Y-%m-%d')} 〜 {max_d.strftime('%Y-%m-%d')}")
else:
    st.sidebar.warning("過去実績DBが取得できません。手動でCSVをロードしてください。")
    uploaded_history = st.sidebar.file_uploader("過去履歴DB-CSVを手動ロード", type=["csv"], key="history_manual")
    if uploaded_history is not None:
        history_df = get_manual_history_data(uploaded_history.getvalue())
        if history_df is not None:
            st.sidebar.success(f"手動ロード完了: {len(history_df)}件")

st.sidebar.markdown("---")
global_target_date = st.sidebar.date_input("判定基準日（開催日）", date.today())
global_target_datetime = pd.to_datetime(global_target_date)

st.sidebar.markdown("### 🏟️ 競馬場・レース選択")
selected_venue_name = st.sidebar.selectbox("競馬場", list(JRA_VENUES.keys()), key="sidebar_venue")
selected_venue_code = JRA_VENUES[selected_venue_name]

# ── 当日出馬表をDBから一括取得（全レース分） ──────────────────────────
target_date_str = global_target_date.strftime("%Y%m%d")
db_combo_key = f"{target_date_str}_{selected_venue_code}"

if st.session_state.get("last_db_combo_key") != db_combo_key:
    # 競馬場・日付が変わったらキャッシュをクリアして再取得
    st.session_state["partner_cache"] = {}
    st.session_state["user_markers"] = {}
    # saved_chaku はリセットしない（着順入力を保持）
    st.session_state["last_db_combo_key"] = db_combo_key  # ← 更新して毎回リセットを防ぐ

with st.spinner("📡 当日出馬表をDBから取得中..."):
    all_entries_df = get_all_entries_of_day(target_date_str, selected_venue_code)

if all_entries_df.empty:
    st.sidebar.error("❌ 出馬表がDBに見つかりません。開催日・競馬場を確認してください。")
    # フォールバック: 手動CSVアップロード
    st.sidebar.markdown("### 📁 手動CSVアップロード（代替）")
    col1, col2, col3 = st.columns(3)
    with col1: st.subheader("1. 前日の結果CSV")
    with col2: st.subheader("2. 当日の出馬表CSV")
    with col3: st.subheader("3. 坂路調教ラップCSV（任意）")
    prev_files = col1.file_uploader("前日の結果CSVを選択", type=["csv"], key="prev", accept_multiple_files=True)
    curr_files = col2.file_uploader("当日の出馬表CSVを選択", type=["csv"], key="curr", accept_multiple_files=True)
    uploaded_hanro = col3.file_uploader("坂路調教ラップCSVを選択", type=["csv"], key="hanro_upload",
                                        help="馬名, 年月日, Time1, Lap4... の列を含むこと")
    _use_db_entries = False
else:
    st.sidebar.success(f"✅ {len(all_entries_df)}頭の出走データを取得")
    race_count = all_entries_df["Ｒ"].nunique()
    st.sidebar.caption(f"{race_count}レース分のデータが揃っています")
    prev_files = []
    curr_files = None
    uploaded_hanro = None
    _use_db_entries = True

# ── 処理キーの決定 ──────────────────────────────────────────────────
if _use_db_entries:
    current_combo_key = db_combo_key
    # 坂路調教をDBから取得（血統登録番号で一括）
    hanro_clean_df = None
    if "ketto_toroku_bango" in all_entries_df.columns:
        bango_list = tuple(all_entries_df["ketto_toroku_bango"].dropna().unique().tolist())
        if bango_list:
            hanro_db = get_hanro_from_db(bango_list, target_date_str)
            if not hanro_db.empty:
                # 馬名をマッピング
                bango_to_name = dict(zip(all_entries_df["ketto_toroku_bango"], all_entries_df["馬名"]))
                hanro_db["馬名"] = hanro_db["ketto_toroku_bango"].map(bango_to_name).fillna("")
                hanro_db["馬名"] = hanro_db["馬名"].apply(clean_horse_name)
                # 最新の1レコードのみ保持
                hanro_db["chokyo_nengappi"] = hanro_db["chokyo_nengappi"].astype(str)
                # ── デバッグ: 変換前の生値をログ出力 ──
                for _dbg_col in ["4Fタイム", "Lap2", "ラスト1F"]:
                    if _dbg_col in hanro_db.columns:
                        _sample = pd.to_numeric(hanro_db[_dbg_col], errors="coerce").dropna()
                        if not _sample.empty:
                            logger.info(f"[調教DB生値] {_dbg_col}: min={_sample.min():.1f}, max={_sample.max():.1f}, median={_sample.median():.1f}")
                # タイム値の単位自動判定（DBが整数10倍 or 小数秒 どちらかを自動検出）
                for _tc in ["4Fタイム", "Lap4", "Lap3", "Lap2", "ラスト1F"]:
                    if _tc in hanro_db.columns:
                        _s = pd.to_numeric(hanro_db[_tc], errors="coerce")
                        # 中央値が100超なら10倍整数格納 → /10, そうでなければ既に秒単位
                        # 坂路4F=52秒台, ラスト1F=12秒台 → 整数格納なら520/120, 小数なら52.0/12.0
                        # 100超 = 整数格納確定
                        _median = _s.median()
                        if pd.notna(_median) and _median >= 100:
                            hanro_db[_tc] = _s / 10.0
                        else:
                            hanro_db[_tc] = _s
                # 当週で一番速いタイムの行を取得（4Fタイム昇順=速い順でソート）
                # 4Fタイムがない場合はラスト1F昇順でフォールバック
                hanro_clean_df = hanro_db.copy()
                hanro_clean_df["_sort_key"] = pd.to_numeric(hanro_clean_df["4Fタイム"], errors="coerce")
                # 4Fタイムがない行はラスト1Fで代用
                _l1_num = pd.to_numeric(hanro_clean_df["ラスト1F"], errors="coerce")
                hanro_clean_df["_sort_key"] = hanro_clean_df["_sort_key"].fillna(_l1_num * 4)
                hanro_clean_df = hanro_clean_df.sort_values("_sort_key", ascending=True)
                if "調教場" in hanro_clean_df.columns:
                    hanro_clean_df = hanro_clean_df.groupby(["馬名", "調教場"]).first().reset_index()
                else:
                    hanro_clean_df = hanro_clean_df.groupby("馬名").first().reset_index()
                hanro_clean_df = hanro_clean_df.drop(columns=["_sort_key"], errors="ignore")

    # 前日結果はDBのhistory_dfから自動的に賄われるため、owabi_ridersはそちらで計算
    owabi_riders = set()
    if history_df is not None and not history_df.empty:
        yesterday_str = (global_target_datetime - pd.Timedelta(days=1)).strftime("%Y%m%d")
        try:
            yesterday_df = history_df[
                history_df["date"] == pd.to_datetime(yesterday_str)
            ].copy()
            if not yesterday_df.empty:
                yesterday_df = preprocess_and_calculate_haichi(yesterday_df)
                owabi_riders = extract_owabi_riders(yesterday_df)
        except Exception as e:
            logger.warning(f"owabi_riders計算エラー: {e}")

    need_process = st.session_state.get("last_processed_key") != current_combo_key

    if need_process:
        with st.spinner("🏇 データを解析・計算しています...（競馬場・日付変更時のみ）"):
            st.session_state["partner_cache"] = {}
            df = all_entries_df.copy()

            # カラム名の整合（CSV版と同じ処理）
            ODDS_COL_ALIASES = ["単勝オッズ", "単勝", "オッズ", "単オッズ", "単勝(元値)",
                                  "Win Odds", "win_odds", "odds", "Odds", "単勝オッズ(確定)",
                                  "予想オッズ", "想定オッズ", "暫定オッズ"]
            POP_COL_ALIASES  = ["単勝人気", "人気", "確定人気", "人気順", "人気(確定)",
                                 "予想人気", "想定人気", "popularity"]
            rename_cols = {}
            for col in df.columns:
                col_norm = col.strip()
                if col_norm in ODDS_COL_ALIASES and col_norm != "オッズ":
                    rename_cols[col] = "オッズ"
                elif col_norm in POP_COL_ALIASES and col_norm != "人気":
                    rename_cols[col] = "人気"
            if rename_cols:
                df = df.rename(columns=rename_cols)

            if "オッズ" not in df.columns: df["オッズ"] = np.nan
            if "人気" not in df.columns: df["人気"] = np.nan

            df = preprocess_and_calculate_haichi(df)

            # 坂路ラップ評価を付与（DB取得データを使用）
            if hanro_clean_df is not None and not hanro_clean_df.empty:
                def classify_lap_db(row):
                    """
                    ラップ評価（動画: TAKE TUBE 竹内氏データより）
                    坂路・ウッド共通の終い2Fラップ判定
                    パターン番号は動画の定義に準拠:
                      5: 終いのみ11秒台 加速 → 最強(単回116%)
                      6(加): 2F連続11秒台 加速 → 強い(単回102%+)
                      6(減速≤0.4): 2Fとも11秒台で終い微減速 → 強い(単回100%+)
                      1: 終いのみ12秒台 加速 → 良い(単回86-102%)
                      3: 終い2F12秒台まとめ 加速 → 普通(単回86%)
                      4: 終い2F12秒台まとめ 減速 → やや不安(単回78%)
                      2: 2Fのみ12秒台 終い減速 → 地雷(単回60%台)
                    """
                    try:
                        l2 = float(row.get("Lap2"))
                        l1 = float(row.get("ラスト1F"))
                    except (TypeError, ValueError):
                        return "-"
                    if pd.isna(l2) or pd.isna(l1): return "-"
                    diff = round(l2 - l1, 1)   # 正=加速, 負=減速
                    accel = diff > 0            # True=加速
                    l1_11 = l1 < 12.0
                    l1_12 = l1 < 13.0
                    l2_11 = l2 < 12.0
                    l2_12 = l2 < 13.0

                    # ─── パターン5: 終いのみ11秒台の加速 ───
                    if accel and l1_11 and not l2_11:
                        return f"🌟[5]終い11秒台加速(+{diff}) 単回116%"
                    # ─── パターン6加速: 2F連続11秒台の加速 ───
                    if accel and l1_11 and l2_11:
                        return f"🌟[6↑]2F連続11秒台加速(+{diff}) 単回102%+"
                    # ─── パターン6減速: 2Fとも11秒台・微減速(≤0.4秒) ───
                    if not accel and l1_11 and l2_11 and abs(diff) <= 0.4:
                        return f"✅[6↓]2F連続11秒台・微減速({diff}) 単回100%+"
                    # ─── パターン1: 終いのみ12秒台の加速 ───
                    if accel and l1_12 and not l2_12:
                        return f"✅[1]終いのみ12秒台加速(+{diff}) 単回86-102%"
                    # ─── パターン3: 終い2F12秒台まとめ加速 ───
                    if accel and l1_12 and l2_12:
                        return f"👍[3]終い2F12秒台まとめ加速(+{diff}) 単回86%"
                    # ─── パターン4: 終い2F12秒台まとめ減速 ───
                    if not accel and l1_12 and l2_12 and l1 >= 12.0:
                        return f"⚠️[4]終い2F12秒台まとめ減速({diff}) 単回78%"
                    # ─── パターン2: 2Fのみ12秒台・終い減速(地雷) ───
                    if not accel and not l1_12 and l2_12:
                        return f"💣[2]地雷:2Fのみ12秒台終い減速({diff}) 単回60%"
                    # ─── 11秒台あり・大幅減速 ───
                    if l2_11 and not accel and abs(diff) > 0.4:
                        return f"⚠️[6↓]2Fのみ11秒台・減速({diff})"
                    # ─── その他 ───
                    if accel:
                        return f"👍加速(+{diff})"
                    elif diff == 0:
                        return f"- フラット(±0.0)"
                    elif abs(diff) > 1.5:
                        return f"🚨急失速({diff}) 要注意"
                    else:
                        return f"- 減速({diff})"

                def eval_wood_last1f(row):
                    """終い1Fタイム評価(ウッド) 単回収ベース"""
                    try:
                        l1 = float(row.get("ラスト1F"))
                    except (TypeError, ValueError):
                        return ""
                    if pd.isna(l1): return ""
                    if row.get("調教場", "") != "wood": return ""
                    if l1 < 12.0:   return "🌟終い11秒台(単回97%)"
                    elif l1 < 13.0: return "✅終い12秒台(単回85%)"
                    elif l1 < 14.0: return "⚠️終い13秒台(単回70%)"
                    else:           return "💣終い14秒以上(非常に悪い)"

                def eval_hanro_4f(row):
                    """坂路4Fタイム評価 単回収ベース"""
                    try:
                        t4 = float(row.get("4Fタイム"))
                    except (TypeError, ValueError):
                        return ""
                    if pd.isna(t4): return ""
                    if row.get("調教場", "") != "hanro": return ""
                    if t4 <= 49.9:   return "🌟4F≤49秒(最強)"
                    elif t4 <= 51.9: return "🌟4F50-51秒(単回93%)"
                    elif t4 <= 53.9: return "✅4F52-53秒(良い)"
                    elif t4 <= 55.9: return "💣4F54-55秒(危険ゾーン)"
                    elif t4 <= 57.9: return "⚠️4F56-57秒"
                    else:            return "💣4F58秒以上(遅い)"

                hanro_clean_df["ラップ評価"] = hanro_clean_df.apply(classify_lap_db, axis=1)
                hanro_clean_df["タイム評価"] = hanro_clean_df.apply(
                    lambda r: eval_wood_last1f(r) or eval_hanro_4f(r), axis=1
                )

                # ── 坂路・ウッド分離マップ ──────────────────────────────
                _h = hanro_clean_df[hanro_clean_df["調教場"] == "hanro"].drop_duplicates("馬名", keep="first").set_index("馬名")
                _w = hanro_clean_df[hanro_clean_df["調教場"] == "wood"].drop_duplicates("馬名", keep="first").set_index("馬名")

                def _col(df_sub, col):
                    return df_sub[col].to_dict() if col in df_sub.columns else {}

                # 坂路マップ
                h_lap  = _col(_h, "ラップ評価")
                h_time = _col(_h, "タイム評価")
                h_t4   = _col(_h, "4Fタイム")
                h_l4   = _col(_h, "Lap4")
                h_l3   = _col(_h, "Lap3")
                h_l2   = _col(_h, "Lap2")
                h_l1   = _col(_h, "ラスト1F")
                # ウッドマップ
                w_lap  = _col(_w, "ラップ評価")
                w_time = _col(_w, "タイム評価")
                w_t4   = _col(_w, "4Fタイム")
                w_l4   = _col(_w, "Lap4")
                w_l3   = _col(_w, "Lap3")
                w_l2   = _col(_w, "Lap2")
                w_l1   = _col(_w, "ラスト1F")

                # dfに坂路・ウッド別カラムとして付与
                df["坂路_ラップ評価"]  = df["馬名"].map(h_lap).fillna("-")
                df["坂路_タイム評価"]  = df["馬名"].map(h_time).fillna("")
                df["坂路_4Fタイム"]    = df["馬名"].map(h_t4)
                df["坂路_Lap4"]        = df["馬名"].map(h_l4)
                df["坂路_Lap3"]        = df["馬名"].map(h_l3)
                df["坂路_Lap2"]        = df["馬名"].map(h_l2)
                df["坂路_ラスト1F"]    = df["馬名"].map(h_l1)
                df["ウッド_ラップ評価"] = df["馬名"].map(w_lap).fillna("-")
                df["ウッド_タイム評価"] = df["馬名"].map(w_time).fillna("")
                df["ウッド_4Fタイム"]   = df["馬名"].map(w_t4)
                df["ウッド_Lap4"]       = df["馬名"].map(w_l4)
                df["ウッド_Lap3"]       = df["馬名"].map(w_l3)
                df["ウッド_Lap2"]       = df["馬名"].map(w_l2)
                df["ウッド_ラスト1F"]   = df["馬名"].map(w_l1)

                # 後方互換: ラップ評価・4Fタイム等は坂路優先で残す
                df["ラップ評価"] = df["馬名"].map({**w_lap, **h_lap}).fillna("-")
                df["タイム評価"] = df["馬名"].map({**w_time, **h_time}).fillna("")
                df["4Fタイム"]   = df["馬名"].map({**w_t4,  **h_t4})
                df["Lap4"]       = df["馬名"].map({**w_l4,  **h_l4})
                df["Lap3"]       = df["馬名"].map({**w_l3,  **h_l3})
                df["Lap2"]       = df["馬名"].map({**w_l2,  **h_l2})
                df["ラスト1F"]   = df["馬名"].map({**w_l1,  **h_l1})
            else:
                for col in ["ラップ評価","タイム評価","4Fタイム","Lap4","Lap3","Lap2","ラスト1F",
                               "坂路_ラップ評価","坂路_タイム評価","坂路_4Fタイム","坂路_Lap4","坂路_Lap3","坂路_Lap2","坂路_ラスト1F",
                               "ウッド_ラップ評価","ウッド_タイム評価","ウッド_4Fタイム","ウッド_Lap4","ウッド_Lap3","ウッド_Lap2","ウッド_ラスト1F"]:
                    if col not in df.columns: df[col] = np.nan

            # 能力指数計算
            if history_df is not None and not history_df.empty:
                df = apply_performance_levels(df, history_df, global_target_datetime)
            else:
                for col in ["総合指数","レベル点","自力点","ボーナス減点","前走着順",
                            "レース間隔","好走/次走あり","前走着差","前走脚質","長期休養フラグ","前走日付"]:
                    df[col] = "-" if col in ["レース間隔","好走/次走あり","前走着差","前走脚質","長期休養フラグ","前走日付"] else np.nan

            st.session_state["cached_owabi_riders"] = owabi_riders

        df = judge_yellow_and_pairs(df, target_col='騎手')
        df = judge_yellow_and_pairs(df, target_col='調教師')
        if '馬主(最新/仮想)' in df.columns: 
            df = judge_yellow_and_pairs(df, target_col='馬主(最新/仮想)')
            
        df, _ = judge_blue_coating(df, target_col='騎手')
        df, _ = judge_blue_coating(df, target_col='調教師')
        if '馬主(最新/仮想)' in df.columns: 
            df, _ = judge_blue_coating(df, target_col='馬主(最新/仮想)')
            
        for col_p in ['騎手_ペア', '調教師_ペア']:
            if col_p in df.columns: 
                df[col_p] = df[col_p].apply(format_condensed_pairs)
                
        if '馬主(最新/仮想)_ペア' in df.columns: 
            df['馬主(最新/仮想)_ペア'] = df['馬主(最新/仮想)_ペア'].apply(format_condensed_pairs)
            
        df['お詫び好走候補'] = df['騎手'].apply(lambda x: x in owabi_riders)
        df = calculate_haichi_features(df)
        
        def get_haichi_sign_label(row):
            signs = []
            is_blue = row.get('騎手_青塗', False) or row.get('調教師_青塗', False) or row.get('馬主(最新/仮想)_青塗', False)
            if is_blue: 
                signs.append("✨🟦 青塗本体")
            if row.get('is_symmetry', 0.0) == 1.0: 
                signs.append("🔄 対称")
            if row.get('next_to_symmetry', 0.0) == 1.0: 
                signs.append("↔️ 隣馬(対称)")
            if row.get('next_to_blue', 0.0) == 1.0: 
                signs.append("🟦 青塗隣馬")
                
            if signs:
                return " / ".join(signs)
            else:
                return "ー"
            
        df['配置サイン'] = df.apply(get_haichi_sign_label, axis=1)

        df['騎手判定'] = "ー"
        df['調教師判定'] = "ー"
        df['馬主判定'] = "ー"
        
        def safe_float(x):
            try:
                import unicodedata as _ud
                s = _ud.normalize('NFKC', str(x))
                s = s.replace(',', '').replace('倍', '').replace(' ', '').strip()
                return float(s) if s not in ('', 'nan', 'None', '---', 'ー', '-') else 0.0
            except Exception:
                return 0.0

        df['オッズ'] = df['オッズ'].apply(lambda x: safe_float(x) if safe_float(x) > 0 else float('nan'))
        df['temp_odds'] = df['オッズ'].fillna(0.0)

        for idx, row in df.iterrows():
            r_num = row['Ｒ']
            odds_val = row['temp_odds']
            my_venue = row['場所']
            
            # 騎手判定
            if row['騎手_青塗'] or row['騎手_黄塗'] or row['お詫び好走候補']:
                rider_name = row['騎手']
                rider_races = df[df['騎手'] == rider_name].sort_values(by='Ｒ')
                prev_rider_races = rider_races[(rider_races['場所'] == my_venue) & (rider_races['Ｒ'] < r_num)]
                
                if row['騎手_青塗']:
                    first_blue_race = rider_races.iloc[0]['Ｒ']
                    curr_num = int(row['馬番'])
                    neighbors = df[(df['場所'] == my_venue) & (df['Ｒ'] == r_num) & (df['馬番'].isin([curr_num - 1, curr_num + 1]))]
                    my_pop = int(row['人気']) if pd.notnull(row['人気']) else 99
                    
                    neighbor_has_pair = False
                    for _, n_row in neighbors.iterrows():
                        if n_row['騎手_黄塗'] or n_row['調教師_黄塗']:
                            neighbor_has_pair = True
                            
                    is_my_pop_top = False
                    for _, n_row in neighbors.iterrows():
                        n_pop = int(n_row['人気']) if pd.notnull(n_row['人気']) else 99
                        if my_pop < n_pop:
                            is_my_pop_top = True
                    
                    if r_num == first_blue_race:
                        if is_my_pop_top: 
                            df.at[idx, '騎手判定'] = "▲ 青塗1鞍目先買いリスク"
                        elif neighbor_has_pair: 
                            df.at[idx, '騎手判定'] = "△ 青塗隣馬ペアあり(オッズ49.9倍以下)"
                        else: 
                            df.at[idx, '騎手判定'] = "✖ 青塗先買いリスク(見送り)"
                    else:
                        if prev_rider_races.empty: 
                            df.at[idx, '騎手判定'] = "▲ 青塗先買い(前走未確定)"
                        else:
                            last_prev = prev_rider_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '騎手判定'] = "▲ 青塗先買い(前走未確定)"
                            else:
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]: 
                                    df.at[idx, '騎手判定'] = "✖ 青塗見送り(好走済)"
                                else: 
                                    df.at[idx, '騎手判定'] = "〇 青塗凡走後狙い(絶好の狙い目)"
                                    
                elif row['騎手_黄塗']:
                    yellow_races = rider_races[(rider_races['騎手_黄塗']) & (rider_races['場所'] == my_venue)].sort_values(by='Ｒ')
                    if not has_previous_pair_race(row.get('騎手_ペア', ''), r_num, my_venue): 
                        df.at[idx, '騎手判定'] = "▲ 先買いリスクあり"
                    else:
                        if prev_rider_races.empty: 
                            df.at[idx, '騎手判定'] = "▲ 黄色前走未確定"
                        else:
                            last_prev = prev_rider_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '騎手判定'] = "▲ 黄色前走未確定"
                            else:
                                is_lp_hit = False
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]:
                                    is_lp_hit = True
                                    
                                is_double_fail = False
                                
                                if len(prev_rider_races) >= 2:
                                    lp2 = prev_rider_races.iloc[-2]
                                    lp2_v = lp2['場所']
                                    lp2_r = lp2['Ｒ']
                                    lp2_c1 = st.session_state['saved_chaku'].get(f"c1_{lp2_v}_{lp2_r}")
                                    lp2_c2 = st.session_state['saved_chaku'].get(f"c2_{lp2_v}_{lp2_r}")
                                    lp2_c3 = st.session_state['saved_chaku'].get(f"c3_{lp2_v}_{lp2_r}")
                                    
                                    if lp2_c1 is not None and lp_c1 is not None:
                                        is_lp2_hit = False
                                        if int(lp2['馬番']) in [lp2_c1, lp2_c2, lp2_c3]:
                                            is_lp2_hit = True
                                            
                                        if not is_lp_hit and not is_lp2_hit: 
                                            is_double_fail = True
                                            
                                if is_double_fail: 
                                    df.at[idx, '騎手判定'] = "◆ 共に凡走(紐警戒)"
                                elif is_lp_hit: 
                                    df.at[idx, '騎手判定'] = "✖ 好走済みで後のレースは狙えない"
                                else:
                                    if len(yellow_races) >= 3 and row['騎手_厩舎同ペア']: 
                                        df.at[idx, '騎手判定'] = "〇 狙いたいケース(前走凡走・騎手厩舎同一ペア)"
                                    else: 
                                        df.at[idx, '騎手判定'] = "〇 狙いたいケース(前走凡走)"
            
            # 調教師判定
            if row['調教師_青塗'] or row['調教師_黄塗']:
                stable_name = row['調教師']
                stable_races = df[df['調教師'] == stable_name].sort_values(by='Ｒ')
                prev_stable_races = stable_races[(stable_races['場所'] == my_venue) & (stable_races['Ｒ'] < r_num)]
                
                if row['調教師_青塗']:
                    first_blue_race = stable_races.iloc[0]['Ｒ']
                    curr_num = int(row['馬番'])
                    neighbors = df[(df['場所'] == my_venue) & (df['Ｒ'] == r_num) & (df['馬番'].isin([curr_num - 1, curr_num + 1]))]
                    my_pop = int(row['人気']) if pd.notnull(row['人気']) else 99
                    
                    neighbor_has_pair = False
                    for _, n_row in neighbors.iterrows():
                        if n_row['騎手_黄塗'] or n_row['調教師_黄塗']:
                            neighbor_has_pair = True
                            
                    is_my_pop_top = False
                    for _, n_row in neighbors.iterrows():
                        n_pop = int(n_row['人気']) if pd.notnull(n_row['人気']) else 99
                        if my_pop < n_pop:
                            is_my_pop_top = True
                            
                    if r_num == first_blue_race:
                        if is_my_pop_top: 
                            df.at[idx, '調教師判定'] = "▲ 青塗1鞍目先買いリスク"
                        elif neighbor_has_pair: 
                            df.at[idx, '調教師判定'] = "△ 青塗隣馬ペアあり(オッズ49.9倍以下)"
                        else: 
                            df.at[idx, '調教師判定'] = "✖ 青塗先買いリスク(見送り)"
                    else:
                        if prev_stable_races.empty: 
                            df.at[idx, '調教師判定'] = "▲ 青塗先買い(前走未確定)"
                        else:
                            last_prev = prev_stable_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '調教師判定'] = "▲ 青塗先買い(前走未確定)"
                            else:
                                lp_num = int(last_prev['馬番'])
                                is_lp_sym = False
                                if (last_prev.get('is_symmetry', 0.0) == 1.0) or (last_prev.get('next_to_symmetry', 0.0) == 1.0):
                                    is_lp_sym = True
                                    
                                is_lp_hit = False
                                if is_lp_sym: 
                                    if (lp_num in [lp_c1, lp_c2, lp_c3]) or ((lp_num + 1) in [lp_c1, lp_c2, lp_c3]) or ((lp_num - 1) in [lp_c1, lp_c2, lp_c3]):
                                        is_lp_hit = True
                                else: 
                                    if lp_num in [lp_c1, lp_c2, lp_c3]:
                                        is_lp_hit = True
                                        
                                if is_lp_hit: 
                                    df.at[idx, '調教師判定'] = "✖ 青塗見送り(好走済)"
                                else: 
                                    df.at[idx, '調教師判定'] = "〇 青塗凡走後狙い(絶好の狙い目)"
                                    
                elif row['調教師_黄塗']:
                    yellow_races = stable_races[(stable_races['調教師_黄塗']) & (stable_races['場所'] == my_venue)].sort_values(by='Ｒ')
                    if not has_previous_pair_race(row.get('調教師_ペア', ''), r_num, my_venue): 
                        df.at[idx, '調教師判定'] = "▲ 先買いリスクあり"
                    else:
                        if prev_stable_races.empty: 
                            df.at[idx, '調教師判定'] = "▲ 黄色前走未確定"
                        else:
                            last_prev = prev_stable_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '調教師判定'] = "▲ 黄色前走未確定"
                            else:
                                lp_num = int(last_prev['馬番'])
                                is_lp_sym = False
                                if (last_prev.get('is_symmetry', 0.0) == 1.0) or (last_prev.get('next_to_symmetry', 0.0) == 1.0):
                                    is_lp_sym = True
                                    
                                is_lp_hit = False
                                if is_lp_sym: 
                                    if (lp_num in [lp_c1, lp_c2, lp_c3]) or ((lp_num + 1) in [lp_c1, lp_c2, lp_c3]) or ((lp_num - 1) in [lp_c1, lp_c2, lp_c3]):
                                        is_lp_hit = True
                                else: 
                                    if lp_num in [lp_c1, lp_c2, lp_c3]:
                                        is_lp_hit = True
                                        
                                is_double_fail = False
                                
                                if len(prev_stable_races) >= 2:
                                    lp2 = prev_stable_races.iloc[-2]
                                    lp2_v = lp2['場所']
                                    lp2_r = lp2['Ｒ']
                                    lp2_c1 = st.session_state['saved_chaku'].get(f"c1_{lp2_v}_{lp2_r}")
                                    lp2_c2 = st.session_state['saved_chaku'].get(f"c2_{lp2_v}_{lp2_r}")
                                    lp2_c3 = st.session_state['saved_chaku'].get(f"c3_{lp2_v}_{lp2_r}")
                                    
                                    if lp2_c1 is not None and lp_c1 is not None:
                                        lp2_num = int(lp2['馬番'])
                                        is_lp2_sym = False
                                        if (lp2.get('is_symmetry', 0.0) == 1.0) or (lp2.get('next_to_symmetry', 0.0) == 1.0):
                                            is_lp2_sym = True
                                            
                                        is_lp2_hit = False
                                        if is_lp2_sym: 
                                            if (lp2_num in [lp2_c1, lp2_c2, lp2_c3]) or ((lp2_num + 1) in [lp2_c1, lp2_c2, lp2_c3]) or ((lp2_num - 1) in [lp2_c1, lp2_c2, lp2_c3]):
                                                is_lp2_hit = True
                                        else: 
                                            if lp2_num in [lp2_c1, lp2_c2, lp2_c3]:
                                                is_lp2_hit = True
                                                
                                        if not is_lp_hit and not is_lp2_hit: 
                                            is_double_fail = True
                                            
                                if is_double_fail: 
                                    df.at[idx, '調教師判定'] = "◆ 共に凡走(紐警戒)"
                                elif is_lp_hit: 
                                    df.at[idx, '調教師判定'] = "✖ 好走済みで後のレースは狙えない"
                                else:
                                    if len(yellow_races) >= 3 and row['調教師_厩舎同ペア']: 
                                        df.at[idx, '調教師判定'] = "〇 狙いたいケース(前走凡走・騎手厩舎同一ペア)"
                                    else: 
                                        df.at[idx, '調教師判定'] = "〇 狙いたいケース(前走凡走)"

            # 馬主判定
            if '馬主(最新/仮想)' in row and (row.get('馬主(最新/仮想)_青塗', False) or row.get('馬主(最新/仮想)_黄塗', False)):
                owner_name = row['馬主(最新/仮想)']
                owner_races = df[df['馬主(最新/仮想)'] == owner_name].sort_values(by='Ｒ')
                prev_owner_races = owner_races[(owner_races['場所'] == my_venue) & (owner_races['Ｒ'] < r_num)]
                
                if row.get('馬主(最新/仮想)_青塗', False):
                    first_blue_race = owner_races.iloc[0]['Ｒ']
                    curr_num = int(row['馬番'])
                    neighbors = df[(df['場所'] == my_venue) & (df['Ｒ'] == r_num) & (df['馬番'].isin([curr_num - 1, curr_num + 1]))]
                    my_pop = int(row['人気']) if pd.notnull(row['人気']) else 99
                    
                    neighbor_has_pair = False
                    for _, n_row in neighbors.iterrows():
                        if n_row.get('馬主(最新/仮想)_黄塗', False):
                            neighbor_has_pair = True
                            
                    is_my_pop_top = False
                    for _, n_row in neighbors.iterrows():
                        n_pop = int(n_row['人気']) if pd.notnull(n_row['人気']) else 99
                        if my_pop < n_pop:
                            is_my_pop_top = True
                            
                    if r_num == first_blue_race:
                        if is_my_pop_top: 
                            df.at[idx, '馬主判定'] = "▲ 青塗1鞍目先買いリスク"
                        elif neighbor_has_pair: 
                            df.at[idx, '馬主判定'] = "△ 青塗隣馬ペアあり(オッズ49.9倍以下)"
                        else: 
                            df.at[idx, '馬主判定'] = "✖ 青塗先買いリスク(見送り)"
                    else:
                        if prev_owner_races.empty: 
                            df.at[idx, '馬主判定'] = "▲ 青塗先買い(前走未確定)"
                        else:
                            last_prev = prev_owner_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '馬主判定'] = "▲ 青塗先買い(前走未確定)"
                            else:
                                is_lp_hit = False
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]:
                                    is_lp_hit = True
                                    
                                if is_lp_hit: 
                                    df.at[idx, '馬主判定'] = "✖ 青塗見送り(好走済)"
                                else: 
                                    df.at[idx, '馬主判定'] = "〇 青塗凡走後狙い(絶好の狙い目)"
                                    
                elif row.get('馬主(最新/仮想)_黄塗', False):
                    yellow_races = owner_races[(owner_races['馬主(最新/仮想)_黄塗']) & (owner_races['場所'] == my_venue)].sort_values(by='Ｒ')
                    if not has_previous_pair_race(row.get('馬主(最新/仮想)_ペア', ''), r_num, my_venue): 
                        df.at[idx, '馬主判定'] = "▲ 先買いリスクあり"
                    else:
                        if prev_owner_races.empty: 
                            df.at[idx, '馬主判定'] = "▲ 黄色前走未確定"
                        else:
                            last_prev = prev_owner_races.iloc[-1]
                            lp_v = last_prev['場所']
                            lp_r = last_prev['Ｒ']
                            lp_c1 = st.session_state['saved_chaku'].get(f"c1_{lp_v}_{lp_r}")
                            lp_c2 = st.session_state['saved_chaku'].get(f"c2_{lp_v}_{lp_r}")
                            lp_c3 = st.session_state['saved_chaku'].get(f"c3_{lp_v}_{lp_r}")
                            
                            if lp_c1 is None and lp_c2 is None and lp_c3 is None: 
                                df.at[idx, '馬主判定'] = "▲ 黄色前走未確定"
                            else:
                                is_lp_hit = False
                                if int(last_prev['馬番']) in [lp_c1, lp_c2, lp_c3]:
                                    is_lp_hit = True
                                    
                                is_double_fail = False
                                
                                if len(prev_owner_races) >= 2:
                                    lp2 = prev_owner_races.iloc[-2]
                                    lp2_v = lp2['場所']
                                    lp2_r = lp2['Ｒ']
                                    lp2_c1 = st.session_state['saved_chaku'].get(f"c1_{lp2_v}_{lp2_r}")
                                    lp2_c2 = st.session_state['saved_chaku'].get(f"c2_{lp2_v}_{lp2_r}")
                                    lp2_c3 = st.session_state['saved_chaku'].get(f"c3_{lp2_v}_{lp2_r}")
                                    
                                    if lp2_c1 is not None and lp_c1 is not None:
                                        is_lp2_hit = False
                                        if int(lp2['馬番']) in [lp2_c1, lp2_c2, lp2_c3]:
                                            is_lp2_hit = True
                                            
                                        if not is_lp_hit and not is_lp2_hit: 
                                            is_double_fail = True
                                            
                                if is_double_fail: 
                                    df.at[idx, '馬主判定'] = "◆ 共に凡走(紐警戒)"
                                elif is_lp_hit: 
                                    df.at[idx, '馬主判定'] = "✖ 好走済みで後のレースは狙えない"
                                else:
                                    if len(yellow_races) >= 3: 
                                        df.at[idx, '馬主判定'] = "〇 狙いたいケース(前走凡走)"
                                    else: 
                                        df.at[idx, '馬主判定'] = "〇 狙いたいケース(前走凡走)"
                                    
            for col_rec in ['騎手判定', '調教師判定', '馬主判定']:
                if col_rec in df.columns:
                    current_rec = df.at[idx, col_rec]
                    if odds_val >= 50.0 and ("〇" in current_rec or "△" in current_rec or "◆" in current_rec):
                        df.at[idx, col_rec] = "△ 狙い(大穴50倍以上)"

        df['配置ポイント'] = df.apply(calculate_placement_points, axis=1)
        
        st.session_state['fully_processed_df'] = df
        st.session_state['last_processed_key'] = current_combo_key
        st.session_state['partner_cache'] = {}
        st.session_state['user_markers'] = {}
        st.session_state['saved_chaku'] = {}

# =========================================================================
# UIの描画処理（キャッシュデータのみを使用）
# =========================================================================
curr_df = st.session_state.get('fully_processed_df', pd.DataFrame())
owabi_riders = st.session_state.get('cached_owabi_riders', set())

if not curr_df.empty:
    venue_list = sorted(curr_df['場所'].unique())
    
    selected_venue = selected_venue_name  # サイドバーで選択済み
    st.session_state["selected_venue"] = selected_venue

    st.sidebar.markdown("### 🔄 オッズ更新")
    if not all_entries_df.empty:
        col_o1, col_o2 = st.sidebar.columns(2)
        with col_o1:
            if st.button("📡 DBからオッズ同期", use_container_width=True, key="odds_db_sync"):
                with st.spinner("DBからオッズ取得中..."):
                    try:
                        odds_df = get_latest_odds_from_db(target_date_str, selected_venue_code)
                    except Exception as _e:
                        st.sidebar.error(f"❌ DBエラー: {_e}")
                        odds_df = pd.DataFrame()
                if not odds_df.empty:
                    for _, o_row in odds_df.iterrows():
                        mask = (
                            (st.session_state["fully_processed_df"]["日付S"] == target_date_str) &
                            (st.session_state["fully_processed_df"]["Ｒ"].astype(int) == int(o_row["race_num"])) &
                            (st.session_state["fully_processed_df"]["馬番"].astype(int) == int(o_row["umaban"]))
                        )
                        try:
                            odds_val = round(float(str(o_row["odds_val"]).strip()), 1)
                        except (ValueError, TypeError):
                            odds_val = float('nan')
                        st.session_state["fully_processed_df"].loc[mask, "オッズ"] = odds_val
                        try:
                            ninki_val = int(float(str(o_row["ninki_val"]).strip()))
                        except (ValueError, TypeError):
                            ninki_val = pd.NA
                        st.session_state["fully_processed_df"].loc[mask, "人気"] = ninki_val
                    st.session_state["fully_processed_df"]["temp_odds"] = (
                        st.session_state["fully_processed_df"]["オッズ"].fillna(999.0))
                    st.sidebar.success("✅ DBオッズ同期完了（表示に反映されました）")
                else:
                    st.sidebar.warning("⚠️ DBにオッズデータが見つかりません")
                    st.sidebar.info(f"DEBUG: date={target_date_str}, venue={selected_venue_code}")
        with col_o2:
            if st.button("🌐 Webスクレイプ", use_container_width=True, key="odds_web_scrape"):
                progress_bar = st.sidebar.progress(0)
                status_text = st.sidebar.empty()
                r_list = sorted(all_entries_df["Ｒ"].unique())
                total_steps = len(r_list)
                success_count = 0
                for step, r_num in enumerate(r_list, 1):
                    progress_bar.progress(min(step / total_steps, 1.0))
                    status_text.text(f"取得中: {selected_venue_name} {r_num}R")
                    scraped_odds = scrape_yahoo_odds_jra(target_date_str, selected_venue_name, r_num)
                    if scraped_odds:
                        success_count += 1
                        for h_num, odd_val in scraped_odds.items():
                            mask = (
                                (st.session_state["fully_processed_df"]["場所"] == selected_venue_name) &
                                (st.session_state["fully_processed_df"]["Ｒ"] == r_num) &
                                (st.session_state["fully_processed_df"]["馬番"] == h_num)
                            )
                            st.session_state["fully_processed_df"].loc[mask, "オッズ"] = odd_val
                            st.session_state["fully_processed_df"].loc[mask, "temp_odds"] = (
                                float(odd_val) if odd_val != "---" else 999.0)
                    time.sleep(0.3)
                progress_bar.empty()
                status_text.empty()
                if success_count > 0:
                    st.sidebar.success(f"オッズ同期完了 ({success_count}/{total_steps}レース)")
                else:
                    st.sidebar.error("オッズの取得に失敗しました")
                st.rerun()

        auto_refresh = st.sidebar.toggle("⏱️ オッズ自動更新（1分）", value=False, key="auto_refresh_odds")
        if auto_refresh:
            st.sidebar.caption(f"最終更新: {pd.Timestamp.now().strftime('%H:%M:%S')}")
            time.sleep(60)
            get_latest_odds_from_db.clear()
            st.rerun()

    if owabi_riders: 
        st.info(f"前日から引き継いだ【お詫び好走候補の騎手】: {', '.join(owabi_riders)}")
        
    st.subheader("🎯 配置・カラー・能力判定結果")

    filtered_df_venue = curr_df[curr_df['場所'] == selected_venue].copy()
    race_list = sorted(filtered_df_venue['Ｒ'].unique())

    tab_list = [f"{r}R" for r in race_list] + ["🎯 予想印まとめ", "📊 黄金比能力比較", "📊 本日の集計"]
    st.markdown("### 🔍 表示メニュー選択")
    
    # 【改修】key='current_tab' で session_state と直結 → 1回の選択で即座に反映
    if 'current_tab' not in st.session_state or st.session_state['current_tab'] not in tab_list:
        st.session_state['current_tab'] = tab_list[0]

    # rerun後もタブ位置を維持: key='current_tab' で session_state 管理
    selected_tab = st.radio(
        "メニュー",
        tab_list,
        key='current_tab',
        horizontal=True,
        label_visibility="collapsed"
    )
    st.markdown("---")
    
    def update_chaku(key_name):
        st.session_state['saved_chaku'][key_name] = st.session_state[key_name]
        st.session_state['partner_cache'] = {}

    if selected_tab.endswith("R"):
        r_num = int(selected_tab.replace("R", ""))
        display_df = filtered_df_venue[filtered_df_venue['Ｒ'] == r_num].copy()
        display_df = display_df.sort_values(by='馬番')

        # 印状況の集計と表示
        marks_dict = {"◎": [], "○": [], "▲": [], "△": [], "☆": [], "✖": []}
        for k, v in st.session_state['user_markers'].items():
            if k.startswith(f"{selected_venue}_{r_num}_") and v in marks_dict:
                marks_dict[v].append(int(k.split('_')[-1]))
                
        for m in marks_dict: marks_dict[m].sort()
        mark_colors = {"◎": "#FF5722", "○": "#2196F3", "▲": "#4CAF50", "△": "#FFC107", "☆": "#9C27B0", "✖": "#757575"}
        
        mark_html_parts = []
        for m in ["◎", "○", "▲", "△", "☆", "✖"]:
            nums_str = ",".join(map(str, marks_dict[m])) if marks_dict[m] else "なし"
            mark_html_parts.append(f"<span style='margin-right:15px; font-size:15px;'><strong style='color:{mark_colors[m]}; font-size:18px;'>{m}</strong>: {nums_str}</span>")
            
        st.markdown(f"<div style='background-color:#FFFFFF; padding:10px 15px; border-radius:8px; border:2px solid #E0E0E0; margin-bottom:15px;'>{''.join(mark_html_parts)}</div>", unsafe_allow_html=True)

        st.markdown("##### 🏁 確定着順（1〜3着）の入力")
        col_c1, col_c2, col_c3 = st.columns(3)
        horse_options = [None] + sorted(list(display_df['馬番'].astype(int).unique()))
        
        with col_c1:
            k1 = f"c1_{selected_venue}_{r_num}"
            v1 = st.session_state['saved_chaku'].get(k1)
            chaku_1 = st.selectbox("1着の馬番", horse_options, index=horse_options.index(v1) if v1 in horse_options else 0, key=k1, on_change=update_chaku, args=(k1,), format_func=lambda x: f"{x}番" if x is not None else "未確定")
        with col_c2:
            k2 = f"c2_{selected_venue}_{r_num}"
            v2 = st.session_state['saved_chaku'].get(k2)
            chaku_2 = st.selectbox("2着の馬番", horse_options, index=horse_options.index(v2) if v2 in horse_options else 0, key=k2, on_change=update_chaku, args=(k2,), format_func=lambda x: f"{x}番" if x is not None else "未確定")
        with col_c3:
            k3 = f"c3_{selected_venue}_{r_num}"
            v3 = st.session_state['saved_chaku'].get(k3)
            chaku_3 = st.selectbox("3着の馬番", horse_options, index=horse_options.index(v3) if v3 in horse_options else 0, key=k3, on_change=update_chaku, args=(k3,), format_func=lambda x: f"{x}番" if x is not None else "未確定")
        
        st.markdown("---")
        
        # 🌟 ここから【横スクロール・予想ボード表示】 🌟
        unrated, core, secondary, ignored = [], [], [], []
        
        for idx, row in display_df.iterrows():
            num_val = int(row['馬番'])
            h_key = f"{selected_venue}_{r_num}_{num_val}"
            m = st.session_state['user_markers'].get(h_key, "未設定")
            
            if m == "未設定": unrated.append(row)
            elif m in ["◎", "○", "▲"]: core.append(row)
            elif m in ["△", "☆"]: secondary.append(row)
            elif m == "✖": ignored.append(row)
            
        st.markdown("### 🎯 直感予想ボード")
        st.markdown("◀ カードを横にスワイプ（スクロール）して仕分けを行ってください。")
        
        # 未評価レーン
        st.markdown(f"#### 📝 1. 未評価 `{len(unrated)}頭`")
        if unrated:
            render_horse_cards_carousel(unrated, selected_venue, curr_df, block_key="unrated")
        else:
            st.info("すべての馬の仕分けが完了しました！")
            
        st.markdown("<hr style='margin: 1em 0; border: none; border-bottom: 1px solid #ccc;'/>", unsafe_allow_html=True)
        
        # 軸・本命レーン
        st.markdown(f"#### 🎯 2. 軸・本命候補 (◎ ○ ▲) `{len(core)}頭`")
        if core:
            render_horse_cards_carousel(core, selected_venue, curr_df, block_key="core")
        else:
            st.info("軸・本命候補はいません。")
            
        st.markdown("<hr style='margin: 1em 0; border: none; border-bottom: 1px solid #ccc;'/>", unsafe_allow_html=True)
        
        # ヒモ・穴レーン
        st.markdown(f"#### ⚡ 3. ヒモ・穴候補 (△ ☆) `{len(secondary)}頭`")
        if secondary:
            render_horse_cards_carousel(secondary, selected_venue, curr_df, block_key="secondary")
        else:
            st.info("ヒモ・穴候補はいません。")

        # 消しレーン（折りたたみ）
        st.markdown("---")
        with st.expander(f"🔽 4. 消した馬を表示する (✖) `{len(ignored)}頭`"):
            if ignored:
                render_horse_cards_carousel(ignored, selected_venue, curr_df, block_key="ignored")
            else:
                st.info("消した馬はいません。")
                        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 このレースの手動攻略マークをすべてクリアする", use_container_width=True, key=f"clear_markers_{selected_venue}_{r_num}"):
            keys_to_clear = [k for k in st.session_state['user_markers'].keys() if k.startswith(f"{selected_venue}_{r_num}_")]
            for k in keys_to_clear: del st.session_state['user_markers'][k]
            st.rerun()

    # 🌟 ここから【予想印まとめタブ】 🌟
    elif selected_tab == "🎯 予想印まとめ":
        st.markdown(f"### 🎯 予想印まとめ ({selected_venue})")
        st.markdown("本日印をつけた馬の最終確認リストです。")
        
        has_any = False
        for r_num in sorted(curr_df[curr_df['場所'] == selected_venue]['Ｒ'].unique()):
            race_df = curr_df[(curr_df['場所'] == selected_venue) & (curr_df['Ｒ'] == r_num)].sort_values('馬番')
            
            marked_rows = []
            for idx, row in race_df.iterrows():
                num_val = int(row['馬番'])
                h_key = f"{selected_venue}_{r_num}_{num_val}"
                m = st.session_state['user_markers'].get(h_key, "未設定")
                if m not in ["未設定", "✖"]:
                    marked_rows.append((row, m))
                    
            if marked_rows:
                has_any = True
                sort_order = {"◎":0, "○":1, "▲":2, "△":3, "☆":4}
                marked_rows.sort(key=lambda x: sort_order.get(x[1], 99))
                
                st.markdown(f"#### 🏁 {r_num}R の買い目候補")
                
                # ここも横スクロールで統一
                rows_to_render = [item[0] for item in marked_rows]
                render_horse_cards_carousel(rows_to_render, selected_venue, curr_df, block_key="marked")
                st.markdown("<hr style='margin: 1em 0; border: none; border-bottom: 1px dashed #ccc;'/>", unsafe_allow_html=True)
                
        if not has_any:
            st.info("💡 まだ印（◎○▲△☆）をつけた馬がいません。各レースから馬を選択してください。")

    elif selected_tab == "📊 黄金比能力比較":
        st.markdown(f"### 📊 黄金比バランス指数・調教比較 ({selected_venue})")
        st.markdown("前走の相手関係、タイム差（着差）、脚質データ、ローテーション、長期休養フラグに加え、坂路調教の4Fタイムおよび終いラップ評価の一覧です。")
        
        perf_display_df = filtered_df_venue.copy()
        if '総合指数' in perf_display_df.columns:
            perf_display_df = perf_display_df.sort_values(by=['Ｒ', '総合指数'], ascending=[True, False]).reset_index(drop=True)
            
            def highlight_perf_only(row):
                styles = [''] * len(row)
                cols = list(row.index)
                if '総合指数' in cols:
                    idx_idx = cols.index('総合指数')
                    try:
                        val_f = float(row['総合指数'])
                        if val_f >= 100.0: styles[idx_idx] = 'background-color: #ffcccc; color: red; font-weight: bold;'
                        elif val_f >= 80.0: styles[idx_idx] = 'background-color: #fff2cc; font-weight: bold;'
                    except: pass
                
                return styles
                
            perf_cols = ['Ｒ', '馬番', '馬名', '総合指数', '長期休養フラグ', 'ラップ評価', '4Fタイム', 'Lap4', 'Lap3', 'Lap2', 'ラスト1F', 'レベル点', '自力点', 'ボーナス減点', '前走着順', '前走着差', '前走脚質', 'レース間隔', '好走/次走あり', '前走日付']
            perf_cols_exist = [c for c in perf_cols if c in perf_display_df.columns]
            
            col_config_integrated = {
                "Ｒ": st.column_config.NumberColumn("レース", format="%d R"), 
                "馬番": st.column_config.NumberColumn("馬番", format="%d"),
                "総合指数": st.column_config.NumberColumn("★ 総合指数", format="%.1f"), 
                "レベル点": st.column_config.NumberColumn("相手レベル点", format="%.1f 点"),
                "自力点": st.column_config.NumberColumn("自力点", format="%.1f 点"), 
                "ボーナス減点": st.column_config.NumberColumn("加減点", format="%.1f 点"),
                "4Fタイム": st.column_config.NumberColumn("坂路4F", format="%.1f")
            }
            
            st.dataframe(
                perf_display_df[perf_cols_exist].style.apply(highlight_perf_only, axis=1), 
                column_config=col_config_integrated, 
                use_container_width=True, 
                hide_index=True, 
                height=600
            )
        else:
            st.warning("能力比較データベースが読み込まれていないか、有効な指数データがありません。")

    elif selected_tab == "📊 本日の集計":
        st.markdown(f"### 📈 本日の成績集計 ({selected_venue})")
        st.markdown("※着順が入力されたレースのみ集計されます。")
        
        finished_races = sum(1 for r in race_list if st.session_state['saved_chaku'].get(f"c1_{selected_venue}_{r}") is not None)
        
        if finished_races > 0:
            summary_df = filtered_df_venue.copy()
            summary_df['actual_rank'] = np.nan
            
            for idx, row in summary_df.iterrows():
                r = row['Ｒ']
                num = int(row['馬番'])
                
                c1 = st.session_state['saved_chaku'].get(f"c1_{selected_venue}_{r}")
                c2 = st.session_state['saved_chaku'].get(f"c2_{selected_venue}_{r}")
                c3 = st.session_state['saved_chaku'].get(f"c3_{selected_venue}_{r}")
                
                if c1 is not None or c2 is not None or c3 is not None:
                    if num == c1: summary_df.at[idx, 'actual_rank'] = 1
                    elif num == c2: summary_df.at[idx, 'actual_rank'] = 2
                    elif num == c3: summary_df.at[idx, 'actual_rank'] = 3
                    else: summary_df.at[idx, 'actual_rank'] = 10
            
            def get_stats(mask):
                target = summary_df[mask & summary_df['actual_rank'].notnull()]
                total = len(target)
                if total == 0: 
                    return 0, 0.0, 0.0, 0.0
                wins = len(target[target['actual_rank'] == 1])
                top3s = len(target[target['actual_rank'] <= 3])
                ret = target[target['actual_rank'] == 1]['temp_odds'].sum() * 100
                return total, wins/total, top3s/total, ret/(total*100)
            
            stats_data = []
            
            mask_nerai = summary_df['騎手判定'].str.contains('〇') | summary_df['調教師判定'].str.contains('〇')
            if '馬主判定' in summary_df.columns: 
                mask_nerai |= summary_df['馬主判定'].str.contains('〇')
            t, w, p, r = get_stats(mask_nerai)
            stats_data.append({"カテゴリ": "🎯 〇 狙い目", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            mask_saki = summary_df['騎手判定'].str.contains('▲') | summary_df['調教師判定'].str.contains('▲')
            if '馬主判定' in summary_df.columns: 
                mask_saki |= summary_df['馬主判定'].str.contains('▲')
            t, w, p, r = get_stats(mask_saki)
            stats_data.append({"カテゴリ": "⚠️ ▲ 先買い", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            mask_oana = summary_df['騎手判定'].str.contains('△') | summary_df['調教師判定'].str.contains('△')
            if '馬主判定' in summary_df.columns: 
                mask_oana |= summary_df['馬主判定'].str.contains('△')
            t, w, p, r = get_stats(mask_oana)
            stats_data.append({"カテゴリ": "⚡ △ 大穴/隣ペア", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            mask_himo = summary_df['騎手判定'].str.contains('◆') | summary_df['調教師判定'].str.contains('◆')
            if '馬主判定' in summary_df.columns: 
                mask_himo |= summary_df['馬主判定'].str.contains('◆')
            t, w, p, r = get_stats(mask_himo)
            stats_data.append({"カテゴリ": "🔗 ◆ 紐警戒", "該当数": t, "勝率": w, "複勝率": p, "単勝回収率": r})
            
            stats_df = pd.DataFrame(stats_data)
            stats_df['勝率'] = stats_df['勝率'].apply(lambda x: f"{x:.1%}")
            stats_df['複勝率'] = stats_df['複勝率'].apply(lambda x: f"{x:.1%}")
            stats_df['単勝回収率'] = stats_df['単勝回収率'].apply(lambda x: f"{x:.0%}")
            
            st.write(f"**集計対象レース数：{finished_races} レース**")
            st.table(stats_df.set_index("カテゴリ"))
        else:
            st.info("レースの着順を入力すると、ここに本日の成績集計が表示されます。")

    st.markdown("""
    **💡 色と推奨記号のルール（PDF 6P・7Pフローチャート準拠）:**
    * <span style="background-color: #FDFFB6; padding: 2px 5px; color: black;">黄塗</span> : 連続する出走機会で配置が一致。
    * <span style="background-color: #A0C4FF; padding: 2px 5px; color: black;">青塗</span> : その日のすべての出走レースで全く同じ配置の数値に固定。
    * <span style="background-color: #A0C4FF; padding: 2px 5px; color: black; border: 1px solid blue;">馬名欄の青塗</span> : 前日「青塗」で一度も3着以内に絡めなかった騎手の「お詫び好走候補」。
    * <span style="background-color: #FFADAD; padding: 2px 5px; color: black; font-weight: bold;">〇 狙い目</span> : 青塗・ペア馬の「前走凡走後（絶好の狙い目）」。
    * <span style="background-color: #FFD6A5; padding: 2px 5px; color: black;">▲ 先買い</span> : 最初のレース（1鞍目・1回目）のため、前走の結果を待てない状態（先買いリスク）。
    * <span style="background-color: #FDFFB6; padding: 2px 5px; color: black;">△ 狙い</span> : 隣の馬にペアがある、または想定単勝オッズ50.0倍以上の大穴に該当.
    * <span style="background-color: #FDFFB6; padding: 2px 5px; color: black;">△ 後続ペア継続</span> : 前走で好走しているが、後続のレースにもペアが控えているため完全には消せない馬。
    * <span style="background-color: #CAFFBF; padding: 2px 5px; color: black; font-weight: bold;">◆ 共に凡走(紐警戒)</span> : ペア内で前走・前々走ともに凡走(4着以下)が続いている馬。紐(2・3着候補)への推奨。
    * <span style="background-color: #E2E2E2; padding: 2px 5px; color: gray;">✖ 見送り</span> : 直前のレースですでに好走（1〜3着）を終えているため、狙いから外す馬.
    * <span style="background-color: #E1BEE7; padding: 2px 5px; color: black; font-weight: bold;">🔄 对称</span> : 同一レース内で同じ調教師の馬が対称配置（正番と逆番が一致）になっている馬。
    * <span style="background-color: #F3E5F5; padding: 2px 5px; color: black;">↔️ 隣馬(対称)</span> : 对称配置になっている馬の隣の馬。
    * <span style="background-color: #E3F2FD; padding: 2px 5px; color: black;">🟦 青塗隣馬</span> : 青塗馬の隣の隣。
    """, unsafe_allow_html=True)

    # -------------------------------------------------------------------------
    # 翌日用CSVのダウンロード機能
    # -------------------------------------------------------------------------
    st.write("---")
    st.subheader("💾 翌日用結果CSVの保存")
    st.markdown("""
    各レースの「1着の馬番」「2着の馬番」「3着の馬番」に入力した結果をファイルに保存し、翌日の「前日の結果CSV」としてそのまま使用できます。
    ※当日の**全競馬場・全レース**に入力した着順が1つにまとめて反映されます（1〜3着以外は10として出力されます）。
    """)

    save_df = curr_df.copy()
    save_df['着順'] = ""

    save_df['_save_umaban_int'] = pd.to_numeric(save_df['馬番'], errors='coerce').fillna(-999).astype(int)
    save_df['_save_r_int'] = pd.to_numeric(save_df['Ｒ'], errors='coerce').fillna(-999).astype(int)

    for v in save_df['場所'].unique():
        r_list_int = sorted(save_df[save_df['場所'] == v]['_save_r_int'].unique())
        
        for r_int in r_list_int:
            if r_int == -999: 
                continue
                
            c1_val = st.session_state['saved_chaku'].get(f"c1_{v}_{r_int}")
            c2_val = st.session_state['saved_chaku'].get(f"c2_{v}_{r_int}")
            c3_val = st.session_state['saved_chaku'].get(f"c3_{v}_{r_int}")
            
            if (c1_val is not None) or (c2_val is not None) or (c3_val is not None):
                mask_race = (save_df['場所'] == v) & (save_df['_save_r_int'] == r_int)
                save_df.loc[mask_race, '着順'] = "10"
                
                if c1_val is not None: 
                    save_df.loc[mask_race & (save_df['_save_umaban_int'] == int(c1_val)), '着順'] = "1"
                if c2_val is not None: 
                    save_df.loc[mask_race & (save_df['_save_umaban_int'] == int(c2_val)), '着順'] = "2"
                if c3_val is not None: 
                    save_df.loc[mask_race & (save_df['_save_umaban_int'] == int(c3_val)), '着順'] = "3"

    save_df = save_df.drop(columns=['_save_umaban_int', '_save_r_int'])
    base_cols = ['日付S', '場所', 'Ｒ', '馬番', '馬名', '騎手', '調教師', '馬主(最新/仮想)', 'オッズ', '人気', '着順']
    save_cols = [c for c in base_cols if c in save_df.columns]
    
    csv_data = save_df[save_cols].to_csv(index=False).encode('utf-8-sig')
    
    file_prefix = curr_df['日付S'].iloc[0] if not curr_df.empty else 'output'
    st.download_button(
        label="🏆 着順入力を反映したCSVをダウンロード",
        data=csv_data,
        file_name=f"result_with_chaku_{file_prefix}.csv",
        mime="text/csv",
        use_container_width=True
    )