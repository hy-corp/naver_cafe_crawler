import re
import os
import pandas as pd
import json
import time
from datetime import datetime, timedelta

from oauth2client.service_account import ServiceAccountCredentials
import gspread

import warnings
import requests
from bs4 import BeautifulSoup
from bs4 import MarkupResemblesLocatorWarning
from cookie import get_naver_cookies

# === 추가: 비동기 ===
import asyncio
import aiohttp
from typing import Dict, Any, List, Tuple, Optional

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)


today_date = datetime.now()
start_date_total = datetime(2025, 8, 1)
end_date_total = datetime(2025, 9, 1)

cafes_to_scrape = {
    "토마스": 17175596,
    "수만휘": 10197921,
    "로물콘": 28699715,
}
boards_to_scrape = {
    17175596: [],
    10197921: [],
    28699715: [],
}

board_num_dict = {}
error_link = []

# =====================
# 구글시트
# ====================
googlesheet_url = "https://docs.google.com/spreadsheets/d/1sGCTNk_arqszCQgEeZ6yFdfh75qYSjqPkIx7gaZl5ho/edit?gid=0#gid=0"
gcp_api_key = "navercafe-crawler-aafd3370cb81.json"

def google_sheet(sheet="원본데이터", url=googlesheet_url, key=gcp_api_key):
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    credential = ServiceAccountCredentials.from_json_keyfile_name(key, scope)
    gc = gspread.authorize(credential)
    doc = gc.open_by_url(url)
    return doc.worksheet(sheet)

raw_sheet = google_sheet()
sheet_extract_data = raw_sheet.get_all_records()
existing_posts = {(row.get('카페'), str(row.get('게시글번호'))) for row in sheet_extract_data}
print("구글시트 연결 완료")

# =====================
# 공통 헤더
# ====================
my_cookie = get_naver_cookies()
headers = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/140.0.0.0 Safari/537.36",
    "cookie": my_cookie,
    "accept": "*/*",
    "accept-language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://cafe.naver.com",
    "referer": "https://cafe.naver.com/",
    "x-cafe-product": "pc"
}

def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)

