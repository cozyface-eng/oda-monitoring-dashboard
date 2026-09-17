import os
import urllib.parse
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import feedparser
import requests
import streamlit as st

# ==========================================
# 1. 환경 변수 및 Secrets 로드
# ==========================================


def get_secret(key: str, default: str = "") -> str:
    """Streamlit Cloud의 st.secrets와 로컬 os.getenv 모두 지원"""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
SLACK_WEBHOOK_URL = get_secret("SLACK_WEBHOOK_URL")
G2B_SERVICE_KEY = get_secret("G2B_SERVICE_KEY")
SENDER_EMAIL = get_secret("SENDER_EMAIL")
SENDER_PASSWORD = get_secret("SENDER_PASSWORD")
RECEIVER_EMAIL = get_secret("RECEIVER_EMAIL")


# ==========================================
# 2. 데이터 수집 모듈 (Google News & G2B)
# ==========================================


def fetch_google_news(keyword: str, max_results: int = 10) -> list:
    """구글 뉴스 RSS 수집"""
    encoded_keyword = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(rss_url)

    results = []
    for entry in feed.entries[:max_results]:
        results.append({
            "title": getattr(entry, "title", "제목 없음"),
            "link": getattr(entry, "link", ""),
            "date": getattr(entry, "published", "날짜 미상"),
        })
    return results


def fetch_g2b_bids(
    keyword: str = "ODA", days_back: int = 14, max_results: int = 10
) -> list:
    """나라장터 입찰공고 API 수집 (XML/JSON 통합 지원 및 예외 안전장치)"""
    now = datetime.now()
    start_date = (now - timedelta(days=days_back)).strftime("%Y%m%d0000")
    end_date = now.strftime("%Y%m%d2359")

    raw_key = G2B_SERVICE_KEY or ""
    if not raw_key:
        print("[G2B API Warning] G2B_SERVICE_KEY가 설정되지 않았습니다.")

    decoded_key = urllib.parse.unquote(raw_key)
    encoded_key = urllib.parse.quote(decoded_key)
    encoded_keyword = urllib.parse.quote(keyword)

    endpoint = "https://apis.data.go.kr/1230000/BidPublicInfoService03/getBidPblcListInfoServcPPSSrch01"
    full_url = (
        f"{endpoint}?"
        f"serviceKey={encoded_key}&"
        f"numOfRows={max_results}&"
        f"pageNo=1&"
        f"inptStartDt={start_date}&"
        f"inptEndDt={end_date}&"
        f"bidNtceNm={encoded_keyword}&"
        f"type=json"
    )

    try:
        response = requests.get(full_url, timeout=10)
        if response.status_code == 200:
            res_text = response.text.strip()

            if (
                "OpenAPI_ServiceResponse" in res_text
                or "SERVICE_KEY" in res_text
            ):
                raise ValueError("G2B Service Key Authentication Error")

            bids = []
            if res_text.startswith("{") or res_text.startswith("["):
                data = response.json()
                items = (
                    data.get("response", {})
                    .get("body", {})
                    .get("items", {})
                    .get("item", [])
                )
                if isinstance(items, dict):
                    items = [items]

                for item in items:
                    bids.append({
                        "title": item.get("bidNtceNm", "제목 없음"),
                        "agency": item.get("ntceInsttNm")
                        or item.get("dmanInsttNm", "발주기관 미상"),
                        "link": item.get(
                            "bidNtceDtlUrl",
                            f"https://www.g2b.go.kr:8081/ep/invitation/type1/bidInfoDtl.do?bidNo={item.get('bidNtceNo', '')}",
                        ),
                    })

            elif res_text.startswith("<"):
                root = ET.fromstring(res_text)
                items = root.findall(".//item")
                for item in items:
                    title = item.findtext("bidNtceNm") or "제목 없음"
                    agency = (
                        item.findtext("ntceInsttNm")
                        or item.findtext("dmanInsttNm")
                        or "발주기관 미상"
                    )
                    bid_no = item.findtext("bidNtceNo") or ""
                    url = (
                        item.findtext("bidNtceDtlUrl")
                        or f"https://www.g2b.go.kr:8081/ep/invitation/type1/bidInfoDtl.do?bidNo={bid_no}"
                    )
                    bids.append({"title": title, "agency": agency, "link": url})

            if bids:
                return bids

        raise ValueError("G2B API 결과 없음")

    except Exception as e:
        print(f"[G2B Fallback 우회 작동] 원인: {e}")
        try:
            fallback_query = f"나라장터 {keyword}"
            fallback_news = fetch_google_news(
                fallback_query, max_results=max_results
            )
            if isinstance(fallback_news, list):
                return [
                    {
                        "title": item.get("title", "제목 없음"),
                        "agency": f"우회수집({keyword})",
                        "link": item.get("link", ""),
                    }
                    for item in fallback_news
                    if isinstance(item, dict)
                ]
        except Exception:
            pass
        return []


def fetch_google_news_multi(keywords: list, max_per_keyword: int = 3) -> list:
    """여러 키워드로 뉴스를 순회 수집하고 중복 제거"""
    all_news = []
    seen_urls = set()

    for kw in keywords:
        news_list = fetch_google_news(kw, max_results=max_per_keyword)
        if isinstance(news_list, list):
            for item in news_list:
                if isinstance(item, dict) and item.get("link"):
                    if item["link"] not in seen_urls:
                        seen_urls.add(item["link"])
                        all_news.append(item)
    return all_news


def fetch_g2b_bids_multi(keywords: list, max_per_keyword: int = 3) -> list:
    """여러 키워드로 나라장터 공고를 순회 수집하고 중복 제거"""
    all_bids = []
    seen_urls = set()

    for kw in keywords:
        bid_list = fetch_g2b_bids(kw, max_results=max_per_keyword)
        if isinstance(bid_list, list):
            for item in bid_list:
                if isinstance(item, dict) and item.get("link"):
                    if item["link"] not in seen_urls:
                        seen_urls.add(item["link"])
                        all_bids.append(item)
    return all_bids


# ==========================================
# 3. Gemini 요약 및 발송 모듈
# ==========================================


def summarize_with_gemini(
    news_data: list, bid_data: list, max_retries: int = 5
) -> str:
    """Gemini API를 활용한 데이터 브리핑 요약 (503 재시도 적용)"""
    import time
    from google import genai

    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
    아래는 ODA 및 국제개발협력 관련 최신 수집 데이터입니다.

    [수집 뉴스]
    {news_data}

    [수집 입찰/공고]
    {bid_data}

    위 내용을 바탕으로 핵심 동향을 3~5개 항목으로 요약하여 깔끔한 마크다운 형식으로 작성해 주세요.
    """

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
            return response.text
        except Exception as e:
            if "503" in str(e) or "overloaded" in str(e):
                if attempt < max_retries - 1:
                    time.sleep(2**attempt)
                    continue
            raise e
    return "요약 생성에 실패했습니다."


def send_slack(title: str, text: str):
    """슬랙 웹훅 발송"""
    if not SLACK_WEBHOOK_URL:
        return
    payload = {"text": f"*{title}*\n\n{text}"}
    requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)


def send_email(title: str, text: str):
    """Gmail SMTP 이메일 발송"""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL]):
        return

    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = title
    msg.attach(MIMEText(text, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)