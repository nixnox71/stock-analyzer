import io
import os
import re
import json
import zipfile
from datetime import datetime, timedelta
from urllib.parse import quote
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import streamlit as st
import FinanceDataReader as fdr
import plotly.graph_objects as go
from plotly.subplots import make_subplots

APP_TITLE = "국내 주식/ETF 종합 분석기"
DART_BASE = "https://opendart.fss.or.kr/api"
FAV_FILE = "favorites.json"

st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")

# ==========================================
# 세션 상태(Session State) 초기화
# ==========================================
if "current_menu" not in st.session_state:
    st.session_state.current_menu = "1. 종합 주식/ETF 분석"
if "target_stock" not in st.session_state:
    st.session_state.target_stock = ""

if "favorites" not in st.session_state:
    if os.path.exists(FAV_FILE):
        try:
            with open(FAV_FILE, "r") as f:
                st.session_state["favorites"] = json.load(f)
        except:
            st.session_state["favorites"] = []
    else:
        st.session_state["favorites"] = []

def save_favorites():
    with open(FAV_FILE, "w") as f:
        json.dump(st.session_state["favorites"], f)

# 사이드바 메뉴 설정
menu_options = [
    "1. 종합 주식/ETF 분석", 
    "2. ⭐ 관심 종목 (즐겨찾기)", 
    "3. 🔥 시가총액 상위 20 수급 동향",
    "4. 🚀 당일 상승률 상위 20",
    "5. 💡 AI 추천 종목 스크리닝",
    "6. 📉 HTS급 보조지표 차트"
]

with st.sidebar:
    st.header("🛠️ 분석 메뉴")
    selected_idx = menu_options.index(st.session_state.current_menu) if st.session_state.current_menu in menu_options else 0
    menu = st.radio("이동할 메뉴를 선택하세요", menu_options, index=selected_idx)
    
    if menu != st.session_state.current_menu:
        st.session_state.current_menu = menu
        st.session_state.target_stock = "" 
        st.rerun()
        
    st.divider()
    st.header("⚙️ 설정")
    dart_key = st.text_input(
        "OpenDART API 키 (선택)",
        value=os.getenv("DART_API_KEY", ""),
        type="password",
        help="공시 재무제표 및 최근 공시 원문을 보려면 OpenDART API 키를 입력하세요."
    )
    st.caption("데이터 출처: FinanceDataReader, Naver API, DART")
    st.divider()
    st.markdown("**주의:** 본 프로그램은 정보 제공용이며 투자 권유가 아닙니다.")

# ==========================================
# 공통 데이터 로드 함수
# ==========================================

@st.cache_data(ttl=86400, show_spinner=False)
def load_listings():
    frames = []
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df = fdr.StockListing(market)
            if not df.empty:
                df["시장"] = market
                frames.append(df)
        except: pass
    try:
        etf = fdr.StockListing("ETF/KR")
        if not etf.empty:
            etf["시장"] = "ETF"
            if "Symbol" in etf.columns:
                etf.rename(columns={"Symbol": "Code"}, inplace=True)
            frames.append(etf)
    except: pass
    
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["Code"], keep="first")
    out["Code"] = out["Code"].astype(str).str.zfill(6)
    return out

@st.cache_data(ttl=3600, show_spinner=False)
def load_price(code, start, end):
    try:
        return fdr.DataReader(code, start, end)
    except:
        return pd.DataFrame()

@st.cache_data(ttl=600, show_spinner=False)
def get_investor_trends_detail(code):
    url = f"https://m.stock.naver.com/api/stock/{code}/trend"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            items = data if isinstance(data, list) else []
            if not items and isinstance(data, dict):
                for k in ["message", "result", "items", "trend"]:
                    if k in data and isinstance(data[k], list):
                        items = data[k]
                        break
            if items:
                df = pd.DataFrame(items)
                inst_col = next((c for c in df.columns if any(x in c.lower() for x in ["organ", "inst"])), None)
                fore_col = next((c for c in df.columns if any(x in c.lower() for x in ["frgn", "foreigner"])), None)
                if inst_col and fore_col:
                    df[inst_col] = pd.to_numeric(df[inst_col].astype(str).str.replace(r'[^\d\-]', '', regex=True), errors="coerce").fillna(0)
                    df[fore_col] = pd.to_numeric(df[fore_col].astype(str).str.replace(r'[^\d\-]', '', regex=True), errors="coerce").fillna(0)
                    return {"기관_1일": df[inst_col].iloc[0] if len(df) > 0 else 0, "외국인_1일": df[fore_col].iloc[0] if len(df) > 0 else 0, "기관_5일": df[inst_col].head(5).sum() if len(df) > 0 else 0, "외국인_5일": df[fore_col].head(5).sum() if len(df) > 0 else 0}
    except: pass
    return {"기관_1일": 0, "외국인_1일": 0, "기관_5일": 0, "외국인_5일": 0}

@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_corp_codes(api_key):
    url = f"{DART_BASE}/corpCode.xml?crtfc_key={api_key.strip()}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and r.content.startswith(b'PK'):
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                xml_name = next((n for n in z.namelist() if n.lower().endswith(".xml")), None)
                if xml_name:
                    root = ET.fromstring(z.read(xml_name))
                    return pd.DataFrame([{"corp_code": (item.findtext("corp_code") or "").strip(), "stock_code": (item.findtext("stock_code") or "").strip()} for item in root.findall("list")])
    except: pass
    return None

