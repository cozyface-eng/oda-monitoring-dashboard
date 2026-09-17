# ==========================================
# Streamlit 기반 ODA 정보 모니터링 대시보드 (app.py)
# ==========================================
import json
import pandas as pd
import streamlit as st

# 기존 스크립트 모듈 불러오기
from combined_oda_slack import (
    fetch_g2b_bids,
    fetch_google_news,
    send_email,
    send_slack,
    summarize_with_gemini,
)

# 1. 페이지 설정
st.set_page_config(
    page_title="ODA 데이터 모니터링 대시보드",
    page_icon="📊",
    layout="wide",
)

st.title("📊 ODA 정보 수집 및 요약 대시보드")
st.caption("구글 뉴스 및 나라장터 입찰 정보를 실시간 수집하고 Gemini로 요약합니다.")

# 2. 사이드바 - 제어 파라미터 설정
st.sidebar.header("⚙️ 수집 및 발송 설정")
search_keyword = st.sidebar.text_input("검색 키워드", value="ODA")
max_news = st.sidebar.slider("뉴스 수집 개수", min_value=3, max_value=15, value=5)
max_bids = st.sidebar.slider("입찰 공고 수집 개수", min_value=3, max_value=15, value=5)

st.sidebar.divider()
send_slack_opt = st.sidebar.checkbox("슬랙 발송 실행", value=True)
send_email_opt = st.sidebar.checkbox("이메일 발송 실행", value=False)

# 3. 데이터 수집 실행 버튼
if st.sidebar.button("🚀 데이터 수집 & 요약 실행", type="primary"):
    with st.spinner("1/2. ODA 뉴스 및 입찰 정보 수집 중..."):
        news_data = fetch_google_news(search_keyword, max_results=max_news)
        bid_data = fetch_g2b_bids(search_keyword, max_results=max_bids)

    # 세션 상태에 데이터 저장
    st.session_state["news_data"] = news_data
    st.session_state["bid_data"] = bid_data

    with st.spinner("2/2. Gemini 3.6 모델이 브리핑 요약 생성 중..."):
        try:
            summary_result = summarize_with_gemini(news_data, bid_data)
            st.session_state["summary_result"] = summary_result
            st.success("데이터 수집 및 요약 완료!")

            # 슬랙 및 이메일 전송 옵션
            if send_slack_opt:
                send_slack(f"{search_keyword} 주요 동향 브리핑", summary_result)
                st.toast("슬랙 메시지 발송 완료!", icon="💬")
            if send_email_opt:
                send_email(f"{search_keyword} 주요 동향 브리핑", summary_result)
                st.toast("이메일 발송 완료!", icon="📧")

        except Exception as e:
            st.error(f"Gemini 요약 생성 실패: {e}")

# 4. 메인 화면 - 탭 구성
tab1, tab2, tab3 = st.tabs(["📝 AI 요약 브리핑", "📰 뉴스 목록", "🏛️ 나라장터 입찰 공고"])

with tab1:
    st.subheader("🤖 Gemini AI 요약 리포트")
    if "summary_result" in st.session_state:
        st.markdown(st.session_state["summary_result"])
    else:
        st.info("사이드바에서 '데이터 수집 & 요약 실행' 버튼을 눌러주세요.")

with tab2:
    st.subheader("📰 Google 뉴스 수집 원본")
    if "news_data" in st.session_state and st.session_state["news_data"]:
        df_news = pd.DataFrame(st.session_state["news_data"])
        st.dataframe(
            df_news,
            column_config={
                "link": st.column_config.LinkColumn("기사 링크"),
                "title": "제목",
                "date": "발행일시",
            },
            use_container_width=True,
        )
    else:
        st.write("수집된 뉴스 데이터가 없습니다.")

with tab3:
    st.subheader("🏛️ 나라장터 입찰 공고 목록")
    if "bid_data" in st.session_state and st.session_state["bid_data"]:
        df_bids = pd.DataFrame(st.session_state["bid_data"])
        st.dataframe(
            df_bids,
            column_config={
                "link": st.column_config.LinkColumn("공고 링크"),
                "title": "공고명",
                "agency": "발주기관",
            },
            use_container_width=True,
        )
    else:
        st.write("수집된 입찰 데이터가 없습니다.")