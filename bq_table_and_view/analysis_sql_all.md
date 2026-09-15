# Spotify Weekly Chart 分析 SQL（BigQuery）— 三區合併

Global / Japan / South Korea 三張表 UNION ALL 成一個 base view，下游每個 view 都帶 `region` 欄位，供 Data Studio 篩選器使用。分析邏輯與 `analysis_sql.md` 相同，差別只在所有彙總、排名、Top 5 都**依 `region` 分開計算**。

## 共通規則

- 所有物件皆為 view，建立於 `spotify_data`，命名 `v_all_xxx`。
- `region` 值沿用表中欄位：`global` / `jp` / `kr`。
- 資料範圍：`date_interval_end >= 2026-01-01`（2026-01-01 為週四，即 w1）。
- `rank` 在表中是 STRING，一律轉成 `rank_int`（INT64）後再比較／排序。
- 週次 `week_num` = (`date_interval_end` − 2026-01-01) 天數 ÷ 7 + 1。
- 合作曲：`artist_names` 以逗號拆開，每位歌手各算一次。
- 「佔榜最久」= 2026 年內出現過的週數（`weeks_in_2026`）；同分以 2026 年累計 streams 排序。
- view 之間有相依，請**依本文件順序**建立。

## View 相依關係

```
global_chart ─┐
japan_chart  ─┼── v_all_base
korea_chart  ─┘   ├── v_all_base_artist
                  │   ├── v_all_kpi
                  │   └── v_all_pie_top_artists_track_share
                  └── v_all_track_summary
                      ├── v_all_kpi
                      ├── v_all_top5_rank1_tracks
                      │   ├── v_all_trend_rank1_top5
                      │   └── v_all_pie_top_artists_track_share
                      └── v_all_top5_longest_tracks
                          ├── v_all_trend_longest_top5
                          └── v_all_pie_top_artists_track_share
```

---

## 0. Base views

### 0-1. `v_all_base`：三區 2026 年榜單，一列 = 一首歌在某區一週的排名

三張表的欄位名稱與順序完全相同（見 `spotify_weekly_chart_schema.md`），所以可以直接 `SELECT *` 後 UNION ALL。

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_base AS
WITH unioned AS (
  SELECT * FROM spotify_data.global_chart
  UNION ALL
  SELECT * FROM spotify_data.japan_chart
  UNION ALL
  SELECT * FROM spotify_data.korea_chart
)
SELECT
  ID,
  region,
  SAFE_CAST(rank AS INT64) AS rank_int,
  track_id,
  track_name,
  artist_names,
  source,
  peak_rank,
  previous_rank,
  weeks_on_chart,
  streams,
  date_interval_start,
  date_interval_end,
  DIV(DATE_DIFF(date_interval_end, DATE '2026-01-01', DAY), 7) + 1 AS week_num
FROM unioned
WHERE date_interval_end >= DATE '2026-01-01';
```

### 0-2. `v_all_base_artist`：歌手拆開，一列 = 一位歌手在某區一首歌一週的排名

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_base_artist AS
SELECT
  b.*,
  TRIM(artist) AS artist
FROM spotify_data.v_all_base AS b,
  UNNEST(SPLIT(b.artist_names, ',')) AS artist;
```

### 0-3. `v_all_track_summary`：一列 = 一首歌在某區的 2026 年彙總

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_track_summary AS
SELECT
  region,
  track_id,
  ANY_VALUE(track_name) AS track_name,
  ANY_VALUE(artist_names) AS artist_names,
  COUNT(DISTINCT date_interval_end) AS weeks_in_2026,
  SUM(streams) AS total_streams_2026,
  MIN(rank_int) AS best_rank_2026,
  MAX(weeks_on_chart) AS max_weeks_on_chart
FROM spotify_data.v_all_base
GROUP BY region, track_id;
```

---

## 1. 第一層：KPI 卡片

### `v_all_kpi`：一區一列

| 欄位 | 業務語意 |
|---|---|
| `total_tracks` | 2026 年至今總上榜歌曲數 |
| `total_artists` | 2026 年至今總上榜歌手數 |
| `rank1_tracks` | 2026 年至今曾進入 rank1 的歌曲數 |
| `rank1_artists` | 2026 年至今曾進入 rank1 的歌手數 |
| `longest_track_weeks` | 2026 年至今在榜最久的歌曲是幾週（各歌曲取 2026 年內最大 `weeks_on_chart`，再取最大者） |
| `longest_track_name` / `longest_track_artists` | 上一項對應的歌曲與歌手（可作卡片副標） |

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_kpi AS
WITH track_counts AS (
  SELECT
    region,
    COUNT(DISTINCT track_id) AS total_tracks,
    COUNT(DISTINCT IF(rank_int = 1, track_id, NULL)) AS rank1_tracks
  FROM spotify_data.v_all_base
  GROUP BY region
),
artist_counts AS (
  SELECT
    region,
    COUNT(DISTINCT artist) AS total_artists,
    COUNT(DISTINCT IF(rank_int = 1, artist, NULL)) AS rank1_artists
  FROM spotify_data.v_all_base_artist
  GROUP BY region
),
longest AS (
  SELECT region, max_weeks_on_chart, track_name, artist_names
  FROM spotify_data.v_all_track_summary
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY region
    ORDER BY max_weeks_on_chart DESC, total_streams_2026 DESC
  ) = 1
)
SELECT
  region,
  track_counts.total_tracks,
  artist_counts.total_artists,
  track_counts.rank1_tracks,
  artist_counts.rank1_artists,
  longest.max_weeks_on_chart AS longest_track_weeks,
  longest.track_name AS longest_track_name,
  longest.artist_names AS longest_track_artists
FROM track_counts
JOIN artist_counts USING (region)
JOIN longest USING (region);
```

