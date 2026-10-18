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

APP_TITLE = "국내 주식 종합 분석기"
DART_BASE = "https://opendart.fss.or.kr/api"

st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")

# 세션 스테이트 초기화 (관심 종목 저장용)
if "favorites" not in st.session_state:
    st.session_state["favorites"] = []

with st.sidebar:
    st.header("🛠️ 분석 메뉴")
    menu = st.radio(
        "이동할 메뉴를 선택하세요",
        [
            "1. 종합 주식 분석기", 
            "2. 관심 종목 (즐겨찾기)", 
            "3. 기술적 보조지표 (RSI/볼린저)", 
            "4. 수급 상위 종목 스크리닝"
        ]
    )
    
    st.divider()
    st.header("⚙️ 설정")
    dart_key = st.text_input(
        "OpenDART API 키 (선택)",
        value=os.getenv("DART_API_KEY", ""),
        type="password",
        help="공시 재무제표 및 최근 공시 원문을 보려면 OpenDART API 키를 입력하세요."
    )
    st.markdown("[OpenDART API 키 발급](https://opendart.fss.or.kr/)")
    st.caption("데이터 출처: FinanceDataReader, Naver API, OpenDART API, Google News")
    st.divider()
    st.markdown("**주의:** 본 프로그램은 정보 제공용이며 투자 권유가 아닙니다.")

# ==========================================
# 공통 데이터 로드 함수
# ==========================================

@st.cache_data(ttl=86400, show_spinner=False)
def load_listings():
    frames = []
    for market in ["KOSPI", "KOSDAQ", "KONEX"]:
        try:
            df = fdr.StockListing(market)
            if df is not None and not df.empty:
                df["시장"] = market
                frames.append(df)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["Code"], keep="first")
    out["Code"] = out["Code"].astype(str).str.zfill(6)
    return out

@st.cache_data(ttl=3600, show_spinner=False)
def load_price(code, start, end):
    return fdr.DataReader(code, start, end)

@st.cache_data(ttl=1800, show_spinner=False)
def get_investor_trends(code):
    url = f"https://m.stock.naver.com/api/stock/{code}/trend"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        "Referer": "https://m.stock.naver.com/",
        "Accept": "application/json, text/plain, */*"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                for k in ["message", "result", "items", "trend"]:
                    if k in data and isinstance(data[k], list):
                        items = data[k]
                        break
            
            if items:
                df = pd.DataFrame(items).head(20)
                inst_col = next((c for c in df.columns if any(x in c.lower() for x in ["organ", "inst", "institution"])), None)
                fore_col = next((c for c in df.columns if any(x in c.lower() for x in ["frgn", "foreigner", "foreign"])), None)
                
                if inst_col and fore_col:
                    inst_sum = pd.to_numeric(df[inst_col].astype(str).str.replace(r'[^\d\-]', '', regex=True), errors="coerce").fillna(0).sum()
                    fore_sum = pd.to_numeric(df[fore_col].astype(str).str.replace(r'[^\d\-]', '', regex=True), errors="coerce").fillna(0).sum()
                    return {"기관_20일": inst_sum, "외국인_20일": fore_sum}
        return "수급 API 대기 중"
    except Exception as e:
        return f"수급 연동 오류: {e}"

@st.cache_data(ttl=86400, show_spinner=False)
def load_corp_codes(api_key):
    if not api_key:
        return pd.DataFrame()
    url = f"{DART_BASE}/corpCode.xml?crtfc_key={api_key}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        if not r.content.startswith(b'PK'):
            return "API_KEY_ERROR"
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml_name = next((n for n in z.namelist() if n.lower().endswith(".xml")), None)
            if not xml_name:
                return pd.DataFrame()
            root = ET.fromstring(z.read(xml_name))
        rows = [{"corp_code": (item.findtext("corp_code") or "").strip(),
                 "stock_code": (item.findtext("stock_code") or "").strip()} 
                for item in root.findall("list")]
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

