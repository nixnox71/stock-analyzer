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
                    return {
                        "기관_1일": df[inst_col].iloc[0] if len(df) > 0 else 0,
                        "외국인_1일": df[fore_col].iloc[0] if len(df) > 0 else 0,
                        "기관_5일": df[inst_col].head(5).sum() if len(df) > 0 else 0,
                        "외국인_5일": df[fore_col].head(5).sum() if len(df) > 0 else 0
                    }
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
                    return pd.DataFrame([{
                        "corp_code": (item.findtext("corp_code") or "").strip(),
                        "stock_code": (item.findtext("stock_code") or "").strip()
                    } for item in root.findall("list")])
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
        ordered_periods.extend([
            (str(y), "11011", f"{y}년 연간"), 
            (str(y), "11014", f"{y}년 3분기"), 
            (str(y), "11012", f"{y}년 반기"), 
            (str(y), "11013", f"{y}년 1분기")
        ])
        
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
        
    patterns = {
        "매출액": ["매출액", "수익(매출액)", "영업수익"], 
        "영업이익": ["영업이익", "영업이익(손실)"], 
        "당기순이익": ["당기순이익", "당기순이익(손실)"], 
        "자산총계": ["자산총계"], 
        "부채총계": ["부채총계"], 
        "자본총계": ["자본총계"]
    }
    
    records = []
    col_names = list(collected_data.keys())
    for label, keywords in patterns.items():
        row_dict = {"주요 계정": label}
        for c_name in col_names:
            raw_df = pd.DataFrame(collected_data[c_name])
            found = raw_df[raw_df["account_nm"].fillna("").apply(lambda x: any(k == x.strip() or k in x for k in keywords))]
            if not found.empty:
                try: 
                    row_dict[c_name] = f"{float(str(found.iloc[0].get('thstrm_amount', '')).replace(',', '').strip()) / 100000000:,.1f} 억원"
                except: 
                    row_dict[c_name] = "-"
            else: 
                row_dict[c_name] = "-"
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
            
            pub_date = item.findtext("pubDate") or ""
            source = item.findtext("source") or ""
            link = item.findtext("link") or ""
            sentiment = "호재 가능성" if p > n else ("악재 가능성" if n > p else "중립/추가 확인")
            
            rows.append({
                "날짜": pub_date[5:16], 
                "제목": title, 
                "출처": source, 
                "분류(자동)": sentiment, 
                "링크": link
            })
        return pd.DataFrame(rows).drop_duplicates(subset=["제목"])
    except: 
        return pd.DataFrame()

