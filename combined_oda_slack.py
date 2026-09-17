from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import smtplib
import urllib.parse
import feedparser
from google import genai
import requests


# ==========================================
# 1. 설정 정보 (사용자 정보 입력)
# ==========================================
import os
import streamlit as st


def get_secret(key: str, default: str = "") -> str:
    """Streamlit Cloud의 st.secrets와 로컬 os.getenv 모두 지원"""
    try:
        # Streamlit 환경일 때
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    # 로컬 또는 일반 파이썬 환경일 때
    return os.getenv(key, default)


# 환경 변수 및 Secrets에서 불러오기
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
SLACK_WEBHOOK_URL = get_secret("SLACK_WEBHOOK_URL")
G2B_SERVICE_KEY = get_secret("G2B_SERVICE_KEY")
SENDER_EMAIL = get_secret("SENDER_EMAIL")
SENDER_PASSWORD = get_secret("SENDER_PASSWORD")
RECEIVER_EMAIL = get_secret("RECEIVER_EMAIL")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
ENABLE_EMAIL = True

# ==========================================
# 2. 데이터 수집 모듈 (RSS & G2B API)
# ==========================================


def fetch_google_news(keyword: str, max_results: int = 10):
    """단일 키워드 기준 구글 뉴스 RSS 수집"""
    encoded_keyword = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(rss_url)

    results = []
    for entry in feed.entries[:max_results]:
        results.append(
            {
                "title": entry.title,
                "link": entry.link,
                "date": getattr(entry, "published", "날짜 미상"),
            }
        )
    return results


def fetch_g2b_bids(
    keyword: str = "ODA", days_back: int = 7, max_results: int = 10
):
    """나라장터 입찰공고 API 직접 수집 (Encoding/Decoding 키 자동 보정 적용)"""
    now = datetime.now()
    start_date = (now - timedelta(days=days_back)).strftime("%Y%m%d0000")
    end_date = now.strftime("%Y%m%d2359")

    # 1. 조달청 입찰공고목록 용역조회 엔드포인트
    endpoint = "https://apis.data.go.kr/1230000/BidPublicInfoService03/getBidPblcListInfoServcPPSSrch01"

    # 2. 인증키 디코딩 보정 (Encoding/Decoding 키 모두 대응 가능하도록 처리)
    raw_key = G2B_SERVICE_KEY or ""
    # 이미 디코딩된 키라도 unquote를 거치면 원문 형태가 유지됩니다.
    decoded_key = urllib.parse.unquote(raw_key)

    # 3. API 요청 파라미터 구성 (requests가 자동으로 올바른 인코딩을 수행함)
    params = {
        "serviceKey": decoded_key,
        "numOfRows": str(max_results),
        "pageNo": "1",
        "inptStartDt": start_date,
        "inptEndDt": end_date,
        "bidNtceNm": keyword,
        "type": "json",
    }

    try:
        response = requests.get(endpoint, params=params, timeout=10)

        if response.status_code == 200:
            res_text = response.text

            # 공공데이터포털 특유의 XML 인증 에러 응답 분기
            if (
                "OpenAPI_ServiceResponse" in res_text
                or "<cmmMsgHeader>" in res_text
                or "SERVICE_KEY" in res_text
            ):
                print(
                    f"[G2B API 인증 실패] 키워드: '{keyword}' - 인증키 확인 필요."
                )
                raise ValueError("G2B API Service Key Error")

            data = response.json()
            items = (
                data.get("response", {})
                .get("body", {})
                .get("items", {})
                .get("item", [])
            )

            # 단건 결과일 경우 리스트로 변환
            if isinstance(items, dict):
                items = [items]

            bids = []
            for item in items:
                title = item.get("bidNtceNm", "제목 없음")
                agency = item.get("ntceInsttNm") or item.get(
                    "dmanInsttNm", "발주기관 미상"
                )
                bid_no = item.get("bidNtceNo", "")
                url = item.get(
                    "bidNtceDtlUrl",
                    f"https://www.g2b.go.kr:8081/ep/invitation/type1/bidInfoDtl.do?bidNo={bid_no}",
                )

                bids.append({"title": title, "agency": agency, "link": url})

            if bids:
                return bids

        raise ValueError("G2B API 반환 데이터 없음")

    except Exception as e:
        # Fallback: API 오류 또는 검색 결과가 없을 경우 구글 RSS 우회 수집
        print(f"[G2B Fallback 작동] 원인: {e}")
        fallback_query = f"나라장터 {keyword}"
        fallback_news = fetch_google_news(
            fallback_query, max_results=max_results
        )
        return [
            {
                "title": item["title"],
                "agency": f"우회수집({keyword})",
                "link": item["link"],
            }
            for item in fallback_news
        ]

# ==========================================
# 2-1. 다중 키워드 지원 & 중복 제거 래퍼 함수 (신규 추가)
# ==========================================


def fetch_google_news_multi(keywords: list, max_per_keyword: int = 3) -> list:
    """여러 키워드로 뉴스를 수집하고 URL 기준 중복 제거"""
    all_news = []
    seen_urls = set()

    for kw in keywords:
        news_list = fetch_google_news(kw, max_results=max_per_keyword)
        for item in news_list:
            if item["link"] not in seen_urls:
                seen_urls.add(item["link"])
                all_news.append(item)

    return all_news


