# cookie.py
import os, sys, time, tempfile
from typing import Optional
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

LOGIN_URL = "https://nid.naver.com/nidlogin.login"

def get_naver_cookies(headless: bool = False) -> Optional[str]:
    """
    네이버 로그인 후 쿠키 문자열 반환. 실패 시 None.
    .env의 NAVER_ID, NAVER_PW 사용.
    CI(GitHub Actions)에서도 안전하게 돌아가도록 구성.
    """
    load_dotenv()
    NAVER_ID = os.getenv("NAVER_ID")
    NAVER_PW = os.getenv("NAVER_PW")
    if not NAVER_ID or not NAVER_PW:
        print("환경변수 NAVER_ID/NAVER_PW가 없습니다.")
        return None

    # OS별 단축키는 쓰지 않고(클립보드 X), JS로 직접 값 주입
    modifier_key = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
    is_ci = os.getenv("GITHUB_ACTIONS") == "true"

    driver = None
    try:
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1280,960")

        # 실행마다 "고유" 프로필 경로 → 락/충돌 방지
        unique_dir = os.path.join(tempfile.gettempdir(), f"chrome-user-data-{time.time_ns()}")
        options.add_argument(f"--user-data-dir={unique_dir}")

        # CI 안정화 플래그
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--remote-debugging-port=0")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        wait = WebDriverWait(driver, 20)

        # 1) 로그인 페이지
        driver.get(LOGIN_URL)

        # 2) 요소 대기
        id_input = wait.until(EC.presence_of_element_located((By.ID, "id")))
        pw_input = wait.until(EC.presence_of_element_located((By.ID, "pw")))

        # 3) JS로 직접 주입(헤드리스/서버에서 가장 안정적)
        driver.execute_script("arguments[0].value = arguments[1];", id_input, NAVER_ID)
        driver.execute_script("arguments[0].value = arguments[1];", pw_input, NAVER_PW)

        # 4) 로그인 버튼 클릭(여러 후보)
        clicked = False
        for by, sel in [
            (By.ID, "log.login"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[contains(., '로그인')]"),
        ]:
            try:
                btn = wait.until(EC.element_to_be_clickable((by, sel)))
                btn.click()
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            pw_input.send_keys(Keys.ENTER)

        # 5) 전환 대기/캡차 우회 불가 시 타임아웃
        end = time.time() + 90
        while time.time() < end and "nidlogin.login" in driver.current_url:
            time.sleep(0.5)

        if "nidlogin.login" in driver.current_url:
            print("로그인 실패(캡차/2단계 인증 미완료 가능).")
            return None

        # 6) 쿠키 추출
        cookies = driver.get_cookies()
        cookie_string = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
        return cookie_string or None

    except Exception as e:
        print(f"Selenium 오류: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
