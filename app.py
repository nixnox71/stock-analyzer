import io
import os
import re
import time
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
from pykrx import stock

APP_TITLE = "국내 주식/ETF 종합 분석기"
DART_BASE = "https://opendart.fss.or.kr/api"

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
            "3. 🔥 기관/외국인 수급 상위 20",
            "4. 🚀 전일 상승률 상위 20",
            "5. 💡 AI 추천 종목 스크리닝",
            "6. 📉 기술적 보조지표 차트"
        ]
    )
    st.divider()
    st.caption("데이터 출처: FinanceDataReader, pykrx, Naver API, DART")
    st.markdown("**주의:** 본 프로그램은 정보 제공용이며 투자 권유가 아닙니다.")

# ==========================================
# 공통 데이터 로드 함수
# ==========================================

@st.cache_data(ttl=86400, show_spinner=False)
def load_listings():
    frames = []
    # 일반 주식 로드
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df = fdr.StockListing(market)
            if not df.empty:
                df["시장"] = market
                frames.append(df)
        except: pass
    # ETF 로드 추가
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

@st.cache_data(ttl=600, show_spinner=False) # 장중 실시간 반영을 위해 캐시 10분으로 축소
def get_investor_trends_detail(code):
    url = f"https://m.stock.naver.com/api/stock/{code}/trend"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://m.stock.naver.com/"
    }
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
                fore_col = next((c for c in df.columns if any(x in c.lower() for x in ["frgn", "foreigner", "foreign"])), None)
                
                if inst_col and fore_col:
                    df[inst_col] = pd.to_numeric(df[inst_col].astype(str).str.replace(r'[^\d\-]', '', regex=True), errors="coerce").fillna(0)
                    df[fore_col] = pd.to_numeric(df[fore_col].astype(str).str.replace(r'[^\d\-]', '', regex=True), errors="coerce").fillna(0)
                    return {
                        "기관_1일": df[inst_col].iloc[0] if len(df) > 0 else 0,
                        "외국인_1일": df[fore_col].iloc[0] if len(df) > 0 else 0,
                        "기관_5일": df[inst_col].head(5).sum() if len(df) > 0 else 0,
                        "외국인_5일": df[fore_col].head(5).sum() if len(df) > 0 else 0,
                        "기관_20일": df[inst_col].head(20).sum() if len(df) > 0 else 0,
                        "외국인_20일": df[fore_col].head(20).sum() if len(df) > 0 else 0
                    }
    except: pass
    return {"기관_1일": 0, "외국인_1일": 0, "기관_5일": 0, "외국인_5일": 0, "기관_20일": 0, "외국인_20일": 0}

def get_latest_bday():
    now = datetime.now()
    for d in range(7):
        dt = now - timedelta(days=d)
        # 휴일 여부를 OHLVC 조회가 성공하는지로 판단
        df = stock.get_market_ohlcv(dt.strftime("%Y%m%d"), "KOSPI")
        if not df.empty:
            return dt
    return now

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
        query = col1.text_input("종목명 또는 종목코드 (ETF 포함)", placeholder="예: 삼성전자, TIGER 미국배당다우존스")
        submitted = col2.form_submit_button("종목 분석", use_container_width=True, type="primary")

    if not submitted and not query:
        st.info("검색창에 주식이나 ETF 이름을 입력하세요.")
        st.stop()

    q = query.strip()
    matches = listings[listings["Code"] == q.zfill(6)] if q.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(q), case=False, na=False)]

    if matches.empty:
        st.error("종목을 찾지 못했습니다.")
        st.stop()

    if len(matches) > 1:
        options = {f'{r["Name"]} ({r["Code"]}) · {r["시장"]}': r["Code"] for _, r in matches.head(30).iterrows()}
        chosen = st.selectbox("여러 종목이 검색되었습니다:", list(options.keys()))
        code = options[chosen]
    else:
        code = str(matches.iloc[0]["Code"]).zfill(6)

    row = listings[listings["Code"] == code].iloc[0]
    company = str(row.get("Name", code))
    
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
        m3.metric("기관 순매수 (당일/장중)", f"{tr['기관_1일']:+,.0f} 주")
        m4.metric("외국인 순매수 (당일/장중)", f"{tr['외국인_1일']:+,.0f} 주")

        df = prices.copy()
        df["MA20"] = df["Close"].rolling(20).mean()
        df["MA60"] = df["Close"].rolling(60).mean()
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="주가"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MA20"], name="20일선"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MA60"], name="60일선"), row=1, col=1)
        if "Volume" in df.columns:
            fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="거래량"), row=2, col=1)
        fig.update_layout(height=500, title=f"{company} 차트", xaxis_rangeslider_visible=False, legend_orientation="h")
        st.plotly_chart(fig, use_container_width=True)


