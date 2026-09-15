"""L 階段：將 T 階段清洗後的 DataFrame 以 MERGE（依 ID）upsert 到 BigQuery。

認證走 ADC：執行前設定環境變數 GOOGLE_APPLICATION_CREDENTIALS 指向 service account JSON key。
"""

import logging
import uuid

import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

from t_clean_region_csv import t_clean_region_csv

logger = logging.getLogger(__name__)

load_dotenv()
DATASET = "spotify_data"
REGION_TABLES = {"global": "global_chart", "kr": "korea_chart", "jp": "japan_chart"}
PK = "ID"

SCHEMA = [
    bigquery.SchemaField("ID", "STRING"),
    bigquery.SchemaField("region", "STRING"),
    bigquery.SchemaField("rank", "STRING"),
    bigquery.SchemaField("track_id", "STRING"),
    bigquery.SchemaField("artist_names", "STRING"),
    bigquery.SchemaField("track_name", "STRING"),
    bigquery.SchemaField("source", "STRING"),
    bigquery.SchemaField("peak_rank", "INTEGER"),
    bigquery.SchemaField("previous_rank", "INTEGER"),
    bigquery.SchemaField("weeks_on_chart", "INTEGER"),
    bigquery.SchemaField("streams", "INTEGER"),
    bigquery.SchemaField("date_interval_start", "DATE"),
    bigquery.SchemaField("date_interval_end", "DATE"),
    bigquery.SchemaField("uploaded_at", "TIMESTAMP"),
]


def build_merge_sql(target: str, staging: str) -> str:
    columns = [f.name for f in SCHEMA]
    update_set = ", ".join(f"`{c}` = S.`{c}`" for c in columns if c != PK)
    insert_cols = ", ".join(f"`{c}`" for c in columns)
    insert_vals = ", ".join(f"S.`{c}`" for c in columns)
    return f"""
MERGE `{target}` T
USING `{staging}` S
ON T.`{PK}` = S.`{PK}`
WHEN MATCHED THEN
  UPDATE SET {update_set}
WHEN NOT MATCHED THEN
  INSERT ({insert_cols}) VALUES ({insert_vals})
"""


def l_load_to_bigquery(df: pd.DataFrame, region: str) -> int:
    """先把 df 載入暫存表，再 MERGE 進 {region} 對應的 table，最後刪除暫存表。回傳受影響筆數。"""
    client = bigquery.Client()
    table = REGION_TABLES[region]
    target_table = f"{client.project}.{DATASET}.{table}"
    staging_table = (
        f"{client.project}.{DATASET}._staging_{table}_{uuid.uuid4().hex[:8]}"
    )

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA, write_disposition="WRITE_TRUNCATE"
    )
    try:
        # staging table 先 truncate 再寫入
        client.load_table_from_dataframe(
            df, staging_table, job_config=job_config
        ).result()

        merge_job = client.query(build_merge_sql(target_table, staging_table))
        merge_job.result()
    except Exception:
        logger.exception("[%s] 載入或 MERGE 失敗：%s", region, target_table)
        raise
    finally:
        client.delete_table(staging_table, not_found_ok=True)

    logger.info(
        "[%s] MERGE 完成：%s，受影響 %d 筆",
        region,
        target_table,
        merge_job.num_dml_affected_rows,
    )
    return merge_job.num_dml_affected_rows


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # region = "jp"  # jp / kr / global
    after_date = "2026-01-01"  # 處理檔名日期晚於此日的榜單，格式 YYYY-MM-DD
    for region in REGION_TABLES:
        l_load_to_bigquery(t_clean_region_csv(region, after_date), region)
