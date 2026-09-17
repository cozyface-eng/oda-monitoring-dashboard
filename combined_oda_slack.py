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


def fetch_g2b_bids(keyword, max_results=10):
    """
    나라장터 API를 통해 입찰공고 수집
    - 상세 실패 원인(HTTP Status, 공공데이터포털 Header, Exception)을 로그에 출력하도록 보완
    """
    service_key = os.getenv("G2B_API_KEY") or os.getenv("DATA_GO_KR_API_KEY")
    
    if not service_key:
        print(f"[G2B API Config Error] API 키가 설정되지 않았습니다. Secrets/환경변수를 확인하세요.")
        return []

    url = "http://apis.data.go.kr/1230000/BidPublicInfoService02/getBidPblcanInfoSearch01"
    
    params = {
        "serviceKey": service_key,
        "numOfRows": max_results,
        "pageNo": "1",
        "inptKd": "1",        # 공고명 검색
        "bidNtceNm": keyword, # 검색 키워드
        "type": "json"
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        
        # 1. HTTP 상태 코드 검증 (200이 아닌 경우 상세 출력)
        if response.status_code != 200:
            print(f"[G2B API HTTP Error] Status Code: {response.status_code} | Body: {response.text[:300]}")
            return []

        # 2. 응답 데이터 JSON 파싱
        try:
            data = response.json()
        except ValueError:
            print(f"[G2B API JSON Parsing Error] 응답이 JSON 형식이 아닙니다: {response.text[:300]}")
            return []

        # 3. 공공데이터포털 Header 에러 코드 검증
        response_body = data.get("response", {})
        header = response_body.get("header", {})
        result_code = header.get("resultCode")
        result_msg = header.get("resultMsg")

        if result_code != "00":
            print(f"[G2B API Service Error] 키워드: '{keyword}' | Code: {result_code} | Msg: {result_msg}")
            return []

        # 4. 아이템 추출
        items = response_body.get("body", {}).get("items", [])
        
        if isinstance(items, dict):
            items = [items]

        if not items:
            print(f"[G2B API Info] 키워드 '{keyword}' 검색 결과 데이터가 없습니다 (Empty items).")
            return []

        bids = []
        for item in items:
            bids.append({
                "title": item.get("bidNtceNm", "공고명 없음"),
                "link": item.get("bidNtceDtlUrl", "https://www.g2b.go.kr"),
                "agency": item.get("ntceInstNm", "발주기관 미상"),
                "date": item.get("bidNtceDt", "")[:10],
                "keyword": keyword
            })
            
        return bids

    except requests.exceptions.Timeout:
        print(f"[G2B API Timeout] 키워드 '{keyword}' 요청 중 타임아웃이 발생했습니다.")
        return []
    except requests.exceptions.RequestException as req_err:
        print(f"[G2B API Network Error] 키워드 '{keyword}' 요청 중 네트워크 에러 발생: {req_err}")
        return []
    except Exception as e:
        print(f"[G2B API Exception] 키워드 '{keyword}' 수집 중 알 수 없는 예외 발생: {type(e).__name__} - {e}")
        return []


def fetch_g2b_bids_multi(keywords, max_per_keyword=10):
    """
    다중 키워드 G2B 입찰공고 수집 및 중복 제거
    - TypeError: 'NoneType' object is not iterable 방지
    """
    all_bids = []
    seen_urls = set()

    for kw in keywords:
        bid_list = fetch_g2b_bids(kw, max_results=max_per_keyword) or []
        
        if not isinstance(bid_list, list):
            continue

        for item in bid_list:
            if isinstance(item, dict) and item.get("link") not in seen_urls:
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
                model="gemini-3.6-flash", contents=prompt
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