def load_corp_codes(api_key):
    if not api_key: return pd.DataFrame()
    df = _fetch_corp_codes(api_key)
    if df is None or df.empty:
        _fetch_corp_codes.clear() 
        return pd.DataFrame()
    return df

def get_recent_disclosures(code, api_key):
    if not api_key: return None, "OpenDART API 키를 입력하세요."
    corp_df = load_corp_codes(api_key)
    if corp_df.empty: return None, "DART 기업코드 통신 지연. 잠시 후 다시 시도해 주세요."
    hit = corp_df[corp_df["stock_code"].astype(str).str.zfill(6) == str(code).zfill(6)]
    if hit.empty: return None, "DART 공시 대상 기업이 아닙니다."
    
    corp_code = hit.iloc[0]["corp_code"]
    end_de = datetime.now().strftime("%Y%m%d")
    bgn_de = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    try:
        j = requests.get(f"{DART_BASE}/list.json", params={"crtfc_key": api_key.strip(), "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de, "page_count": "6"}, timeout=10).json()
        if j.get("status") == "000" and j.get("list"):
            df = pd.DataFrame(j["list"])
            df["link"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + df["rcept_no"]
            return df[["rcept_dt", "report_nm", "link"]], None
        return None, "최근 3개월 내 주요 공시가 없습니다."
    except: return None, "공시 서버 응답 지연"

@st.cache_data(ttl=3600, show_spinner=False)
def get_dart_financials(code, api_key):
    if not api_key: return None, "OpenDART API 키를 입력하세요."
    corp_df = load_corp_codes(api_key)
    if corp_df.empty: return None, "데이터를 불러오는 중입니다. 잠시 후 재시도하세요."
    hit = corp_df[corp_df["stock_code"].astype(str).str.zfill(6) == str(code).zfill(6)]
    if hit.empty: return None, "공시 대상이 아닙니다."
    
    corp_code = hit.iloc[0]["corp_code"]
    curr_year = datetime.now().year
    ordered_periods = []
    for y in range(curr_year, curr_year - 3, -1):
        ordered_periods.extend([(str(y), "11011", f"{y}년 연간"), (str(y), "11014", f"{y}년 3분기"), (str(y), "11012", f"{y}년 반기"), (str(y), "11013", f"{y}년 1분기")])
        
    collected_data = {}
    for y, reprt_code, col_name in ordered_periods:
        if len(collected_data) >= 3: break
        try:
            url = f"{DART_BASE}/fnlttSinglAcntAll.json"
            params = {"crtfc_key": api_key.strip(), "corp_code": corp_code, "bsns_year": y, "reprt_code": reprt_code, "fs_div": "CFS"}
            res = requests.get(url, params=params, timeout=5).json()
            if res.get("status") == "000" and res.get("list"):
                collected_data[col_name] = res["list"]
                continue
            params["fs_div"] = "OFS"
            res_ofs = requests.get(url, params=params, timeout=5).json()
            if res_ofs.get("status") == "000" and res_ofs.get("list"):
                collected_data[col_name] = res_ofs["list"]
        except: continue
            
    if not collected_data: return None, "재무제표가 접수되지 않았습니다."
        
    patterns = {"매출액": ["매출액", "수익(매출액)", "영업수익"], "영업이익": ["영업이익", "영업이익(손실)"], "당기순이익": ["당기순이익", "당기순이익(손실)"], "자산총계": ["자산총계"], "부채총계": ["부채총계"], "자본총계": ["자본총계"]}
    records = []
    col_names = list(collected_data.keys())
    for label, keywords in patterns.items():
        row_dict = {"주요 계정": label}
        for c_name in col_names:
            raw_df = pd.DataFrame(collected_data[c_name])
            found = raw_df[raw_df["account_nm"].fillna("").apply(lambda x: any(k == x.strip() or k in x for k in keywords))]
            if not found.empty:
                try: row_dict[c_name] = f"{float(str(found.iloc[0].get('thstrm_amount', '')).replace(',', '').strip()) / 100000000:,.1f} 억원"
                except: row_dict[c_name] = "-"
            else: row_dict[c_name] = "-"
        records.append(row_dict)
    return pd.DataFrame(records)[["주요 계정"] + col_names], f"최근 3개 실적: {' · '.join(col_names)}"

def get_news(company):
    query = quote(f'"{company}" 주식')
    url = f"https://news.google.com/rss/search?q={query}+after:{(datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        resp = requests.get(url, timeout=10)
        root = ET.fromstring(resp.content)
        rows = []
        for item in root.findall(".//item")[:15]:
            title = item.findtext("title") or ""
            p = sum(k in title.lower() for k in ["흑자", "상승", "급등", "수주", "계약", "호실적", "배당"])
            n = sum(k in title.lower() for k in ["적자", "하락", "급락", "소송", "횡령", "유상증자", "부진"])
            rows.append({"날짜": (item.findtext("pub