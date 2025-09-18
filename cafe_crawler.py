import re
import pandas as pd
import json

import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

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
import asyncio

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)





'''
fixed_day와 cafes_to_scrape(카페id), boards_to_scrape(게시판 이름)을 지정하면 해당 정보 크롤링
'''
# 수집할 날짜 지정
today = datetime.now()
fixed_day = datetime(2025, 9, 18)

# 크롤링할 카페 이름과 ID - "카페이름": 12345678
cafes_to_scrape = {
    "로물콘": 28699715,
}

# 크롤링할 카페ID와 게시판 이름 - 카페ID: ['게시판1', '게시판2']
boards_to_scrape = {
    28699715: [],
}

# 값 담을 값들 정의
board_num_dict = {}      # 게시판 {"게시판이름":"게시판id"}
error_link = []          # 크롤링 실패한 링크
final_list_of_dicts = [] # 크롤링 담길 리스트

# 구글시트 url, gcp 인증키
googlesheet_url = "https://docs.google.com/spreadsheets/d/1sGCTNk_arqszCQgEeZ6yFdfh75qYSjqPkIx7gaZl5ho/edit?gid=0#gid=0"
gcp_api_key = "navercafe-crawler-aafd3370cb81.json"

def google_sheet(sheet ="원본데이터", url=googlesheet_url, key = gcp_api_key):
    '''
    구글시트에서 시트 정보를 가져오는 코드입니다.
    google_sheet("시트이름", "주소")를 가져오시면 해당 시트로 연겯룁니다.
    '''
    scope = ['https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive']
    credential = ServiceAccountCredentials.from_json_keyfile_name(key, scope)
    gc = gspread.authorize(credential)
    doc = gc.open_by_url(url)
    sheet = doc.worksheet(sheet)
    return sheet

# 구글시트 원본시트에 연결
raw_sheet = google_sheet()
# 구글시트 원본시트 데이터 json으로 가져오기
sheet_extract_data = raw_sheet.get_all_records()
# (카페, 게시글번호)형태로 이미 크롤링한 값 필터 생성
existing_posts = {(row.get('카페'), str(row.get('게시글번호'))) for row in sheet_extract_data}
print("구글시트 연결 완료")

# 카페의 게시판 이름을 불러오기 위한 헤더
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

# =========================
# (추가) 비동기 헬퍼들
# =========================
def run_async(coro):
    """
    이미 실행 중인 이벤트 루프가 있으면 그 위에서 실행하고,
    없으면 asyncio.run으로 실행한다.
    (Jupyter 등에서 안전하게 동작)
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 노트북/스트림릿 등: 재진입 허용
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)

async def fetch_article_json(session: aiohttp.ClientSession, url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    상세 실패 오류를 써주는 코드
    return: (data, err_reason, text_head_if_decode_err)
    """
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
    except asyncio.TimeoutError:
        return None, "Timeout", None
    except aiohttp.ClientError as ce:
        return None, f"ClientError: {ce}", None
    except Exception as e:
        return None, f"UnknownError: {e}", None