def get_recent_disclosures(code, api_key):
    if not api_key:
        return None, "사이드바에 OpenDART API 키를 입력하면 최신 공시 원문을 확인할 수 있습니다."
    try:
        corp_df = load_corp_codes(api_key)
        if isinstance(corp_df, str) and corp_df == "API_KEY_ERROR":
            return None, "입력하신 OpenDART API 키가 유효하지 않습니다."
        if corp_df.empty:
            return None, "DART 기업코드 목록을 불러오지 못했습니다."
        hit = corp_df[corp_df["stock_code"].astype(str).str.zfill(6) == str(code).zfill(6)]
        if hit.empty:
            return None, "해당 종목의 기업코드를 찾지 못했습니다."
        corp_code = hit.iloc[0]["corp_code"]
        end_de = datetime.now().strftime("%Y%m%d")
        bgn_de = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        url = f"{DART_BASE}/list.json"
        j = requests.get(url, params={"crtfc_key": api_key, "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de, "page_count": "6"}, timeout=15).json()
        if j.get("status") == "000" and j.get("list"):
            df = pd.DataFrame(j["list"])
            df["link"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + df["rcept_no"]
            return df[["rcept_dt", "report_nm", "link"]], None
        return None, "최근 3개월 내 주요 공시가 없습니다."
    except Exception as e:
        return None, f"공시 조회 오류: {e}"

@st.cache_data(ttl=3600, show_spinner=False)
def get_dart_financials(code, api_key):
    if not api_key:
        return None, "OpenDART API 키를 사이드바에 입력하시면 정확한 공시 재무제표가 연동됩니다."
    try:
        corp_df = load_corp_codes(api_key)
        if isinstance(corp_df, str) and corp_df == "API_KEY_ERROR":
            return None, "유효하지 않은 API 키입니다."
        if corp_df.empty:
            return None, "DART 기업코드를 불러오지 못했습니다."
        hit = corp_df[corp_df["stock_code"].astype(str).str.zfill(6) == str(code).zfill(6)]
        if hit.empty:
            return None, "종목코드를 찾지 못했습니다."
        corp_code = hit.iloc[0]["corp_code"]
        curr_year = datetime.now().year
        
        ordered_periods = []
        for y in range(curr_year, curr_year - 3, -1):
            ordered_periods.append((str(y), "11011", f"{y}년 연간"))
            ordered_periods.append((str(y), "11014", f"{y}년 3분기"))
            ordered_periods.append((str(y), "11012", f"{y}년 반기"))
            ordered_periods.append((str(y), "11013", f"{y}년 1분기"))
            
        collected_data = {}
        for y, reprt_code, col_name in ordered_periods:
            if len(collected_data) >= 3:
                break
            try:
                url = f"{DART_BASE}/fnlttSinglAcntAll.json"
                params = {"crtfc_key": api_key, "corp_code": corp_code, "bsns_year": y, "reprt_code": reprt_code, "fs_div": "CFS"}
                res = requests.get(url, params=params, timeout=5).json()
                if res.get("status") == "000" and res.get("list"):
                    collected_data[col_name] = res["list"]
                    continue
                params["fs_div"] = "OFS"
                res_ofs = requests.get(url, params=params, timeout=5).json()
                if res_ofs.get("status") == "000" and res_ofs.get("list"):
                    collected_data[col_name] = res_ofs["list"]
            except Exception:
                continue
                
        if not collected_data:
            return None, "재무제표 데이터를 조회하지 못했습니다."
            
        patterns = {
            "매출액": ["매출액", "수익(매출액)", "영업수익"],
            "영업이익": ["영업이익", "영업이익(손실)"],
            "당기순이익": ["당기순이익", "당기순이익(손실)"],
            "자산총계": ["자산총계"],
            "부채총계": ["부채총계"],
            "자본총계": ["자본총계"],
            "영업활동현금흐름": ["영업활동현금흐름", "영업활동으로인한현금흐름"]
        }
        
        records = []
        col_names = list(collected_data.keys())
        for label, keywords in patterns.items():
            row_dict = {"주요 계정": label}
            for c_name in col_names:
                raw_df = pd.DataFrame(collected_data[c_name])
                found = raw_df[raw_df["account_nm"].fillna("").apply(lambda x: any(k == x.strip() or k in x for k in keywords))]
                if not found.empty:
                    val_str = found.iloc[0].get("thstrm_amount", "")
                    try:
                        num = float(str(val_str).replace(",", "").strip())
                        disp = f"{num / 100000000:,.1f} 억원"
                    except Exception:
                        disp = str(val_str) if val_str else "-"
                    row_dict[c_name] = disp
                else:
                    row_dict[c_name] = "-"
            records.append(row_dict)
            
        if records:
            res_df = pd.DataFrame(records)[["주요 계정"] + col_names]
            return res_df, f"최근 3개 실적: {' · '.join(col_names)}"
        return None, "재무 항목 정규화 실패"
    except Exception as e:
        return None, f"파싱 실패: {e}"

def get_news(company):
    since = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    query = quote(f'"{company}" 주식')
    url = f"https://news.google.com/rss/search?q={query}+after:{since}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        rows = []
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            source = item.findtext("source") or ""
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub).astimezone().replace(tzinfo=None)
                date_text = dt.strftime("%Y-%m-%d")
            except Exception:
                date_text = pub
            title_l = title.lower()
            negative = ["적자", "하락", "급락", "수사", "소송", "횡령", "감사", "유상증자", "부진", "하향", "매도"]
            positive = ["흑자", "상승", "급등", "수주", "계약", "개선", "호실적", "배당", "자사주", "승인", "상향", "신사업"]
            p = sum(k in title_l for k in positive)
            n = sum(k in title_l for k in negative)
            sentiment = "호재 가능성" if p > n else ("악재 가능성" if n > p else "중립/추가 확인")
            rows.append({"날짜": date_text, "제목": title, "출처": source, "분류(자동)": sentiment, "링크": link})
        return pd.DataFrame(rows).drop_duplicates(subset=["제목"]).head(20)
    except Exception:
        return pd.DataFrame()

def won(v):
    return f"{float(v):,.0f}원" if pd.notna(v) else "자료 없음"

listings = load_listings()
if listings.empty:
    st.error("종목 목록을 불러오지 못했습니다. 인터넷 연결을 확인하세요.")
    st.stop()

# ==========================================
# 메뉴별 화면 분기 처리
# ==========================================

if menu == "1. 종합 주식 분석기":
    st.title("📈 국내 주식 종합 분석기")
    st.caption("공시 재무(OpenDART) · 수급 동향 · 1년 주가 차트 · 뉴스 분석")

    with st.form("search_form"):
        col1, col2 = st.columns([3, 1])
        query = col1.text_input("종목명 또는 종목코드 입력", placeholder="예: 삼성전자 또는 005930")
        submitted = col2.form_submit_button("종목 분석", use_container_width=True, type="primary")

    if not submitted and not query:
        st.info("분석할 국내 상장 종목의 이름 또는 6자리 코드를 입력하세요.")
        st.stop()

    q = query.strip()
    matches = listings[listings["Code"] == q.zfill(6)] if q.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(q), case=False, na=False)]

    if matches.empty:
        st.error("종목을 찾지 못했습니다.")
        st.stop()

    if len(matches) > 1:
        options = {f'{r["Name"]} ({r["Code"]}) · {r["시장"]}': r["Code"] for _, r in matches.head(30).iterrows()}
        chosen = st.selectbox("여러 종목이 검색되었습니다. 선택하세요:", list(options.keys()))
        code = options[chosen]
    else:
        code = str(matches.iloc[0]["Code"]).zfill(6)

    row = listings[listings["Code"] == code].iloc[0]
    company = str(row.get("Name", code))
    
    col_t1, col_t2 = st.columns([4, 1])
    col_t1.subheader(f"{company} ({code})")
    
    # 관심 종목 추가/제거 버튼
    is_fav = code in st.session_state["favorites"]
    if col_t2.button("⭐ 관심 종목 등록" if not is_fav else "❌ 관심 해제", use_container_width=True):
        if not is_fav:
            st.session_state["favorites"].append(code)
            st.success("관심 종목에 추가되었습니다!")
        else:
            st.session_state["favorites"].remove(code)
            st.info("관심 종목에서 해제되었습니다.")

    # 뉴스
    news_df = get_news(company)
    st.markdown("### 🔔 핵심 뉴스 (호재/악재 분류)")
    if news_df is not None and not news_df.empty:
        core_news = news_df[news_df["분류(자동)"] != "중립/추가 확인"]
        if not core_news.empty:
            for _, n in core_news.head(5).iterrows():
                color = "red" if "호재" in n["분류(자동)"] else "blue"
                st.markdown(f'**[{n["날짜"]}] {n["제목"]}** - <span style="color:{color}">{n["분류(자동)"]}</span>', unsafe_allow_html=True)
                st.caption(f'{n["출처"]} | [기사 열람]({n["링크"]})')
        else:
            st.info("최근 3개월 내 주요 호재/악재 키워드 뉴스가 없습니다.")
    st.divider()

    # 주가 및 수급
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
        
        trends = get_investor_trends(code)
        if isinstance(trends, dict):
            m3.metric("기관 순매수 (20일)", f"{trends['기관_20일']:+,.0f} 주")
            m4.metric("외국인 순매수 (20일)", f"{trends['외국인_20일']:+,.0f} 주")
        else:
            m3.metric("기관 순매수 (20일)", "조회 대기")
            m4.metric("외국인 순매수 (20일)", "조회 대기")

        # 기본 차트
        df = prices.copy()
        df["MA20"] = df["Close"].rolling(20).mean()
        df["MA60"] = df["Close"].rolling(60).mean()
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="주가"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MA20"], name="20일선"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MA60"], name="60일선"), row=1, col=1)
        if "Volume" in df.columns:
            fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="거래량"), row=2, col=1)
        fig.update_layout(height=500, title=f"{company} 최근 1년 차트", xaxis_rangeslider_visible=False, legend_orientation="h", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    col_fin, col_dart = st.columns(2)
    with col_fin:
        st.markdown("### 📊 최근 3분기/연간 재무제표")
        dart_fin, fin_info = get_dart_financials(code, dart_key)
        if dart_fin is not None and isinstance(dart_fin, pd.DataFrame):
            st.dataframe(dart_fin, use_container_width=True, hide_index=True)
            st.caption(f"출처: 금융감독원 OpenDART ({fin_info})")
        else:
            st.info(fin_info)
    with col_dart:
        st.markdown("### 📜 최근 주요 공시")
        dart_disc, dart_err = get_recent_disclosures(code, dart_key)
        if dart_disc is not None and not dart_disc.empty:
            for _, d in dart_disc.iterrows():
                st.markdown(f"- **{d['rcept_dt']}** : [{d['report_nm']}]({d['link']})")
        else:
            st.info(dart_err)

elif menu == "2. 관심 종목 (즐겨찾기)":
    st.title("⭐ 나의 관심 종목 리스트")
    st.caption("종합 분석기에서 등록한 즐겨찾기 종목들의 현재가와 등락률을 모아봅니다.")
    
    favs = st.session_state["favorites"]
    if not favs:
        st.info("등록된 관심 종목이 없습니다. '1. 종합 주식 분석기' 메뉴에서 종목을 검색한 뒤 등록해 보세요!")
    else:
        fav_rows = listings[listings["Code"].isin(favs)]
        if not fav_rows.empty:
            cards_data = []
            for _, r in fav_rows.iterrows():
                c_code = r["Code"]
                c_name = r["Name"]
                c_market = r["시장"]
                
                # 최근 가격 조회
                p_df = load_price(c_code, (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
                if p_df is not None and not p_df.empty:
                    last_c = p_df["Close"].iloc[-1]
                    prev_c = p_df["Close"].iloc[-2] if len(p_df) > 1 else last_c
                    chg = (last_c / prev_c - 1) * 100
                    cards_data.append({
                        "종목코드": c_code,
                        "종목명": c_name,
                        "시장": c_market,
                        "현재가": f"{last_c:,.0f}원",
                        "전일대비": f"{chg:+.2f}%"
                    })
            if cards_data:
                st.dataframe(pd.DataFrame(cards_data), use_container_width=True, hide_index=True)

elif menu == "3. 기술적 보조지표 (RSI/볼린저)":
    st.title("📉 기술적 보조지표 분석")
    st.caption("볼린저 밴드 및 RSI(상대강도지수) 지표를 통해 과매수/과매도 구간을 확인합니다.")
    
    target = st.text_input("분석할 종목명 또는 코드 입력", value="삼성전자")
    t_match = listings[listings["Code"] == target.zfill(6)] if target.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(target), case=False, na=False)]
    
    if not t_match.empty:
        t_code = str(t_match.iloc[0]["Code"]).zfill(6)
        t_name = t_match.iloc[0]["Name"]
        
        df_p = load_price(t_code, (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
        if df_p is not None and not df_p.empty:
            # 볼린저 밴드 계산
            df_p["MA20"] = df_p["Close"].rolling(20).mean()
            df_p["STD"] = df_p["Close"].rolling(20).std()
            df_p["Upper"] = df_p["MA20"] + (df_p["STD"] * 2)
            df_p["Lower"] = df_p["MA20"] - (df_p["STD"] * 2)
            
            # RSI 계산 (14일)
            delta = df_p["Close"].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            df_p["RSI"] = 100 - (100 / (1 + rs))
            
            st.subheader(f"{t_name} 볼린저 밴드")
            fig_bb = go.Figure()
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Close"], name="종가", line=dict(color="blue")))
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Upper"], name="상단밴드", line=dict(color="gray", dash="dash")))
            fig_bb.add_trace(go.Scatter(x=df_p.index, y=df_p["Lower"], name="하단밴드", line=dict(color="gray", dash="dash"), fill='tonexty'))
            fig_bb.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_bb, use_container_width=True)
            
            st.subheader(f"{t_name} RSI (상대강도지수)")
            fig_rsi = go.Figure()
            fig_rsi.add_trace(go.Scatter(x=df_p.index, y=df_p["RSI"], name="RSI", line=dict(color="orange")))
            fig_rsi.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="과매수 (70)")
            fig_rsi.add_hline(y=30, line_dash="dash", line_color="green", annotation_text="과매도 (30)")
            fig_rsi.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_rsi, use_container_width=True)

