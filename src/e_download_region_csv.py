"""E 階段：以 Selenium 開啟 Spotify Charts，人工登入後下載各地區每週榜單 CSV 到 data/。

注意：必須由人手動在開啟的 Chrome 視窗登入，請勿改成 headless。
"""

import calendar
import logging
import random  # 延遲操作
import time
from datetime import date, datetime, timezone
from pathlib import Path

from selenium import webdriver  # 引入webdriver，webdriver為瀏覽器主控權
from selenium.webdriver.chrome.options import Options  # 用來作Chrome設定
from selenium.webdriver.chrome.service import Service  # 用來管理driver，例如安裝路徑
from selenium.webdriver.common.by import By  # 用來指定元素的定位方式
from selenium.webdriver.support import expected_conditions as EC  # 描述持續等待的條件
from selenium.webdriver.support.ui import WebDriverWait  # 等待後才執行

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_BUTTON_SELECTOR = "#__next > div > div:nth-child(3) > div > div > div.styled__CSVLink-sc-135veyd-5.hJopSD > span > span > button"


def get_uri(region: str, year: int, month: int) -> list[str]:
    """由於spotify chart網站設定“當周週五-下週四”為一週的榜單計算範疇，故定義一函式，
    用以確定目前這一個月有幾個星期五，而有幾個星期五就代表當月有幾週榜單、
    應該要下載幾次csv檔，同時也能知道每週的榜單結束在哪一個週四，根據先前調查，
    週四的日期會連動到url網址串。所以我們可以推算url，並包裝在list中回傳。"""
    output = []
    today = datetime.now(tz=timezone.utc).date()
    for a_week in calendar.monthcalendar(year, month):
        if a_week[3] != 0:  # [3]代表週四，0 代表這個週四屬於上個月
            week_end_at = date(year, month, a_week[3])
            # 今天的日期一定要晚於上一週榜單結束日，才會有合法的榜單 URL
            if week_end_at < today:
                url = f"https://charts.spotify.com/charts/view/regional-{region}-weekly/{year}-{month:02}-{a_week[3]:02}"
                output.append(url)
    return output


def download_btn_click(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """模擬人類捲動頁面並點擊 CSV 下載鈕。"""
    driver.execute_script("window.scrollTo(0, 500);")
    time.sleep(random.uniform(2, 4))

    csv_btn = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, CSV_BUTTON_SELECTOR))
    )
    csv_btn.click()
    # 考量下載速度可能因當下網速而不一，所以設定睡覺秒數較久
    time.sleep(random.uniform(4, 8))


def _build_driver() -> webdriver.Chrome:
    # 不指定executable_path，由Selenium Manager 自動偵測本機Chrome版本，並下載、快取對應的chromedriver。
    service = Service()

    options = Options()
    download_dir = str(DATA_DIR)
    prefs = {
        "download.default_directory": download_dir,
        "savefile.default_directory": download_dir,  # 兩個都要設定
        "download.prompt_for_download": False,  # 禁止詢問下載提示
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,  # 安全瀏覽設定
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(options=options, service=service)


def e_download_region_csv(
    regions: list[str], year: int, start_month: int, end_month: int
) -> None:
    """下載 regions 在 year 年 start_month~end_month 月的每週榜單 CSV，已存在的檔案會跳過。"""
    DATA_DIR.mkdir(exist_ok=True)
    driver = _build_driver()
    driver.maximize_window()
    driver.get("https://charts.spotify.com/home")

    input("已在spotify chart網站入口，請手動登入後輸入enter: ")  # 手動登入
    wait = WebDriverWait(driver, 10)

    try:
        for region in regions:
            for month in range(start_month, end_month + 1):
                for url in get_uri(region, year=year, month=month):
                    file_name = "-".join(url.split("/")[-2:]) + ".csv"
                    if (DATA_DIR / file_name).exists():
                        logger.info("檔案 %s 已存在，跳過下載", file_name)
                        continue
                    driver.get(url)
                    download_btn_click(driver, wait)
                    logger.info("已觸發下載：%s", file_name)
    except Exception:
        logger.exception("下載失敗，停留在當前頁面 %s", driver.current_url)
    finally:
        logger.info("finished")
        driver.quit()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    regions = ["jp", "kr", "global"]
    year = 2026
    start_month = 1
    end_month = 9

    e_download_region_csv(regions, year, start_month, end_month)