def fetch_g2b_bids_multi(keywords: list, max_per_keyword: int = 3) -> list:
    """여러 키워드로 나라장터 공고를 수집하고 URL 기준 중복 제거"""
    all_bids = []
    seen_urls = set()

    for kw in keywords:
        bid_list = fetch_g2b_bids(kw, max_results=max_per_keyword)
        for item in bid_list:
            if item["link"] not in seen_urls:
                seen_urls.add(item["link"])
                all_bids.append(item)

    return all_bids

# ==========================================
# 3. Gemini 요약 모듈 (링크 포함 방식 개선)
# ==========================================
import json
import time
from google import genai
from google.genai.errors import APIError


def summarize_with_gemini(
    news_list: list, bid_list: list, max_retries: int = 5
) -> str:
    """Gemini 3.6 모델 요약 (503 대응 강화: 최대 5회 재시도)"""
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
    당신은 ODA(개발협력) 분야 정보 분석가입니다.
    아래 수집된 ODA 뉴스 및 나라장터 입찰 정보를 바탕으로, 핵심 이슈별 요약 브리핑을 작성해 주세요.

    [작성 규격]
    1. 각 요약 항목의 제목에는 반드시 원본 기사/공고의 URL 링크를 마크다운 형태 `[제목](URL)`로 포함하세요.
    2. 슬랙(Slack) 및 이메일에서 바로 클릭하여 이동할 수 있도록 URL을 절대 생략하지 마세요.
    3. 핵심 불렛포인트(•)로 작성하고, 핵심 내용을 2~3줄로 설명하세요.

    [ 수집 데이터 ]
    1. ODA 주요 뉴스:
    {json.dumps(news_list, ensure_ascii=False, indent=2)}

    2. 나라장터 입찰/소식:
    {json.dumps(bid_list, ensure_ascii=False, indent=2)}
    """

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash", contents=prompt
            )
            return response.text.strip()

        except APIError as e:
            # 503(UNAVAILABLE) 또는 429(Too Many Requests) 발생 시 대기 후 재시도
            if attempt < max_retries:
                wait_time = (2**attempt) * 2  # 4초, 8초, 16초, 32초 점진적 대기
                print(
                    f"⚠️ Gemini API 서버 지연/과부하 ({e.code}). {wait_time}초 후 재시도합니다... ({attempt}/{max_retries})"
                )
                time.sleep(wait_time)
            else:
                print("❌ Gemini API 요청 최종 실패.")
                raise e
        except Exception as e:
            print(f"❌ Gemini 요약 처리 중 예외 발생: {e}")
            raise e

# ==========================================
# 4. 슬랙 및 이메일 전송 모듈
# ==========================================
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests


def send_slack(title: str, summary: str):
    """슬랙 Webhook 메시지 안전 발송 (400 에러 방지)"""
    if not SLACK_WEBHOOK_URL:
        print("⚠️ 슬랙 Webhook URL이 설정되지 않았습니다.")
        return

    # 슬랙용 마크다운 형식으로 전송 데이터 구성
    payload = {
        "text": f"📢 *{title}*\n\n{summary}",
        "unfurl_links": False,  # 링크 미리보기 자동 생성 방지 (깔끔한 메시지 유지)
    }

    try:
        # json=payload 방식을 사용해야 특수문자/줄바꿈이 자동 이스케이프 처리됩니다.
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)

        if response.status_code == 200:
            print(" 슬랙 발송 완료")
        else:
            print(f"❌ 슬랙 발송 실패: {response.status_code}")
            print(f" 상세 원인: {response.text}")  # 400 상세 원인 출력
    except Exception as e:
        print(f"❌ 슬랙 전송 중 예외 발생: {e}")


def send_email(title: str, summary: str):
    """이메일 발송 (MIME, 마크다운 링크 HTML 변환 적용)"""
    if not ENABLE_EMAIL:
        return

    # 마크다운 링크 [제목](URL) -> HTML <a href="URL">제목</a> 변환
    html_summary = re.sub(
        r"\[(.*?)\]\((https?://[^\s]+)\)",
        r'<a href="\2" target="_blank" style="color: #1264a3; font-weight: bold;">\1</a>',
        summary,
    )
    html_summary = html_summary.replace("\n", "<br>")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📢 [ODA 알림] {title}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
        <h2 style="color: #1264a3;">📊 {title}</h2>
        <div style="line-height: 1.6; color: #333;">
            {html_summary}
        </div>
    </div>
    """
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        server.quit()
        print(" 이메일 발송 완료")
    except Exception as e:
        print(f"❌ 이메일 발송 실패: {e}")

# ==========================================
# 5. 파이프라인 실행
# ==========================================
if __name__ == "__main__":
    print("🚀 ODA 뉴스 및 입찰공고 자동화 파이프라인을 시작합니다...\n")

    # 1) 데이터 수집
    news_data = fetch_google_news("공적개발원조 ODA", max_results=3)
    bid_data = fetch_g2b_bids("ODA", days_back=7)

    # 2) Gemini 3.6 요약
    print("🤖 Gemini 3.6 모델이 요약 브리핑을 생성하는 중입니다...")
    summary_result = summarize_with_gemini(news_data, bid_data)

    print("\n[생성된 요약 결과]")
    print(summary_result)
    print("\n-------------------------------------------")

    # 3) 슬랙 및 이메일 전송
    today_str = datetime.now().strftime("%Y-%m-%d")
    report_title = f"일간 ODA 동향 및 입찰 리포트 ({today_str})"

    send_slack(report_title, summary_result)
    send_email(report_title, summary_result)