import os
import json
import requests
import feedparser
import streamlit as st
from datetime import datetime

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
G2B_SERVICE_KEY = get_secret("G2B_SERVICE_KEY") or get_secret("G2B_API_KEY") or get_secret("DATA_GO_KR_API_KEY")
SENDER_EMAIL = get_secret("SENDER_EMAIL")
SENDER_PASSWORD = get_secret("SENDER_PASSWORD")
RECEIVER_EMAIL = get_secret("RECEIVER_EMAIL")


# ==========================================
# 2. 구글 뉴스 수집 관련 함수
# ==========================================
def fetch_google_news(keyword, max_results=10):
    """구글 뉴스 RSS를 통해 키워드 관련 뉴스 수집"""
    encoded_kw = requests.utils.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_kw}&hl=ko&gl=KR&ceid=KR:ko"
    
    try:
        feed = feedparser.parse(rss_url)
        news_list = []
        for entry in feed.entries[:max_results]:
            news_list.append({
                "title": entry.get("title", "제목 없음"),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": entry.get("source", {}).get("title", "Google News"),
                "keyword": keyword
            })
        return news_list
    except Exception as e:
        print(f"[Google News Exception] 키워드 '{keyword}' 수집 중 예외 발생: {type(e).__name__} - {e}")
        return []


def fetch_google_news_multi(keywords, max_per_keyword=10):
    """다중 키워드 구글 뉴스 수집 및 중복 제거"""
    all_news = []
    seen_urls = set()
    
    for kw in keywords:
        news_items = fetch_google_news(kw, max_results=max_per_keyword) or []
        for item in news_items:
            if isinstance(item, dict) and item.get("link") not in seen_urls:
                seen_urls.add(item["link"])
                all_news.append(item)
                
    return all_news


# ==========================================
# 3. 나라장터(G2B) 입찰공고 수집 관련 함수
# ==========================================
def fetch_g2b_bids(keyword, max_results=10):
    """
    나라장터 API를 통해 입찰공고 수집
    - 상세 실패 원인(HTTP Status, 공공데이터포털 Header, Exception)을 로그에 출력
    """
    if not G2B_SERVICE_KEY:
        print(f"[G2B API Config Error] API 키(G2B_SERVICE_KEY)가 설정되지 않았습니다. Secrets를 확인하세요.")
        return []

    url = "http://apis.data.go.kr/1230000/BidPublicInfoService02/getBidPblcanInfoSearch01"
    
    params = {
        "serviceKey": G2B_SERVICE_KEY,
        "numOfRows": max_results,
        "pageNo": "1",
        "inptKd": "1",        # 공고명 검색
        "bidNtceNm": keyword, # 검색 키워드
        "type": "json"
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        
        # 1. HTTP 상태 코드 검증
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
    - TypeError: 'NoneType' object is not iterable 예방
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
# 4. Gemini 요약 및 슬랙 발송 함수
# ==========================================
def summarize_with_gemini(news_data, bid_data):
    """Gemini API를 사용하여 뉴스 및 입찰공고 요약 생성"""
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEY가 설정되지 않아 요약을 생성할 수 없습니다."

    news_text = "\n".join([f"- {n['title']} ({n['source']})" for n in news_data[:10]])
    bid_text = "\n".join([f"- {b['title']} ({b['agency']})" for b in bid_data[:10]])

    prompt = f"""
    아래 수집된 ODA 관련 정보와 입찰공고를 바탕으로 핵심 요약을 작성해 주세요.

    [주요 뉴스]
    {news_text if news_text else "수집된 뉴스 없음"}

    [주요 입찰공고]
    {bid_text if bid_text else "수집된 입찰공고 없음"}
    """

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"[Gemini Exception] 요약 생성 중 오류 발생: {e}")
        return f"요약 생성 실패: {e}"


def send_slack_message(summary_text, news_data, bid_data):
    """슬랙 웹훅을 통해 결과 알림 발송"""
    if not SLACK_WEBHOOK_URL:
        print("[Slack Error] SLACK_WEBHOOK_URL이 설정되지 않았습니다.")
        return False

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📢 ODA 모니터링 및 입찰공고 리포트"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🤖 AI 핵심 요약*\n{summary_text}"
            }
        },
        {"type": "divider"}
    ]

    payload = {"blocks": blocks}

    try:
        res = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(payload), headers={"Content-Type": "application/json"})
        if res.status_code == 200:
            print("슬랙 발송 완료")
            return True
        else:
            print(f"[Slack Error] Status: {res.status_code}, Body: {res.text}")
            return False
    except Exception as e:
        print(f"[Slack Exception] 슬랙 발송 중 예외 발생: {e}")
        return False