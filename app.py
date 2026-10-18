import os
import re
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
st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")

if "favorites" not in st.session_state:
    st.session_state["favorites"] = []

with st.sidebar:
    st.header("🛠️ 분석 메뉴")
    menu = st.radio(
        "이동할 메뉴를 선택하세요",
        [
            "1. 종합 주식/ETF 분석", 
            "2. ⭐ 관심 종목 (즐겨찾기)", 
            "3. 🔥 시가총액 상위 20 수급 동향",
            "4. 🚀 당일 상승률 상위 20",
            "5. 💡 AI 추천 종목 스크리닝",
            "6. 📉 기술적 보조지표 차트"
        ]
    )
    st.divider()
    st.caption("데이터 출처: FinanceDataReader, Naver API, DART")

# ==========================================
# 공통 데이터 로드 함수 (pykrx 완전 제거)
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
                    return {
                        "기관_1일": df[inst_col].iloc[0] if len(df) > 0 else 0,
                        "외국인_1일": df[fore_col].iloc[0] if len(df) > 0 else 0,
                        "기관_5일": df[inst_col].head(5).sum() if len(df) > 0 else 0,
                        "외국인_5일": df[fore_col].head(5).sum() if len(df) > 0 else 0
                    }
    except: pass
    return {"기관_1일": 0, "외국인_1일": 0, "기관_5일": 0, "외국인_5일": 0}

def won(v):
    return f"{float(v):,.0f}원" if pd.notna(v) else "자료 없음"

listings = load_listings()
if listings.empty:
    st.error("종목 목록을 불러오지 못했습니다. 인터넷 연결을 확인하세요.")
    st.stop()

