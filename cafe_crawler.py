import requests
import json
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from bs4 import BeautifulSoup
from tqdm import tqdm

import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
import gspread


'''
fixed_day와 cafes_to_scrape(카페id), boards_to_scrape(게시판 이름)을 지정하면 해당 정보 크롤링

'''

today = datetime.now() # 오늘 날짜
fixed_day = datetime(2025, 9, 16) # 현재 날짜에서 3개월 전 날짜를 계산

# 크롤링할 카페 이름과 ID
cafes_to_scrape = {
    "로물콘": 28699715,
    # "다른카페이름": 12345678, # 다른 카페를 추가할 수 있습니다.
}

# 크롤링할 카페ID와 게시판 이름
boards_to_scrape = {
    28699715: ['통합입시정보', '논술면접정보'],
    # 카페ID: ['게시판1', '게시판2'], # 다른 카페의 게시판 리스트
}


board_num_dict = {}      # 게시판 {"게시판이름":"게시판id"}
error_link = []          # 크롤링 실패한 링크
final_list_of_dicts = [] # 크롤링 담길 리스트

# 카페의 게시판 이름을 불러오기 위한 헤더
cafe_headers = {
    'authority': 'apis.naver.com',
    'method': 'GET',
    'path': '/cafe-web/cafe-cafemain-api/v1.0/cafes/28699715/menus',
    'scheme': 'https',
    'accept': '*/*',
    'accept-encoding': 'gzip, deflate, br, zstd',
    'accept-language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'cookie': 'NAC=qnVlBwQjui0p; NNB=JKRF7DSCW7EGQ; NACT=1; page_uid=jKBx4dqVOswss4cJnIZssssstI4-178201; ASID=31afc8760000019950edfd9b0000001d; nid_inf=1782000082; NID_AUT=y6xu0T4jbVu6S2l6eG13W3+x/G2QcSx9xyFd9idJIrvNyg4VCRNtua/z+0YkAAsO; NID_SES=AAABuc/zkUmGuvOOIygFXU1y41U8EC3Ld4XYI2o6DFZq6NH547TtYdkIJASth+P5jm+9Ggopv5VdzQai/PwLz6JUL7EeQZkZyXvmIT1SMmQ1JQsTnF7XLQE/XEusrFDIb5Kp3qdPNYZNRNbHce28EMiGwyNRFtHIPFnMc4QSQCRck5OCRVQvOCRsaW9NF4JAMih3ANa4ye5c+6POZJmMA5qYCDq8XAELfkC1B25EKSffLXXD/oA9VYvc9BJfK9lb98UMkUbX0wNNfZnAf/6mWE6zPTlfCV7PnLBc0GHCYKhQGbE5Gbt94OGWCoiU+xjQXf8RPGeu6YDgV2MNLUXxI0IPFrOK58NOZDMr5OzYe8mXqyqqkqJyU0lKWulpPEghGxWvpsVhcpzFNHb7RXRV45gFKrqj2sdgO2vz1tCWI3ZpqpJs39boyfkiFFxSDtsNKiHBdaYhAHcUpwzTixszB8zb5Hj6BORUWY55ZlSSqCYOjz/0BL2vJ1nxPib5+z/lOOWaW+NU+0r86VXyxqCRq+HoRp76jJrPeeCZWKtM3Xm0FO/OmyxmL0nRbJYpf6SJNqrIC4tEsiOewV10gQCl9jzn02Y=; BUC=tODZmuoFZZtOZytqIyS3dNCBtFWb4YQWupCeE4YpHC8=',
    'origin': 'https://cafe.naver.com',
    'priority': 'u=1, i',
    'referer': 'https://cafe.naver.com/f-e/cafes/28699715/menus/21',
    'sec-ch-ua': '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
    'x-cafe-product': 'pc'
}

# 나머지 일반 헤더
headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