def parse_article_data(cafe_name: str, cafe_id: int, article: int, menu_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    
    '''
    수집된 게시글 번호로 각각의 데이터를 최종수집하는 함수입니다.
    '''

    # 로그인 에러 처리
    if data.get('errorCode') == '0004':
        raise ValueError("LoginError(0004)")

    result = data.get('result', {})
    article_data = result.get('article', {})

    # 제목
    title = article_data.get('subject', '제목 없음')

    # 본문 (HTML→텍스트)
    html_content = article_data.get('contentHtml')
    if not html_content:
        html_content = result.get('scrap', {}).get('contentHtml', '')
    article_content = BeautifulSoup(html_content, 'html.parser').get_text(strip=True, separator='\n')

    # 댓글
    comments = result.get('comments', {}).get('items', [])
    comments_list = [BeautifulSoup(comment.get('content', ''), 'html.parser').get_text(strip=True, separator='\n')
                     for comment in comments]

    # 날짜
    posting_time = article_data.get('writeDate')
    if posting_time:
        posting_date = datetime.fromtimestamp(posting_time/1000).strftime("%Y-%m-%d")
    else:
        posting_date = datetime.now().strftime("%Y-%m-%d")

    return {
        '카페': cafe_name,
        '날짜': posting_date,
        '제목': title,
        '본문': article_content,
        '댓글(줄당1개)': "\n".join(comments_list),
        '게시글번호': article
    }

async def fetch_articles_concurrently(cafe_name: str, cafe_id: int, menu_id: int,
                                      article_id_list: List[int], base_headers: Dict[str, str],
                                      concurrency: int = 25):
    """
    게시글 상세들을 비동기로 병렬 수집하여 final_list_of_dicts, error_link를 갱신
    (전역 리스트 사용 → 기존 코드와의 호환 유지)
    """
    sem = asyncio.Semaphore(concurrency)

    # 게시판별 referer를 맞춰주면 안정적
    h = dict(base_headers)
    h["referer"] = f"https://cafe.naver.com/f-e/cafes/{cafe_id}/menus/{menu_id}"

    async with aiohttp.ClientSession(headers=h, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:

        async def _one(article: int):
            article_url = f"https://article.cafe.naver.com/gw/v3/cafes/{cafe_id}/articles/{article}?query=&menuId={menu_id}&useCafeId=true&requestFrom=A"
            async with sem:
                data, err, head = await fetch_article_json(session, article_url)
                if err:
                    if err == "JSONDecodeError" and head:
                        print(f"JSONDecodeError (게시글 {article}): JSON 아님. 응답 앞부분 ↓\n{head}")
                    else:
                        print(f"요청 에러 (게시글 {article}): {err} / URL: {article_url}")
                    error_link.append(article_url)
                    return

                try:
                    parsed = parse_article_data(cafe_name, cafe_id, article, menu_id, data)
                    final_list_of_dicts.append(parsed)
                except ValueError as ve:
                    # LoginError(0004) 등
                    print(f"게시글 ID {article}: {ve}, 건너뜁니다.")
                    error_link.append(article_url)
                except KeyError as ke:
                    print(f"KeyError (게시글 {article}): {ke}, data keys: {list(data.keys())}")
                    error_link.append(article_url)
                except Exception as e:
                    print(f"ParseError (게시글 {article}): {e}")
                    error_link.append(article_url)

        # 진행바는 동기 tqdm과 충돌될 수 있어 간단히 gather만
        await asyncio.gather(*[_one(a) for a in article_id_list])

# -----------------------카페별 게시판ID를 수집합니다.-------------------------------------
for cafe_name, cafe_id in cafes_to_scrape.items():
    url = f"https://apis.naver.com/cafe-web/cafe-cafemain-api/v1.0/cafes/{cafe_id}/menus"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        data = response.json()

        all_menus = data.get('result', {}).get('menus', [])
        all_menus.extend(data.get('result', {}).get('linkMenus', []))

        # 해당 카페에서 원하는 게시판 이름 리스트를 가져옵니다.
        target_board_names = boards_to_scrape.get(cafe_id, [])
        board_ids = [] # 카페 id가 담길 리스트

        if not all_menus:
            print("메뉴 목록을 찾을 수 없습니다.")
        elif not target_board_names:
            # 리스트가 비어있으면 board_ids에 0만 추가
            board_ids.append(0)
            print(f"카페 {cafe_name}에 지정된 게시판이 없으므로 '전체글'을 수집합니다.")
        else:
            for menu in all_menus:
                menu_id = menu.get('menuId')
                menu_name = menu.get('name')

                board_num_dict[menu_name] = menu_id

                # 게시판 이름에 맞는 카페 id 추출
                if menu_name in target_board_names:
                    board_ids.append(menu_id)

    except requests.exceptions.RequestException as e:
        print(f"[{cafe_name}] URL 요청 중 오류 발생: {e}")
    except json.JSONDecodeError as e:
        print(f"[{cafe_name}] JSON 파싱 오류: {e}")

    # --------------------게시판에서 게시글의 게시번호를 수집하여 article_id_list에 담습니다.-----------------------------
    for ids in board_ids:
        article_id_list = []  # 게시물 id를 담을 리스트
        should_stop = False   # 페이지 반복을 멈추기 위한 플래그 변수 추가

        # 페이지 번호는 임의로 큰 값 5000 지정 (지정 날짜 도달시 자동으로 멈추는 코드가 존재하므로)
        for page in range(1,5000):
            menu_url = f"https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafe_id}/menus/{ids}/articles?page={page}&sortBy=TIME"

            try:
                response = requests.get(menu_url, headers=headers)
                response.raise_for_status()

                list_per_post = len(response.json()['result']['articleList']) # 목록의 게시글 수
                for num in range(list_per_post):
                    post_timestamp = response.json()['result']['articleList'][num]['item']['writeDateTimestamp']
                    post_time =  post_timestamp/1000 # 밀리초를 초 단위로 변환
                    post_day = datetime.fromtimestamp(post_time)

                    # 지정 날짜가 지나면 목록 크롤링을 멈춘다.
                    if fixed_day > post_day:
                        should_stop = True
                        break
                    else:
                        article_id = response.json().get('result')['articleList'][num]['item']['articleId']

                        if (cafe_name, str(article_id)) in existing_posts:
                            break
                        else:
                            article_id_list.append(article_id)

                # 지정 날짜가 지났거나 중복 게시글 발견시 해당 게시판 작업을 멈춘다
                if should_stop:
                    break
            except Exception:
                break

        # -------------- (변경) 상세 수집: 비동기 병렬 -------------------------
        if article_id_list:
            print(f"[{cafe_name}] 메뉴 {ids} 상세 {len(article_id_list)}건 병렬 수집 시작...")
            run_async(fetch_articles_concurrently(
                cafe_name=cafe_name,
                cafe_id=cafe_id,
                menu_id=ids,
                article_id_list=article_id_list,
                base_headers=headers,
                concurrency=30,
            ))

        # ----------------------- 게시판 ID(ids)에 해당하는 이름을 찾습니다. ----------------------------
        if ids == 0:
            board_name = '전체글'  # ID가 0일 때 사용할 게시판 이름
        else:
            board_name = [name for name, id in board_num_dict.items() if id == ids][0] if board_num_dict else f"menu:{ids}"

        # 원하는 문구를 출력합니다.
        succ_cnt = sum(1 for r in final_list_of_dicts if (r['카페']==cafe_name))
        print(f"[{cafe_name}] '{board_name}' 게시판에서 총 {succ_cnt}개 게시글 수집 누적.")

# =========================
# 업로드
# =========================
if final_list_of_dicts:
    cafe_data = pd.DataFrame(final_list_of_dicts)
    cafe_data = cafe_data.sort_values(by='날짜', ascending=False)
    raw_sheet.update([cafe_data.columns.tolist()])
    values_to_append = cafe_data.values.tolist()
    raw_sheet.append_rows(values_to_append, value_input_option='USER_ENTERED')
    print(f"총 {len(values_to_append)}개의 새 데이터를 구글시트에 성공적으로 추가했습니다.")
    print(f'구글시트 업로드 완료 (수집실패 갯수 : {len(error_link)}개)')
else:
    print(f"수집할 데이터가 없습니다 (수집실패 갯수 : {len(error_link)}개)")