# =====================
# 비동기 상세 수집
# =====================
async def fetch_article_json(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status >= 400:
                return None, f"HTTP {resp.status}", None
            try:
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    return data, None, None
                return None, "JSONNotDict", None
            except Exception:
                head = (await resp.text())[:200]
                return None, "JSONDecodeError", head
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", None

def parse_article_data(cafe_name, cafe_id, article, menu_id, data):
    if data.get('errorCode') == '0004':
        raise ValueError("LoginError(0004)")

    result = data.get('result', {})
    article_data = result.get('article', {})

    title = article_data.get('subject', '제목 없음')
    html_content = article_data.get('contentHtml') or result.get('scrap', {}).get('contentHtml', '')
    article_content = BeautifulSoup(html_content, 'html.parser').get_text(strip=True, separator='\n')

    comments = result.get('comments', {}).get('items', [])
    comments_list = [BeautifulSoup(c.get('content', ''), 'html.parser').get_text(strip=True, separator='\n')
                     for c in comments]

    posting_time = article_data.get('writeDate')
    posting_date = datetime.fromtimestamp(posting_time/1000).strftime("%Y-%m-%d") if posting_time else datetime.now().strftime("%Y-%m-%d")

    return {
        '카페': cafe_name,
        '날짜': posting_date,
        '제목': title,
        '본문': article_content,
        '댓글(줄당1개)': "\n".join(comments_list),
        '게시글번호': article
    }

async def fetch_articles_concurrently(cafe_name, cafe_id, menu_id, article_id_list, base_headers, final_list_of_dicts, concurrency=25):
    sem = asyncio.Semaphore(concurrency)
    h = dict(base_headers)
    h["referer"] = f"https://cafe.naver.com/f-e/cafes/{cafe_id}/menus/{menu_id}"

    async with aiohttp.ClientSession(headers=h, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        async def _one(article):
            article_url = f"https://article.cafe.naver.com/gw/v3/cafes/{cafe_id}/articles/{article}?query=&menuId={menu_id}&useCafeId=true&requestFrom=A"
            async with sem:
                data, err, head = await fetch_article_json(session, article_url)
                if err:
                    error_link.append(article_url)
                    return
                try:
                    parsed = parse_article_data(cafe_name, cafe_id, article, menu_id, data)
                    final_list_of_dicts.append(parsed)
                except Exception:
                    error_link.append(article_url)
        await asyncio.gather(*[_one(a) for a in article_id_list])


# =====================
# 비동기 게시판 수집
# =====================
async def fetch_board_articles(cafe_name, cafe_id, menu_id,
                                 chunk_start_date, chunk_end_date,
                                 last_page_seen, existing_posts,
                                 final_list_of_dicts,
                                 PAGE_RECORD_FILE, EMPTY_BOARD_FILE,
                                 lock: asyncio.Lock):

    key = f"{cafe_id}:{menu_id}"
    article_id_list = []
    should_stop = False

    async with aiohttp.ClientSession(headers=headers, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        for page in range(last_page_seen.get(key, 1), 1000):
            url = f"https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafe_id}/menus/{menu_id}/articles?page={page}&sortBy=TIME"
            try:
                async with session.get(url, timeout=5) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    articles = data['result']['articleList']
                    if not articles:
                        # 빈 게시판 처리
                        empty_boards = set()
                        if os.path.exists(EMPTY_BOARD_FILE):
                            with open(EMPTY_BOARD_FILE, "r", encoding="utf-8") as f:
                                empty_boards = set(json.load(f))
                        empty_boards.add(key)
                        with open(EMPTY_BOARD_FILE, "w", encoding="utf-8") as f:
                            json.dump(list(empty_boards), f, ensure_ascii=False)
                        break

                    for item in articles:
                        post_time = datetime.fromtimestamp(item['item']['writeDateTimestamp']/1000)
                        if post_time < chunk_start_date:
                            should_stop = True
                            last_page_seen[key] = page + 1
                            os.makedirs(os.path.dirname(PAGE_RECORD_FILE), exist_ok=True)
                            with open(PAGE_RECORD_FILE, "w", encoding="utf-8") as f:
                                json.dump(last_page_seen, f, ensure_ascii=False)
                            break
                        aid = item['item']['articleId']
                        if (cafe_name, str(aid)) not in existing_posts:
                            article_id_list.append(aid)
                    if should_stop:
                        break
            except Exception:
                break

    if article_id_list:
        await fetch_articles_concurrently(
            cafe_name, cafe_id, menu_id,
            article_id_list, headers,
            final_list_of_dicts, concurrency=30
        )

# ====================
# 크롤링 시작
# ====================
current_end_date = end_date_total
while current_end_date > start_date_total:
    final_list_of_dicts = []
    chunk_end_date = current_end_date
    chunk_start_date = current_end_date - timedelta(days=1)

    print(f"\n{'='*20}")
    print(f"--- 수집 기간: {chunk_start_date.date()} ~ {chunk_end_date.date()} ---")
    print(f"{'='*20}\n")

    PAGE_RECORD_FILE = "page/last_pages.json"
    if os.path.exists(PAGE_RECORD_FILE):
        with open(PAGE_RECORD_FILE, "r") as f:
            last_page_seen = json.load(f)
    else:
        last_page_seen = {}

    EMPTY_BOARD_FILE = "page/empty_boards.json"

    for cafe_name, cafe_id in cafes_to_scrape.items():
        file_write_lock = asyncio.Lock()

        # 카페 게시판 목록 조회
        resp = requests.get(f"https://apis.naver.com/cafe-web/cafe-cafemain-api/v1.0/cafes/{cafe_id}/menus", headers=headers)
        all_menus = resp.json().get('result', {}).get('menus', [])
        all_menus.extend(resp.json().get('result', {}).get('linkMenus', []))

        # boards_to_scrape : 원하는 게시판에서 게시판 이름 뽑아오기
        target_board_names = boards_to_scrape.get(cafe_id, [])

        # 원하는 게시판이 없으면 전체 게시판 이름 : id 형태로 수집
        if not target_board_names:
            board_ids = [m.get('menuId') for m in all_menus]
            print(f"카페 {cafe_name} 전체 게시판 {len(board_ids)}개 수집합니다.")
        else:
        # 원하는 게시판이 있으면, 해당 게시판의 이름 :id 수집
            board_ids = [m.get('menuId') for m in all_menus if m.get('name') in target_board_names]


        tasks = [
            fetch_board_articles(cafe_name, cafe_id, bid,
                                 chunk_start_date, chunk_end_date,
                                 last_page_seen, existing_posts,
                                 final_list_of_dicts,
                                 PAGE_RECORD_FILE, EMPTY_BOARD_FILE,
                                 file_write_lock) 
            for bid in board_ids if bid is not None # bid가 None이 아닌 경우만 처리
        ]

        # 작업을 실행하고 기다리는 메인 코루틴 정의
        async def main():
            await asyncio.gather(*tasks)

        # 메인 코루틴을 asyncio.run으로 실행
        if tasks: # 실행할 작업이 있을 때만 실행
            try:
                asyncio.run(main())
            except RuntimeError as e:
                # Jupyter Notebook 등에서 루프가 이미 실행 중일 때 처리
                if "cannot run loop while another loop is running" in str(e):
                    import nest_asyncio
                    nest_asyncio.apply()
                    asyncio.run(main())
                else:
                    raise e

# ====================
# 구글시트 업로드
# ====================
    if final_list_of_dicts:
        cafe_data = pd.DataFrame(final_list_of_dicts).sort_values(by='날짜', ascending=False)
        raw_sheet.update([cafe_data.columns.tolist()])
        raw_sheet.append_rows(cafe_data.values.tolist(), value_input_option='USER_ENTERED')
        print(f"\n✅ 기간 [{chunk_start_date.date()} ~ {chunk_end_date.date()}] 동안 {len(final_list_of_dicts)} 건의 새 데이터를 저장했습니다.")
    else:
        print(f"\n- 기간 [{chunk_start_date.date()} ~ {chunk_end_date.date()}] 동안 수집된 새 데이터가 없습니다.")

    current_end_date -= timedelta(days=1)
    time.sleep(3)