elif menu == "4. 수급 상위 종목 스크리닝":
    st.title("🔥 기관/외국인 수급 트렌드 분석")
    st.caption("주요 대형 우량주들의 최근 주가 흐름과 거래량 추이를 비교합니다.")
    
    market_choice = st.selectbox("시장 선택", ["KOSPI 주요 종목", "KOSDAQ 주요 종목"])
    sample_codes = ["005930", "000660", "373220", "005380", "035420"] if "KOSPI" in market_choice else ["247540", "091900", "028300", "293490", "112040"]
    
    rows_data = []
    for sc in sample_codes:
        hit = listings[listings["Code"] == sc]
        if not hit.empty:
            s_name = hit.iloc[0]["Name"]
            p_df = load_price(sc, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
            if p_df is not None and not p_df.empty:
                cur = p_df["Close"].iloc[-1]
                chg_rate = (cur / p_df["Close"].iloc[0] - 1) * 100
                vol_avg = int(p_df["Volume"].mean())
                rows_data.append({
                    "종목코드": sc,
                    "종목명": s_name,
                    "현재가": f"{cur:,.0f}원",
                    "최근 1개월 수익률": f"{chg_rate:+.2f}%",
                    "평균 거래량": f"{vol_avg:,}주"
                })
    if rows_data:
        st.dataframe(pd.DataFrame(rows_data), use_container_width=True, hide_index=True)