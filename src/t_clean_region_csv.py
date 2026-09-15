"""T 階段：清洗 data/ 下 e_download_region_csv.py 下載的每週榜單 CSV，欄位對齊 spotify_weekly_chart_schema.md。"""

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BATCH_SIZE = 10

# 輸出欄位順序，與 BigQuery table schema 一致
SCHEMA_COLUMNS = [
    "ID",
    "region",
    "rank",
    "track_id",
    "artist_names",
    "track_name",
    "source",
    "peak_rank",
    "previous_rank",
    "weeks_on_chart",
    "streams",
    "date_interval_start",
    "date_interval_end",
    "uploaded_at",
]
INT_COLUMNS = ["peak_rank", "previous_rank", "weeks_on_chart", "streams"]


def _end_date_of(path: Path) -> date:
    """檔名結尾的日期即該週榜單的結束日（週四），例如 regional-jp-weekly-2026-01-22.csv。"""
    return date.fromisoformat(path.stem[-10:])


def _clean_one_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)  # CSV 帶 BOM
    end = _end_date_of(path)

    df["track_id"] = df["uri"].str.removeprefix("spotify:track:")
    df["date_interval_start"] = end - timedelta(days=6)  # 榜單統計區間為週五~週四
    df["date_interval_end"] = end
    df["region"] = path.stem.split("-")[1]  # global / kr / jp
    df["ID"] = end.isoformat() + "-" + df["rank"].str.zfill(3)  # 例：2026-01-22-001
    return df


def t_clean_region_csv(region: str, after_date: str) -> pd.DataFrame:
    """清洗指定地區、檔名日期晚於 after_date（YYYY-MM-DD，不含當天）的所有週榜 CSV。

    region 對應檔名中的地區代碼：jp / kr / global。
    """
    after = date.fromisoformat(after_date)
    files_list = sorted(
        p
        for p in DATA_DIR.glob(f"regional-{region}-weekly-*.csv")
        if _end_date_of(p) > after
    )
    if not files_list:
        msg = f"{DATA_DIR} 中找不到 {region} 在 {after_date} 之後的榜單 CSV，請檢查 Extract Task"
        logger.error(msg)
        raise FileNotFoundError(msg)

    batches = []
    for i in range(0, len(files_list), BATCH_SIZE):
        batch = files_list[i : i + BATCH_SIZE]
        batches.append(
            pd.concat(
                [_clean_one_csv(file_path) for file_path in batch], ignore_index=True
            )
        )
        logger.info(
            "[%s] 已清洗第 %d 批，共 %d 個檔案", region, i // BATCH_SIZE + 1, len(batch)
        )

    df = pd.concat(batches, ignore_index=True)
    df[INT_COLUMNS] = df[INT_COLUMNS].astype("int64")
    df["uploaded_at"] = pd.Timestamp.now(tz="UTC")  # 本次程式執行（上傳）時間
    return df.reindex(columns=list(SCHEMA_COLUMNS))


# if __name__ == "__main__":
# # 測試區，主程式入口在 l_load_to_bigquery
# logging.basicConfig(
#     level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
# )

# region = "jp"  # jp / kr / global
# after_date = "2026-08-01"  # 處理檔名日期晚於此日的榜單，格式 YYYY-MM-DD

# result = t_clean_region_csv(region, after_date)
# logger.info("前 5 筆：\n%s", result.head())
# logger.info("共 %d 筆", len(result))