# 카페별 게시판ID를 수집합니다.
for cafe_name, cafe_id in cafes_to_scrape.items():
    
    url = f"https://apis.naver.com/cafe-web/cafe-cafemain-api/v1.0/cafes/{cafe_id}/menus"
    
    try:
        response = requests.get(url, headers=cafe_headers)
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
            print("수집할 게시판 이름이 지정되지 않았습니다.")
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


    # 게시판에서 게시글의 게시번호를 수집하여 article_id_list에 담습니다.
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
                        try:
                            article_id = response.json().get('result')['articleList'][num]['item']['articleId']
                            article_id_list.append(article_id)
                        except:
                                continue

                # 지정 날짜가 지나면 해당 게시판 작업을 멈춘다
                if should_stop:
                    break
            except:
                break

        
        # 수집된 게시글의 작성날짜, 제목, 본문, 댓글을 수집합니다.
        for article in tqdm(article_id_list):
            article_url = f"https://article.cafe.naver.com/gw/v3/cafes/{cafe_id}/articles/{article}?query=&menuId={ids}&useCafeId=true&requestFrom=A"

            try:
                response = requests.get(article_url, headers=headers)
                response.raise_for_status() 
                data = response.json()

                # --- 에러 처리 로직 ---
                # JSON 응답의 최상위에 'errorCode' 키가 있는지 확인
                if data.get('errorCode') == '0004':
                    print(f"게시글 ID {article}: 에러코드 0004 발생, 건너뜁니다.")
                    continue # 다음 게시글로 넘어감

                # --- 게시글 데이터 추출 ---
                result = data.get('result', {})
                article_data = result.get('article', {})
                
                # 게시글 제목 추출
                title = article_data.get('subject', '제목 없음')

                # 본문 HTML 추출 (article 또는 scrap 키 확인)
                html_content = article_data.get('contentHtml')
                if not html_content:
                    html_content = result.get('scrap', {}).get('contentHtml', '')
                    
                # HTML에서 텍스트만 추출
                article_content = BeautifulSoup(html_content, 'html.parser').get_text(strip=True, separator='\n')

                # 댓글 추출
                comments = result.get('comments', {}).get('items', [])
                comments_list = [BeautifulSoup(comment.get('content', ''), 'html.parser').get_text(strip=True, separator='\n')
                                 for comment in comments]
                

                posting_time = response.json()['result']['article']['writeDate']/1000 # 밀리초를 초 단위로 변환
                posting_date = datetime.fromtimestamp(posting_time).strftime("%Y-%m-%d")
                

                # --- 데이터 저장 ---
                # 게시글 하나의 정보를 딕셔너리에 담아 리스트에 추가합니다.
                article_info = {
                    'date' : posting_date,
                    'title': title,
                    'content': article_content,
                    'comment': comments_list
                }
                final_list_of_dicts.append(article_info)

                time.sleep(0.1)

            except requests.exceptions.RequestException as e:
                error_link.append(article_url)
                continue
            except (KeyError, json.JSONDecodeError) as e:
                print(f"JSON 파싱 중 오류 발생 (게시글 ID {article}): {e}")
                continue
    

        # 게시판 ID(ids)에 해당하는 이름을 찾습니다.
        board_name = [name for name, id in board_num_dict.items() if id == ids][0]

        # 원하는 문구를 출력합니다.
        print(f"[{cafe_name}] '{board_name}' 게시판에서 총 {len(article_id_list)}개 게시글 수집 완료.")
        print('')
        print('-'*100)


cafe_data = pd.DataFrame(final_list_of_dicts)

# ----------------------------------- 구글 시트 업로드 -----------------------------------------

# 인증 및 스프레드시트 설정
scope = ['https://spreadsheets.google.com/feeds',
         'https://www.googleapis.com/auth/drive']

# 다운로드 받았던 키 값
json_key_path = "NAVER_CRAWLER_GOOGLESHEET_KEY.json"  

credential = ServiceAccountCredentials.from_json_keyfile_name(json_key_path, scope)
gc = gspread.authorize(credential)

# 개인에 따라 수정 필요 - 스프레드시트 URL
spreadsheet_url = "https://docs.google.com/spreadsheets/d/1sGCTNk_arqszCQgEeZ6yFdfh75qYSjqPkIx7gaZl5ho/edit?gid=0#gid=0"

doc = gc.open_by_url(spreadsheet_url)

# 개인에 따라 수정 필요 - 시트 선택
sheet = doc.worksheet("시트1")

# 기존 시트의 내용을 모두 삭제합니다.
sheet.clear()

# 데이터프레임의 헤더(열 이름)와 값을 구글 시트에 업로드합니다.
sheet.update([cafe_data.columns.values.tolist()] + cafe_data.values.tolist())

print('-'*100)
print("`cafe_data.csv` 파일이 구글 시트에 성공적으로 업로드되었습니다.")