# ==========================================
# 2. 관심 종목 (즐겨찾기)
# ==========================================
elif menu == "2. ⭐ 관심 종목 (즐겨찾기)":
    st.title("⭐ 나의 관심 종목 리스트")
    st.caption("장중 실시간 거래량 및 기관/외국인 수급 현황을 모니터링합니다.")
    
    favs = st.session_state["favorites"]
    if not favs:
        st.info("등록된 관심 종목이 없습니다.")
    else:
        fav_rows = listings[listings["Code"].isin(favs)]
        if not fav_rows.empty:
            cards_data = []
            for _, r in fav_rows.iterrows():
                c_code = r["Code"]
                c_name = r["Name"]
                c_market = r["시장"]
                p_df = load_price(c_code, (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
                
                if p_df is not None and not p_df.empty:
                    last_c = p_df["Close"].iloc[-1]
                    prev_c = p_df["Close"].iloc[-2] if len(p_df) > 1 else last_c
                    chg = (last_c / prev_c - 1) * 100
                    vol = p_df["Volume"].iloc[-1] if "Volume" in p_df.columns else 0
                    
                    tr = get_investor_trends_detail(c_code)
                    cards_data.append({
                        "종목명": c_name,
                        "시장": c_market,
                        "현재가": f"{last_c:,.0f}원",
                        "등락률": f"{chg:+.2f}%",
                        "거래량(당일)": f"{vol:,.0f}주",
                        "외국인 매매(당일)": f"{tr['외국인_1일']:+,.0f}주",
                        "기관 매매(당일)": f"{tr['기관_1일']:+,.0f}주"
                    })
            if cards_data:
                st.dataframe(pd.DataFrame(cards_data), use_container_width=True, hide_index=True)


# ==========================================
# 3. 기관/외국인 수급 상위 20
# ==========================================
elif menu == "3. 🔥 기관/외국인 수급 상위 20":
    st.title("🔥 최근 5일 수급 상위 20 종목")
    
    col_m, col_i = st.columns(2)
    market_c = col_m.selectbox("시장 선택", ["KOSPI", "KOSDAQ"])
    investor_c = col_i.selectbox("수급 주체", ["외국인", "기관합계"])
    
    if st.button("수급 상위 조회 (실시간 KRX 연동)", type="primary"):
        with st.spinner("한국거래소(KRX) 전체 데이터를 스크리닝 중입니다..."):
            dt_end = get_latest_bday()
            dt_start = dt_end - timedelta(days=7) # 약 5거래일
            try:
                df_net = stock.get_market_net_purchases_of_equities_by_ticker(dt_start.strftime("%Y%m%d"), dt_end.strftime("%Y%m%d"), market_c, investor_c)
                if not df_net.empty:
                    top20 = df_net.sort_values("순매수거래대금", ascending=False).head(20)
                    rows = []
                    for code, row in top20.iterrows():
                        rows.append({
                            "순위": len(rows) + 1,
                            "종목명": row['종목명'],
                            "순매수대금(원)": f"{row['순매수거래대금']:,.0f}",
                            "순매수량(주)": f"{row['순매수거래량']:,.0f}"
                        })
                    st.dataframe(pd.DataFrame(rows).set_index("순위"), use_container_width=True)
            except Exception as e:
                st.error(f"조회 실패: {e}")


# ==========================================
# 4. 전일 상승률 상위 20
# ==========================================
elif menu == "4. 🚀 전일 상승률 상위 20":
    st.title("🚀 시장별 상승률 상위 20 종목")
    st.caption("가장 최근 거래일 기준 급등주 및 거래량, 매매동향을 파악합니다.")
    
    market_g = st.selectbox("시장 구분", ["KOSPI", "KOSDAQ"])
    
    if st.button("상승률 상위 조회", type="primary"):
        with st.spinner("시장 데이터 집계 중..."):
            b_day = get_latest_bday()
            df_ohlcv = stock.get_market_ohlcv(b_day.strftime("%Y%m%d"), market_g)
            
            if not df_ohlcv.empty:
                top20_g = df_ohlcv.sort_values("등락률", ascending=False).head(20)
                rows_g = []
                for code, row in top20_g.iterrows():
                    name = stock.get_market_ticker_name(code)
                    tr = get_investor_trends_detail(code)
                    rows_g.append({
                        "순위": len(rows_g) + 1,
                        "종목명": name,
                        "상승률": f"{row['등락률']:+.2f}%",
                        "종가": f"{row['종가']:,.0f}원",
                        "거래량": f"{row['거래량']:,.0f}주",
                        "외국인(당일)": f"{tr['외국인_1일']:+,.0f}주",
                        "기관(당일)": f"{tr['기관_1일']:+,.0f}주"
                    })
                st.markdown(f"**기준일:** {b_day.strftime('%Y년 %m월 %d일')}")
                st.dataframe(pd.DataFrame(rows_g).set_index("순위"), use_container_width=True)


# ==========================================
# 5. AI 추천 종목 스크리닝
# ==========================================
elif menu == "5. 💡 AI 추천 종목 스크리닝":
    st.title("💡 AI 조건검색: 주도주 탐색")
    st.markdown("""
    **[추천 알고리즘 조건]**  
    ✔️ 최근 5일 평균 대비 당일 거래량 **200% 이상 (3배) 폭증**  
    ✔️ 주가 추세: **5일선 및 20일 이동평균선 상향 돌파**  
    ✔️ 수급 유입: **당일 또는 최근 5일간 외국인/기관 순매수세 존재**  
    *(서버 안정을 위해 당일 시장 활성 상위 150종목 내에서 우선 스크리닝합니다)*
    """)
    
    if st.button("엔진 가동 (스크리닝 시작)", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            status_text.text("시장 주도 종목 150개 추출 중...")
            df_k = fdr.StockListing('KRX')
            df_k = df_k[~df_k['Name'].str.contains("KODEX|TIGER|KBSTAR|ARIRANG|KOSEF|HANARO|ACE", na=False)] # 일반 주식 위주
            df_top150 = df_k.sort_values('Volume', ascending=False).head(150)
            
            recom_res = []
            total = len(df_top150)
            
            for i, (idx, row) in enumerate(df_top150.iterrows()):
                c = row['Code']
                n = row['Name']
                status_text.text(f"차트 및 수급 분석 중... ({n})")
                
                pdf = load_price(c, (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
                if pdf is None or len(pdf) < 20:
                    progress_bar.progress((i + 1) / total)
                    continue
                
                close = pdf['Close'].iloc[-1]
                ma5 = pdf['Close'].rolling(5).mean().iloc[-1]
                ma20 = pdf['Close'].rolling(20).mean().iloc[-1]
                
                # 1. 이동평균선 조건
                if close > ma5 and close > ma20:
                    vol = pdf['Volume'].iloc[-1]
                    prev_vol_avg = pdf['Volume'].iloc[-6:-1].mean() # 직전 5거래일 평균 거래량
                    
                    # 2. 거래량 폭증 조건 (200% 증가 = 3배 이상)
                    if prev_vol_avg > 0 and vol >= (prev_vol_avg * 3.0):
                        tr = get_investor_trends_detail(c)
                        
                        # 3. 매수세 조건 (당일 또는 5일 기준 양수)
                        if tr['기관_1일'] > 0 or tr['외국인_1일'] > 0 or tr['기관_5일'] > 0 or tr['외국인_5일'] > 0:
                            recom_res.append({
                                "종목명": n,
                                "현재가": f"{close:,.0f}",
                                "상승률": f"{(close/pdf['Close'].iloc[-2]-1)*100:+.2f}%",
                                "거래량": f"{vol:,.0f}주",
                                "외국인(5일)": f"{tr['외국인_5일']:+,.0f}",
                                "기관(5일)": f"{tr['기관_5일']:+,.0f}"
                            })
                progress_bar.progress((i + 1) / total)
            
            progress_bar.empty()
            status_text.empty()
            
            if recom_res:
                st.success(f"조건을 완벽히 만족하는 {len(recom_res)}개 종목을 발굴했습니다!")
                st.dataframe(pd.DataFrame(recom_res), use_container_width=True, hide_index=True)
            else:
                st.warning("현재 시장 흐름에서 모든 조건을 만족하는 종목이 없습니다. (시장이 안정될 때 다시 시도해 보세요)")
                
        except Exception as e:
            st.error(f"스크리닝 중 오류: {e}")


# ==========================================
# 6. 기술적 보조지표 차트
# ==========================================
elif menu == "6. 📉 기술적 보조지표 차트":
    st.title("📉 보조지표 정밀 차트 (RSI/볼린저)")
    
    target = st.text_input("분석할 주식/ETF 검색", value="삼성전자")
    t_match = listings[listings["Code"] == target.zfill(6)] if target.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(target), case=False, na=False)]
    
    if not t_match.empty:
        t_code = str(t_match.iloc[0]["Code"]).zfill(6)
        t_name = t_match.iloc[0]["Name"]
        
        df_p = load_price(t_code, (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
        if df_p is not None and not df_p.empty:
            df_p["MA20"] = df_p["Close"].rolling(20).mean()
            df_p["STD"] = df_p["Close"].rolling(20).std()
            df_p["Upper"] = df_p["MA20"] + (df_p["STD"] * 2)
            df_p["Lower"] = df_p["MA20"] - (df_p["STD"] * 2)
            
            delta = df_p["Close"].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            df_p["RSI"] = 100 - (100 / (1 + (gain / loss)))
            
            st.subheader(f"{t_name} 볼린저 밴드")
            fig_bb = go.Figure()
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Close"], name="종가", line=dict(color="blue")))
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Upper"], name="상단", line=dict(color="gray", dash="dash")))
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Lower"], name="하단", line=dict(color="gray", dash="dash"), fill='tonexty'))
            fig_bb.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_bb, use_container_width=True)
            
            st.subheader(f"{t_name} RSI 지표")
            fig_rsi = go.Figure()
            fig_rsi.add_trace(go.Scatter(x=df_p.index, y=df_p["RSI"], name="RSI", line=dict(color="orange")))
            fig_rsi.add_hline(y=70, line_dash="dash", line_color="red")
            fig_rsi.add_hline(y=30, line_dash="dash", line_color="green")
            fig_rsi.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_rsi, use_container_width=True)