# ==========================================
# 1. 종합 주식/ETF 분석
# ==========================================
if menu == "1. 종합 주식/ETF 분석":
    st.title("📈 국내 주식 및 ETF 종합 분석기")
    with st.form("search_form"):
        col1, col2 = st.columns([3, 1])
        query = col1.text_input("종목명 또는 종목코드 (ETF 포함)", placeholder="예: 삼성전자")
        submitted = col2.form_submit_button("종목 분석", use_container_width=True, type="primary")

    if not submitted and not query:
        st.stop()

    q = query.strip()
    matches = listings[listings["Code"] == q.zfill(6)] if q.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(q), case=False, na=False)]

    if matches.empty:
        st.error("종목을 찾지 못했습니다.")
        st.stop()

    code = str(matches.iloc[0]["Code"]).zfill(6)
    company = str(matches.iloc[0]["Name"])
    
    col_t1, col_t2 = st.columns([4, 1])
    col_t1.subheader(f"{company} ({code})")
    
    is_fav = code in st.session_state["favorites"]
    if col_t2.button("⭐ 관심 종목 등록" if not is_fav else "❌ 관심 해제", use_container_width=True):
        if not is_fav:
            st.session_state["favorites"].append(code)
            st.success("등록 완료!")
        else:
            st.session_state["favorites"].remove(code)
            st.info("해제 완료.")

    end = datetime.now().date() + timedelta(days=1)
    start = end - timedelta(days=370)
    prices = load_price(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    if prices is not None and not prices.empty:
        close = prices["Close"].dropna()
        c_price = float(close.iloc[-1])
        change = (c_price / float(close.iloc[0]) - 1) * 100
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("최근 종가", won(c_price))
        m2.metric("조회 구간 등락률", f'{change:+.2f}%')
        
        tr = get_investor_trends_detail(code)
        m3.metric("기관 순매수 (최근 1일)", f"{tr['기관_1일']:+,.0f} 주")
        m4.metric("외국인 순매수 (최근 1일)", f"{tr['외국인_1일']:+,.0f} 주")

        df = prices.copy()
        df["MA20"] = df["Close"].rolling(20).mean()
        df["MA60"] = df["Close"].rolling(60).mean()
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="주가"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MA20"], name="20일선"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MA60"], name="60일선"), row=1, col=1)
        if "Volume" in df.columns:
            fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="거래량"), row=2, col=1)
        fig.update_layout(height=500, title=f"{company} 차트", xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

# ==========================================
# 2. 관심 종목 (즐겨찾기)
# ==========================================
elif menu == "2. ⭐ 관심 종목 (즐겨찾기)":
    st.title("⭐ 나의 관심 종목 리스트")
    favs = st.session_state["favorites"]
    if not favs:
        st.info("등록된 관심 종목이 없습니다.")
    else:
        fav_rows = listings[listings["Code"].isin(favs)]
        cards_data = []
        for _, r in fav_rows.iterrows():
            c_code, c_name = r["Code"], r["Name"]
            p_df = load_price(c_code, (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
            if p_df is not None and not p_df.empty:
                last_c = p_df["Close"].iloc[-1]
                prev_c = p_df["Close"].iloc[-2] if len(p_df) > 1 else last_c
                chg = (last_c / prev_c - 1) * 100
                vol = p_df["Volume"].iloc[-1] if "Volume" in p_df.columns else 0
                tr = get_investor_trends_detail(c_code)
                cards_data.append({
                    "종목명": c_name, "현재가": f"{last_c:,.0f}원", "등락률": f"{chg:+.2f}%",
                    "거래량": f"{vol:,.0f}주", "외국인(당일)": f"{tr['외국인_1일']:+,.0f}주", "기관(당일)": f"{tr['기관_1일']:+,.0f}주"
                })
        if cards_data:
            st.dataframe(pd.DataFrame(cards_data), use_container_width=True, hide_index=True)

# ==========================================
# 3. 시가총액 상위 20 수급 동향 (pykrx 에러 회피를 위한 대체)
# ==========================================
elif menu == "3. 🔥 시가총액 상위 20 수급 동향":
    st.title("🔥 시장별 시가총액 상위 20종목 수급 점검")
    market_c = st.selectbox("시장 선택", ["KOSPI", "KOSDAQ"])
    
    if st.button("수급 및 시세 조회", type="primary"):
        with st.spinner("데이터 분석 중입니다..."):
            df_m = listings[listings['시장'] == market_c].copy()
            if 'Marcap' in df_m.columns:
                top20 = df_m.sort_values("Marcap", ascending=False).head(20)
            else:
                top20 = df_m.head(20)
                
            rows = []
            for i, r in top20.iterrows():
                code = r['Code']
                p_df = load_price(code, (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
                close_p = p_df['Close'].iloc[-1] if p_df is not None and not p_df.empty else 0
                tr = get_investor_trends_detail(code)
                rows.append({
                    "순위": len(rows) + 1, "종목명": r['Name'], "현재가": f"{close_p:,.0f}원",
                    "외국인(5일 합계)": f"{tr['외국인_5일']:+,.0f}주", "기관(5일 합계)": f"{tr['기관_5일']:+,.0f}주"
                })
            st.dataframe(pd.DataFrame(rows).set_index("순위"), use_container_width=True)

# ==========================================
# 4. 당일 상승률 상위 20
# ==========================================
elif menu == "4. 🚀 당일 상승률 상위 20":
    st.title("🚀 시장별 상승률 상위 20 종목")
    market_g = st.selectbox("시장 구분", ["KOSPI", "KOSDAQ"])
    
    if st.button("상승률 상위 조회", type="primary"):
        with st.spinner("시장 데이터 집계 중..."):
            df_krx = listings[listings['시장'] == market_g].copy()
            if 'ChagesRatio' in df_krx.columns:
                top20_g = df_krx.sort_values("ChagesRatio", ascending=False).head(20)
                rows_g = []
                for _, row in top20_g.iterrows():
                    code = row['Code']
                    tr = get_investor_trends_detail(code)
                    rows_g.append({
                        "순위": len(rows_g) + 1, "종목명": row['Name'], 
                        "상승률": f"{row['ChagesRatio']:+.2f}%", "종가": f"{row['Close']:,.0f}원",
                        "거래량": f"{row['Volume']:,.0f}주",
                        "외국인(당일)": f"{tr['외국인_1일']:+,.0f}주", "기관(당일)": f"{tr['기관_1일']:+,.0f}주"
                    })
                st.dataframe(pd.DataFrame(rows_g).set_index("순위"), use_container_width=True)
            else:
                st.error("현재 시간 기준 등락률 데이터를 불러올 수 없습니다.")

# ==========================================
# 5. AI 추천 종목 스크리닝
# ==========================================
elif menu == "5. 💡 AI 추천 종목 스크리닝":
    st.title("💡 조건검색: 주도주 탐색")
    if st.button("스크리닝 시작", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        df_k = listings[(listings['시장'].isin(['KOSPI', 'KOSDAQ'])) & (~listings['Name'].str.contains("KODEX|TIGER|KBSTAR", na=False))]
        if 'Volume' in df_k.columns:
            df_top = df_k.sort_values('Volume', ascending=False).head(100)
        else:
            df_top = df_k.head(100)
            
        recom_res = []
        total = len(df_top)
        
        for i, (_, row) in enumerate(df_top.iterrows()):
            c, n = row['Code'], row['Name']
            status_text.text(f"분석 중... ({n})")
            pdf = load_price(c, (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
            if pdf is not None and len(pdf) >= 20:
                close = pdf['Close'].iloc[-1]
                ma5 = pdf['Close'].rolling(5).mean().iloc[-1]
                ma20 = pdf['Close'].rolling(20).mean().iloc[-1]
                if close > ma5 and close > ma20:
                    vol = pdf['Volume'].iloc[-1]
                    prev_vol_avg = pdf['Volume'].iloc[-6:-1].mean()
                    if prev_vol_avg > 0 and vol >= (prev_vol_avg * 2.0):
                        tr = get_investor_trends_detail(c)
                        if tr['기관_1일'] > 0 or tr['외국인_1일'] > 0:
                            recom_res.append({
                                "종목명": n, "현재가": f"{close:,.0f}", "거래량": f"{vol:,.0f}주",
                                "외국인(당일)": f"{tr['외국인_1일']:+,.0f}", "기관(당일)": f"{tr['기관_1일']:+,.0f}"
                            })
            progress_bar.progress((i + 1) / total)
        status_text.empty()
        if recom_res:
            st.dataframe(pd.DataFrame(recom_res), use_container_width=True, hide_index=True)
        else:
            st.warning("조건을 완벽히 만족하는 종목이 없습니다.")

# ==========================================
# 6. 기술적 보조지표 차트
# ==========================================
elif menu == "6. 📉 기술적 보조지표 차트":
    st.title("📉 보조지표 정밀 차트")
    target = st.text_input("종목명 검색", value="삼성전자")
    t_match = listings[listings["Code"] == target.zfill(6)] if target.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(target), case=False, na=False)]
    
    if not t_match.empty:
        t_code = str(t_match.iloc[0]["Code"]).zfill(6)
        df_p = load_price(t_code, (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
        if df_p is not None and not df_p.empty:
            df_p["MA20"] = df_p["Close"].rolling(20).mean()
            df_p["STD"] = df_p["Close"].rolling(20).std()
            df_p["Upper"] = df_p["MA20"] + (df_p["STD"] * 2)
            df_p["Lower"] = df_p["MA20"] - (df_p["STD"] * 2)
            
            fig_bb = go.Figure()
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Close"], name="종가"))
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Upper"], name="상단", line=dict(dash="dash")))
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Lower"], name="하단", line=dict(dash="dash"), fill='tonexty'))
            fig_bb.update_layout(height=400, title=f"{t_match.iloc[0]['Name']} 볼린저 밴드")
            st.plotly_chart(fig_bb, use_container_width=True)