# ==========================================
# 증권사 리포트 & 투자의견 추출 함수 (초고속 파싱으로 프리징 완전 차단)
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def get_analyst_reports(code):
    url = f"https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={code}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        html = r.content.decode('euc-kr', 'replace')
        
        # 무한루프를 일으키던 정규식을 빼고, 안전하고 빠른 문자열 자르기(split) 적용
        table_start = html.find('<table class="type1">')
        table_end = html.find('</table>', table_start)
        if table_start != -1 and table_end != -1:
            html = html[table_start:table_end]
            
        def clean_html(raw):
            return re.sub(r'<[^>]+>', '', raw).strip()
            
        reports = []
        rows = html.split('<tr>')
        for row in rows[1:]:
            tds = row.split('<td')
            if len(tds) >= 8:
                title_raw = '<td' + tds[3]
                title_match = re.search(r'<a[^>]*>(.*?)</a>', title_raw, re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else clean_html(title_raw)
                
                price = clean_html('<td' + tds[4])
                opinion = clean_html('<td' + tds[5])
                firm = clean_html('<td' + tds[6])
                date = clean_html('<td' + tds[7])
                
                if title and opinion and opinion != "투자의견":
                    reports.append({
                        "작성일": date,
                        "증권사": firm,
                        "투자의견": opinion,
                        "목표가": price,
                        "리포트 제목": title
                    })
        return pd.DataFrame(reports).head(5)
    except Exception:
        return pd.DataFrame()

def won(v): return f"{float(v):,.0f}원" if pd.notna(v) else "자료 없음"

listings = load_listings()
if listings.empty:
    st.error("종목 목록을 불러오지 못했습니다. 인터넷 연결을 확인하세요.")
    st.stop()

# ==========================================
# 1. 종합 주식/ETF 분석 (모든 구역에 에러 보호막 추가)
# ==========================================
if st.session_state.current_menu == "1. 종합 주식/ETF 분석":
    st.title("📈 국내 주식 및 ETF 종합 분석기")
    with st.form("search_form"):
        col1, col2 = st.columns([3, 1])
        query_val = st.session_state.target_stock if st.session_state.target_stock else ""
        query = col1.text_input("종목명 또는 종목코드 (ETF 포함)", value=query_val, placeholder="예: 삼성전자")
        submitted = col2.form_submit_button("종목 분석", use_container_width=True, type="primary")

    if not submitted and not query: st.stop()

    q = query.strip()
    matches = listings[listings["Code"] == q.zfill(6)] if q.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(q), case=False, na=False)]
    if matches.empty: st.error("종목을 찾지 못했습니다."); st.stop()
    
    code, company = str(matches.iloc[0]["Code"]).zfill(6), str(matches.iloc[0]["Name"])
    
    col_t1, col_t2 = st.columns([4, 1])
    col_t1.subheader(f"{company} ({code})")
    
    is_fav = code in st.session_state["favorites"]
    if col_t2.button("⭐ 관심 종목 등록" if not is_fav else "❌ 관심 해제", use_container_width=True):
        if not is_fav:
            st.session_state["favorites"].append(code)
            st.success("등록 완료! (브라우저를 닫아도 유지됩니다)")
        else:
            st.session_state["favorites"].remove(code)
            st.info("해제 완료.")
        save_favorites() 

    # 뉴스 파트 (보호막 적용)
    st.markdown("### 🔔 핵심 뉴스 (호재/악재 자동 분류)")
    try:
        news_df = get_news(company)
        if news_df is not None and not news_df.empty:
            core_news = news_df[news_df["분류(자동)"] != "중립/추가 확인"]
            if not core_news.empty:
                for _, n in core_news.head(5).iterrows():
                    color = "red" if "호재" in n["분류(자동)"] else "blue"
                    st.markdown(f'**[{n["날짜"]}] {n["제목"]}** - <span style="color:{color}">{n["분류(자동)"]}</span>', unsafe_allow_html=True)
                    st.caption(f'{n["출처"]} | [기사 열람]({n["링크"]})')
            else: st.info("최근 주요 호재/악재 뉴스가 없습니다.")
        else:
            st.info("최근 주요 뉴스가 없습니다.")
    except Exception as e:
        st.error(f"뉴스 데이터를 불러오는 중 일시적인 오류가 발생했습니다.")
        
    st.divider()
    
    # 증권사 리포트 파트 (보호막 적용)
    st.markdown("### 📑 최근 증권사 리포트 및 투자의견")
    try:
        reports_df = get_analyst_reports(code)
        if not reports_df.empty:
            opinions = reports_df['투자의견'].tolist()
            buy_count = sum(1 for o in opinions if '매수' in o or 'Buy' in o or 'buy' in o.lower())
            
            st.caption(f"🎯 최근 5개 리포트 중 **매수(Buy) 의견: {buy_count}곳**")
            st.dataframe(reports_df, use_container_width=True, hide_index=True)
        else:
            st.info("최근 발행된 증권사 리포트가 없습니다. (ETF, 소형주 등)")
    except Exception as e:
        st.error(f"리포트 데이터를 분석하는 중 일시적인 오류가 발생했습니다.")
        
    st.divider()

    # 차트 파트 (보호막 적용)
    try:
        prices = load_price(code, (datetime.now() - timedelta(days=370)).strftime("%Y-%m-%d"), (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))
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
            fig.update_layout(height=400, margin=dict(t=10, b=10), xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"주가 차트를 그리는 중 일시적인 오류가 발생했습니다.")

    # DART 파트 (보호막 적용)
    st.divider()
    col_fin, col_dart = st.columns(2)
    with col_fin:
        st.markdown("### 📊 최근 실적/재무 (OpenDART)")
        try:
            dart_fin, fin_info = get_dart_financials(code, dart_key)
            if dart_fin is not None and isinstance(dart_fin, pd.DataFrame):
                st.dataframe(dart_fin, use_container_width=True, hide_index=True)
                st.caption(f"출처: 금융감독원 OpenDART ({fin_info})")
            else: 
                st.info(fin_info)
        except Exception as e:
            st.error(f"재무 정보를 불러오는 중 오류가 발생했습니다.")
            
    with col_dart:
        st.markdown("### 📜 최근 주요 공시")
        try:
            dart_disc, dart_err = get_recent_disclosures(code, dart_key)
            if dart_disc is not None and not dart_disc.empty:
                for _, d in dart_disc.iterrows():
                    st.markdown(f"- **{d['rcept_dt']}** : [{d['report_nm']}]({d['link']})")
            else: 
                st.info(dart_err)
        except Exception as e:
            st.error(f"공시 정보를 불러오는 중 오류가 발생했습니다.")