---

## 2. 第二層：排名趨勢折線圖

### 2-1. `v_all_top5_rank1_tracks`：各區曾進入 rank1 的歌曲，依在榜週數取前 5

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_top5_rank1_tracks AS
SELECT
  region,
  track_id,
  track_name,
  artist_names,
  weeks_in_2026,
  total_streams_2026,
  ROW_NUMBER() OVER (
    PARTITION BY region
    ORDER BY weeks_in_2026 DESC, total_streams_2026 DESC
  ) AS top_order
FROM spotify_data.v_all_track_summary
WHERE best_rank_2026 = 1
QUALIFY top_order <= 5;
```

### 2-2. `v_all_top5_longest_tracks`：各區不曾進入 rank1 的歌曲中，2026 年佔榜最久的前 5 首

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_top5_longest_tracks AS
SELECT
  region,
  track_id,
  track_name,
  artist_names,
  weeks_in_2026,
  total_streams_2026,
  ROW_NUMBER() OVER (
    PARTITION BY region
    ORDER BY weeks_in_2026 DESC, total_streams_2026 DESC
  ) AS top_order
FROM spotify_data.v_all_track_summary
WHERE best_rank_2026 != 1
QUALIFY top_order <= 5;
```

### 2-3. `v_all_trend_rank1_top5`：2-1 各區 5 首的每週排名

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_trend_rank1_top5 AS
SELECT
  region,
  t.top_order,
  CONCAT(t.track_name, ' - ', t.artist_names) AS track_label,
  b.week_num,
  b.date_interval_end,
  b.rank_int,
  b.streams
FROM spotify_data.v_all_top5_rank1_tracks AS t
JOIN spotify_data.v_all_base AS b
  USING (region, track_id);
```

### 2-4. `v_all_trend_longest_top5`：2-2 各區 5 首的每週排名

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_trend_longest_top5 AS
SELECT
  region,
  t.top_order,
  CONCAT(t.track_name, ' - ', t.artist_names) AS track_label,
  b.week_num,
  b.date_interval_end,
  b.rank_int,
  b.streams
FROM spotify_data.v_all_top5_longest_tracks AS t
JOIN spotify_data.v_all_base AS b
  USING (region, track_id);
```

**Data Studio 設定提示**

- 維度 `week_num`、細分維度 `track_label`、指標 `rank_int`（匯總方式選 MIN）。
- Y 軸勾選「反轉」，rank 1 才會在最上方。
- 同一首歌可能同時出現在多區的 Top 5；沒選定單一區域時，`rank_int` 會被跨區匯總而失真，所以篩選器應限定單選（見文末）。

---

## 3. 第三層：代表歌手的上榜歌曲佔比圓餅圖

### `v_all_pie_top_artists_track_share`：各區 2-1 與 2-2 共 10 首歌的歌手，各自在該區 2026 年上榜歌曲數佔該區全年度上榜歌曲的比例

- 歌手名單、歌曲數、分母、`Others` 的定義同 `analysis_sql.md`，但全部以區域為單位計算。
- 同一位歌手會在不同區各有一列，數字各自獨立。

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_pie_top_artists_track_share AS
WITH top_artists AS (
  SELECT DISTINCT region, TRIM(artist) AS artist
  FROM (
    SELECT region, artist_names FROM spotify_data.v_all_top5_rank1_tracks
    UNION ALL
    SELECT region, artist_names FROM spotify_data.v_all_top5_longest_tracks
  ),
    UNNEST(SPLIT(artist_names, ',')) AS artist
),
top_artist_rows AS (
  SELECT region, artist, b.track_id
  FROM spotify_data.v_all_base_artist AS b
  JOIN top_artists USING (region, artist)
),
total AS (
  SELECT region, COUNT(DISTINCT track_id) AS total_tracks
  FROM spotify_data.v_all_base
  GROUP BY region
),
slices AS (
  SELECT region, artist, COUNT(DISTINCT track_id) AS track_count
  FROM top_artist_rows
  GROUP BY region, artist
  UNION ALL
  SELECT region, 'Others', ANY_VALUE(total.total_tracks) - COUNT(DISTINCT top_artist_rows.track_id)
  FROM top_artist_rows
  JOIN total USING (region)
  GROUP BY region
)
SELECT
  region,
  slices.artist,
  slices.track_count,
  SAFE_DIVIDE(slices.track_count, total.total_tracks) AS share_of_total_tracks,
  total.total_tracks
FROM slices
JOIN total USING (region);
```

**重疊檢查（每區應 `slice_sum = total_tracks`）**

```sql
SELECT
  region,
  SUM(track_count) AS slice_sum,
  ANY_VALUE(total_tracks) AS total_tracks
FROM spotify_data.v_all_pie_top_artists_track_share
GROUP BY region;
```

---

## 4. Data Studio 篩選器設定

- 每個 view 都有同名欄位 `region`，篩選器控制項選 `region` 當控制欄位。
- **建議設為單選並給預設值**（例如 `global`）。原因：KPI 卡片、折線圖、圓餅圖都是「單一區域內」才有意義的數字；如果同時選多區，Data Studio 會把三區加總（例如 `total_tracks` 相加、同一首歌的 `rank_int` 被合併），結果會失真。
- 篩選器要作用到頁面上來自不同 view 的圖表，需要各資料來源的 `region` 欄位 ID 相同。BigQuery 資料來源預設以欄位名當 ID，一般會直接生效；若某張圖沒被篩到，檢查該資料來源的 `region` 欄位是否被改過名稱。
