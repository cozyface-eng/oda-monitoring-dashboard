import streamlit as st
import pandas as pd
from combined_oda_slack import (
    fetch_google_news_multi,
    fetch_g2b_bids_multi,
    summarize_with_gemini,
    send_slack,
    send_email
)

# ==========================================
# 1. 페이지 기본 설정 및 스타일 정의
# ==========================================
st.set_page_config(
    page_title="ODA 정보 모니터링 대시보드",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 커스텀 타이틀 및 헤더
st.title("🌍 ODA & 국제개발협력 모니터링 대시보드")
st.markdown("""
구글 뉴스 및 나라장터(G2B)에서 수집된 **ODA(공적개발원조), KOICA, EDCF** 관련 최신 정보와 **Gemini AI**가 생성한 브리핑 요약을 확인하세요.
""")
st.markdown("---")

# ==========================================
# 2. 사이드바 설정 영역
# ==========================================
st.sidebar.header("⚙️ 수집 및 발송 설정")

# 2-1. 키워드 설정 (쉼표 구분)
keyword_input = st.sidebar.text_input(
    "검색 키워드 (쉼표로 구분)",
    value="ODA, KOICA, 국제개발협력, EDCF"
)
keywords = [kw.strip() for kw in keyword_input.split(",") if kw.strip()]

# 2-2. 수집 개수 설정
st.sidebar.subheader("📊 수집 수량 설정")
max_news_per_kw = st.sidebar.slider(
    "키워드당 뉴스 수집 개수", 
    min_value=1, max_value=10, value=3
)
max_bids_per_kw = st.sidebar.slider(
    "키워드당 입찰 수집 개수", 
    min_value=1, max_value=10, value=3
)

# 2-3. 알림 발송 옵션
st.sidebar.subheader("🔔 자동 알림 설정")
send_slack_opt = st.sidebar.checkbox("수집 완료 시 슬랙 발송", value=True)
send_email_opt = st.sidebar.checkbox("수집 완료 시 이메일 발송", value=False)

# 2-4. 수집 실행 버튼
run_button = st.sidebar.button("🚀 데이터 수집 & 요약 실행", type="primary", use_container_width=True)

# ==========================================
# 3. 데이터 수집 및 요약 실행 로직
# ==========================================
if run_button:
    if not keywords:
        st.sidebar.error("최소 하나 이상의 키워드를 입력해 주세요.")
    else:
        # Step 1: 데이터 수집 진행
        with st.spinner(f"1/2. 키워드 ({', '.join(keywords)}) 기준 데이터 수집 및 중복 제거 중..."):
            news_data = fetch_google_news_multi(keywords, max_per_keyword=max_news_per_kw)
            bid_data = fetch_g2b_bids_multi(keywords, max_per_keyword=max_bids_per_kw)

            # 세션 상태에 데이터 저장
            st.session_state["news_data"] = news_data
            st.session_state["bid_data"] = bid_data

        # Step 2: Gemini AI 브리핑 요약 생성
        with st.spinner("2/2. Gemini AI 모델이 주요 동향 요약 브리핑 생성 중..."):
            try:
                # 503 과부하 오류 대응을 위한 재시도 로직 포함
                summary_result = summarize_with_gemini(news_data, bid_data, max_retries=5)
                st.session_state["summary_result"] = summary_result
                st.success("🎉 데이터 수집 및 AI 요약 생성이 완료되었습니다!")

                # Step 3: 슬랙 / 이메일 알림 발송
                title = f"[{', '.join(keywords[:2])} 외] ODA 동향 브리핑"
                if send_slack_opt:
                    send_slack(title, summary_result)
                    st.toast("💬 슬랙 메시지가 성공적으로 발송되었습니다!", icon="✅")
                
                if send_email_opt:
                    send_email(title, summary_result)
                    st.toast("📧 이메일이 성공적으로 발송되었습니다!", icon="✅")

            except Exception as e:
                st.error(f"❌ Gemini AI 요약 생성 중 오류가 발생했습니다.\n\n상세 오류: {e}")

# ==========================================
# 4. 결과 출력 영역 (탭 구성)
# ==========================================
tab1, tab2, tab3 = st.tabs(["🤖 AI 요약 브리핑", "📰 수집 뉴스", "🏛️ 나라장터 입찰/공고"])

# --- Tab 1: AI 요약 브리핑 ---
with tab1:
    st.subheader("💡 Gemini AI 동향 요약")
    if "summary_result" in st.session_state and st.session_state["summary_result"]:
        st.markdown(st.session_state["summary_result"])
    else:
        st.info("👈 사이드바에서 [🚀 데이터 수집 & 요약 실행] 버튼을 눌러주세요.")

# --- Tab 2: 수집 뉴스 ---
with tab2:
    st.subheader("📰 구글 뉴스 수집 결과")
    if "news_data" in st.session_state and st.session_state["news_data"]:
        news_df = pd.DataFrame(st.session_state["news_data"])
        
        # 수집 함수의 필드명과 화면 표시용 필드명을 모두 지원
        news_df = news_df.rename(columns={
            "title": "제목",
            "headline": "제목",
            "link": "링크",
            "url": "링크",
            "published": "발행일",
            "date": "발행일",
            "published_at": "발행일",
        })

        # 누락된 컬럼은 빈 값으로 채워 앱이 KeyError로 중단되지 않도록 함
        news_df = news_df.reindex(columns=["제목", "발행일", "링크"])
        
        st.dataframe(
            news_df,
            column_config={
                "링크": st.column_config.LinkColumn("원본 링크", display_text="기사 바로가기")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.write("수집된 뉴스 데이터가 없습니다.")

# --- Tab 3: 나라장터 입찰/공고 ---
with tab3:
    st.subheader("🏛️ 나라장터 입찰 및 소식 수집 결과")
    if "bid_data" in st.session_state and st.session_state["bid_data"]:
        bid_df = pd.DataFrame(st.session_state["bid_data"])
        
        # 컬럼명 한글 변경 및 URL 링크 처리
        bid_df = bid_df.rename(columns={
            "title": "공고/뉴스 제목",
            "agency": "발주/수집 기관",
            "link": "링크"
        })

        # 누락된 컬럼은 빈 값으로 채워 앱이 KeyError로 중단되지 않도록 함
        bid_df = bid_df.reindex(columns=["공고/뉴스 제목", "발주/수집 기관", "링크"])
        
        st.dataframe(
            bid_df,
            column_config={
                "링크": st.column_config.LinkColumn("상세 링크", display_text="공고 바로가기")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.write("수집된 입찰 데이터가 없습니다.")