# ==========================================
# 2. 관심 종목 (즐겨찾기)
# ==========================================
elif st.session_state.current_menu == "2. ⭐ 관심 종목 (즐겨찾기)":
    st.title("⭐ 나의 관심 종목 리스트")
    favs = st.session_state["favorites"]
    if not favs: 
        st.info("등록된 관심 종목이 없습니다. 종합 분석기에서 등록해 보세요.")
    else:
        for fav in favs:
            hit = listings[listings["Code"] == fav]
            if hit.empty: continue
            c_name = hit.iloc[0]["Name"]
            
            with st.container():
                c1, c2, c3, c4, c5 = st.columns([1.5, 1.5, 1.5, 1.5, 1.5])
                c1.markdown(f"#### {c_name}\n`{fav}`")
                
                p_df = load_price(fav, (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
                if p_df is not None and not p_df.empty:
                    last_c = p_df["Close"].iloc[-1]
                    prev_c = p_df["Close"].iloc[-2] if len(p_df) > 1 else last_c
                    chg = (last_c / prev_c - 1) * 100
                    c2.metric("현재가", f"{last_c:,.0f}원", f"{chg:+.2f}%")
                    
                    tr = get_investor_trends_detail(fav)
                    c3.metric("외국인(당일)", f"{tr['외국인_1일']:+,.0f}주")
                    c4.metric("기관(당일)", f"{tr['기관_1일']:+,.0f}주")
                else:
                    c2.write("데이터 없음"); c3.write("-"); c4.write("-")
                
                st.markdown("<br>", unsafe_allow_html=True)
                if c5.button("➡️ 분석화면 이동", key=f"btn_{fav}", use_container_width=True):
                    st.session_state.current_menu = "1. 종합 주식/ETF 분석"
                    st.session_state.target_stock = fav
                    st.rerun()
            st.divider()

# ==========================================
# 3, 4, 5. 스크리닝 기능들
# ==========================================
elif st.session_state.current_menu == "3. 🔥 시가총액 상위 20 수급 동향":
    st.title("🔥 시장별 시가총액 상위 20종목 수급 점검")
    market_c = st.selectbox("시장 선택", ["KOSPI", "KOSDAQ"])
    if st.button("수급 및 시세 조회", type="primary"):
        with st.spinner("데이터 분석 중입니다..."):
            df_m = listings[listings['시장'] == market_c].copy()
            top20 = df_m.sort_values("Marcap", ascending=False).head(20) if 'Marcap' in df_m.columns else df_m.head(20)
            rows = []
            for i, r in top20.iterrows():
                p_df = load_price(r['Code'], (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
                close_p = p_df['Close'].iloc[-1] if p_df is not None and not p_df.empty else 0
                tr = get_investor_trends_detail(r['Code'])
                rows.append({
                    "순위": len(rows) + 1, 
                    "종목명": r['Name'], 
                    "현재가": f"{close_p:,.0f}원", 
                    "외국인(5일 합계)": f"{tr['외국인_5일']:+,.0f}주", 
                    "기관(5일 합계)": f"{tr['기관_5일']:+,.0f}주"
                })
            st.dataframe(pd.DataFrame(rows).set_index("순위"), use_container_width=True)

elif st.session_state.current_menu == "4. 🚀 당일 상승률 상위 20":
    st.title("🚀 시장별 상승률 상위 20 종목")
    market_g = st.selectbox("시장 구분", ["KOSPI", "KOSDAQ"])
    if st.button("상승률 상위 조회", type="primary"):
        with st.spinner("시장 데이터 집계 중..."):
            df_krx = listings[listings['시장'] == market_g].copy()
            if 'ChagesRatio' in df_krx.columns:
                top20_g = df_krx.sort_values("ChagesRatio", ascending=False).head(20)
                rows_g = []
                for _, r in top20_g.iterrows():
                    tr = get_investor_trends_detail(r['Code'])
                    rows_g.append({
                        "순위": len(rows_g) + 1, 
                        "종목명": r['Name'], 
                        "상승률": f"{r['ChagesRatio']:+.2f}%", 
                        "종가": f"{r['Close']:,.0f}원", 
                        "거래량": f"{r['Volume']:,.0f}주", 
                        "외국인(당일)": f"{tr['외국인_1일']:+,.0f}주", 
                        "기관(당일)": f"{tr['기관_1일']:+,.0f}주"
                    })
                st.dataframe(pd.DataFrame(rows_g).set_index("순위"), use_container_width=True)
            else: 
                st.error("현재 시간 기준 등락률 데이터를 불러올 수 없습니다.")

elif st.session_state.current_menu == "5. 💡 AI 추천 종목 스크리닝":
    st.title("💡 조건검색: 주도주 탐색")
    st.caption("속도 최적화를 위해 당일 거래량 최상위 **30개 주도주** 내에서만 정밀 스크리닝을 진행합니다.")
    if st.button("스크리닝 시작 (초고속 모드)", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        df_k = listings[(listings['시장'].isin(['KOSPI', 'KOSDAQ'])) & (~listings['Name'].str.contains("KODEX|TIGER|KBSTAR", na=False))]
        
        df_top = df_k.sort_values('Volume', ascending=False).head(30) if 'Volume' in df_k.columns else df_k.head(30)
        
        recom_res = []
        for i, (_, r) in enumerate(df_top.iterrows()):
            status_text.text(f"분석 중... ({r['Name']})")
            pdf = load_price(r['Code'], (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
            if pdf is not None and len(pdf) >= 20:
                close = pdf['Close'].iloc[-1]
                ma5 = pdf['Close'].rolling(5).mean().iloc[-1]
                ma20 = pdf['Close'].rolling(20).mean().iloc[-1]
                
                if close > ma5 and close > ma20:
                    vol = pdf['Volume'].iloc[-1]
                    prev_vol_avg = pdf['Volume'].iloc[-6:-1].mean()
                    
                    if prev_vol_avg > 0 and vol >= (prev_vol_avg * 2.0):
                        tr = get_investor_trends_detail(r['Code'])
                        if tr['기관_1일'] > 0 or tr['외국인_1일'] > 0:
                            recom_res.append({
                                "종목명": r['Name'], 
                                "현재가": f"{close:,.0f}", 
                                "거래량": f"{vol:,.0f}주", 
                                "외국인(당일)": f"{tr['외국인_1일']:+,.0f}", 
                                "기관(당일)": f"{tr['기관_1일']:+,.0f}"
                            })
            progress_bar.progress((i + 1) / len(df_top))
        status_text.empty()
        
        if recom_res: 
            st.dataframe(pd.DataFrame(recom_res), use_container_width=True, hide_index=True)
        else: 
            st.warning("조건을 완벽히 만족하는 종목이 없습니다.")

# ==========================================
# 6. 정밀 기술적 보조지표 차트
# ==========================================
elif st.session_state.current_menu == "6. 📉 HTS급 보조지표 차트":
    st.title("📉 HTS급 정밀 기술적 분석 차트")
    st.markdown("**(캔들 차트 + 볼린저 밴드 + 피보나치 되돌림 + RSI 지표)**")
    
    target = st.text_input("종목명 검색", value="삼성전자")
    t_match = listings[listings["Code"] == target.zfill(6)] if target.isdigit() else listings[listings["Name"].astype(str).str.contains(re.escape(target), case=False, na=False)]
    
    if not t_match.empty:
        t_code = str(t_match.iloc[0]["Code"]).zfill(6)
        
        df_p = load_price(t_code, (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"))
        if df_p is not None and not df_p.empty:
            df_p["MA20"] = df_p["Close"].rolling(20).mean()
            df_p["STD"] = df_p["Close"].rolling(20).std()
            df_p["Upper"] = df_p["MA20"] + (df_p["STD"] * 2)
            df_p["Lower"] = df_p["MA20"] - (df_p["STD"] * 2)
            
            max_p, min_p = df_p['High'].max(), df_p['Low'].min()
            diff = max_p - min_p
            fib_levels = {
                "0.0% (고점)": max_p, 
                "23.6%": max_p - diff * 0.236, 
                "38.2%": max_p - diff * 0.382, 
                "50.0% (절반)": max_p - diff * 0.5, 
                "61.8%": max_p - diff * 0.618, 
                "100.0% (저점)": min_p
            }
            
            delta = df_p["Close"].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            df_p["RSI"] = 100 - (100 / (1 + (gain / loss)))
            
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05)
            fig.add_trace(go.Candlestick(x=df_p.index, open=df_p['Open'], high=df_p['High'], low=df_p['Low'], close=df_p['Close'], name="주가"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_p.index, y=df_p['Upper'], name="볼린저 상단", line=dict(color="rgba(173, 216, 230, 0.5)", dash="dash")), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_p.index, y=df_p['Lower'], name="볼린저 하단", fill='tonexty', fillcolor="rgba(173, 216, 230, 0.1)", line=dict(color="rgba(173, 216, 230, 0.5)", dash="dash")), row=1, col=1)
            
            fib_colors = ["red", "orange", "yellow", "green", "blue", "purple"]
            for i, (label, val) in enumerate(fib_levels.items()):
                fig.add_hline(y=val, line_dash="dot", line_color=fib_colors[i], annotation_text=label, annotation_position="top left", row=1, col=1)
            
            fig.add_trace(go.Scatter(x=df_p.index, y=df_p['RSI'], name="RSI (14일)", line=dict(color="purple")), row=2, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="red", annotation_text="과매수 (70)", row=2, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", annotation_text="과매도 (30)", row=2, col=1)
            
            fig.update_layout(height=800, margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)