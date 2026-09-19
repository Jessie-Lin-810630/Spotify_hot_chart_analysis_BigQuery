# Spotify Weekly Chart 分析 SQL（BigQuery）— 三區合併

Global / Japan / South Korea 三張表 UNION ALL 成一個 base view，下游每個 view 都帶 `region` 欄位，供 Data Studio 篩選器地區使用。

## 共通規則

- 所有物件皆為 view，建立於同一 dataset `spotify_data`，命名原則為: `v_all_xxx`。
- `v_all_xxx` 的 `all` 表示這張表查詢了三個地區各一張基底資料表，並未拆成一區一張 view 表。
- 資料範圍：`date_interval_end >= 2026-01-01`，時間顆粒度以週為單位，週次計算公式為：
  ```plain text
  `week_num` = (`date_interval_end` − 2026-01-01) 天數 ÷ 7 + 1。
  ```
- 邏輯建模注意事項，從基底資料表 table 抽出 view 表應記得以下：
  - view 表中的 `region` 欄位值沿用基底資料表 (table) 中的 `region` 欄位值：`global` / `jp` / `kr`。
  - 欄位 `rank` 在基底資料表中是 STRING，做成 view 表需一律轉成 `rank_int`（INT64）後再排序或篩選。
  - 基底資料表中的合作曲 (feat.)，其 `artist_names` 欄位值需以逗號拆開，每位歌手各算一次新的資料列。
  - 「佔榜最久的定義」= 2026 年內出現過的週數（`weeks_in_2026`），無關於是否連續上榜，只要該週上榜即算入 1 週。
  - 當佔榜時間一樣久，則改以「 2026 年累計點閱率 (`streams`) 」排序。

- view 表之間有相依，請**依本文件順序**建立。

## View 相依關係

```
global_chart (table) ─┐
japan_chart (table)  ─┼── v_all_base
korea_chart (table)  ─┘   ├── v_all_base_artist
                          │   ├── v_all_kpi
                          │   └── v_all_pie_top_artists_track_share
                          │
                          └── v_all_track_summary
                              ├── v_all_kpi
                              ├── v_all_top5_rank1_tracks
                              │   ├── v_all_trend_rank1_top5
                              │   └── v_all_pie_top_artists_track_share
                              └── v_all_top5_longest_tracks
                                  ├── v_all_trend_longest_top5
                                  └── v_all_pie_top_artists_track_share
```

### View 業務語意初步說明

| view 表名稱 |                      語意                    | 維度與列的顆粒度 |
|------------|---------------------------------------------|-----|
| v_all_base | global、japan、korea 三區在 2026 年的週榜單總集，一首歌一週代表一列 | 維度：歌+週+地區；<br>列的顆粒度：每1個地區每1週每1首屬1列 |
| v_all_base_artist | 將 2026 年三區的週榜單總集拆成一個歌手參與的一首歌代表一個資料列<br>因此一首合作曲在某一週上榜的資料列會被拆成多個資料列，所屬於不同歌手 | 維度：地區+歌+週+歌手；<br>列的顆粒度：每1個地區每1週每1首每1位歌手屬1列 |
| v_all_track_summary | 一首歌在某一區 2026 年的整體戰績總結，包含它總共待過幾週榜、累積多少 streams、最好名次爬到第幾名，以及 Spotify 官方報告的連續在榜週數 | 維度：歌+地區；<br>列的顆粒度：每1個地區每1首屬1列 |
| v_all_kpi | 某一區 2026 年榜單的整體規模與話題熱度，包含上榜歌曲數、上榜歌手數、曾拿下冠軍的歌曲數與歌手數，以及在榜最久的那首歌待了幾週 (回答「這個市場今年有多少歌在流動、多少歌真正紅到頂」) | 維度：地區；<br>列的顆粒度：每1個地區屬1列 |
| v_all_top5_rank1_tracks | 某一區 2026 年曾登上冠軍、且在榜時間最久的前 5 首歌，當同樣週數時改以累計 streams 決勝 (回答「既紅得起來、又紅得夠久」的歌曲) | 維度：歌+地區；<br>列的顆粒度：每1個地區每1首屬1列，故每1個地區貢獻 5 列 |
| v_all_top5_longest_tracks | 某一區 2026 年從未拿過冠軍、但在榜時間最久的前 5 首歌 (回答「雖然沒爆紅到榜首，卻有穩定曝光度的長銷型歌曲」) | 維度：歌+地區；<br>列的顆粒度：列的顆粒度：每1個地區每1首屬1列，故每1個地區貢獻 5 列 |
| v_all_trend_rank1_top5 | 承 `v_all_top5_rank1_tracks`，5 首冠軍長青歌在 2026 年每一週各地的名次走勢 (回答「它們是在各地是快速衝頂後滑落，還是長期盤據前段班」) | 維度：歌+地區+週；<br>列的顆粒度：每1首每1個地區每1週屬 1 列 |
| v_all_trend_longest_top5 | 承 `v_all_top5_longest_tracks`，5 首非冠軍長青歌，在 2026 年每一週各地的名次走勢 (回答「沒進過冠軍的歌，名次是穩定持平還是緩慢下滑」) | 維度：歌+地區+週；<br>列的顆粒度：每1首每1個地區每1週屬 1 列 |
| v_all_pie_top_artists_track_share | 5 首冠軍長青歌、加上 5 首非冠軍長青歌，總計 10 首歌的所屬歌手，他們的作品能夠進入 2026 年某一區域的週榜單之頻率 (回答「這 10 首的歌手的多數歌曲都很熱門還是靠一首闖天下」) | 維度：歌手+地區；<br>列的顆粒度：每1位歌手每1個地區屬 1 列 |

---

## 0. Base views

### 0-1. `v_all_base`

- 語意：global、japan、korea 三區在 2026 年的週榜單總集，一首歌一週代表一列。
- 維度：歌+週+地區；列的顆粒度：每1個地區每1週每1首屬1列。
- 三張表的欄位名稱與順序完全相同（見 `spotify_weekly_chart_schema.md`），所以可以直接 `SELECT *` 後 UNION ALL。

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

### 0-2. `v_all_base_artist`

- 語意：將 2026 年三區的週榜單總集拆成一個歌手參與的一首歌代表一個資料列。因此一首合作曲在某一週上榜的資料列會被拆成多個資料列，所屬於不同歌手。
- 維度：地區+歌+週+歌手；列的顆粒度：每1個地區每1週每1首每1位歌手屬1列。

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_base_artist AS
SELECT
  b.*,
  TRIM(artist) AS artist
FROM spotify_data.v_all_base AS b,
  UNNEST(SPLIT(b.artist_names, ',')) AS artist;
```

### 0-3. `v_all_track_summary`

- 語意：一首歌在某一區 2026 年的整體戰績總結，包含它總共待過幾週榜、累積多少 streams、最好名次爬到第幾名，以及 Spotify 官方報告的連續在榜週數。
- 維度：歌+地區；列的顆粒度：每1個地區每1首屬1列。

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

### `v_all_kpi`

- 語意：某一區 2026 年榜單的整體規模與話題熱度，包含上榜歌曲數、上榜歌手數、曾拿下冠軍的歌曲數與歌手數，以及在榜最久的那首歌待了幾週（回答「這個市場今年有多少歌在流動、多少歌真正紅到頂」）。
- 維度：地區；列的顆粒度：每1個地區屬1列。

| 欄位 | 業務語意 |
|---|---|
| `total_tracks` | 2026 年至今總上榜歌曲數 |
| `total_artists` | 2026 年至今總上榜歌手數 |
| `rank1_tracks` | 2026 年至今曾進入 rank1 的歌曲數 |
| `rank1_artists` | 2026 年至今曾進入 rank1 的歌手數 |
| `longest_track_weeks` | 歷年至今在榜最久的歌曲是幾週（各歌曲取自己的 `weeks_on_chart` 最大值，再從所有歌曲中取最大者） |
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

### 2-1. `v_all_top5_rank1_tracks`

- 語意：某一區 2026 年曾登上冠軍、且在榜時間最久的前 5 首歌，當同樣週數時改以累計 streams 決勝（回答「既紅得起來、又紅得夠久」的歌曲）。
- 維度：歌+地區；列的顆粒度：每1個地區每1首屬1列，故每1個地區貢獻 5 列。

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

### 2-2. `v_all_top5_longest_tracks`

- 語意：某一區 2026 年從未拿過冠軍、但在榜時間最久的前 5 首歌（回答「雖然沒爆紅到榜首，卻有穩定曝光度的長銷型歌曲」）。
- 維度：歌+地區；列的顆粒度：每1個地區每1首屬1列，故每1個地區貢獻 5 列。

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

### 2-3. `v_all_trend_rank1_top5`

- 語意：承 `v_all_top5_rank1_tracks`，5 首冠軍長青歌在 2026 年每一週各地的名次走勢（回答「它們在各地是快速衝頂後滑落，還是長期盤據前段班」）。
- 維度：歌+地區+週；列的顆粒度：每1首每1個地區每1週屬 1 列。

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

### 2-4. `v_all_trend_longest_top5`

- 語意：承 `v_all_top5_longest_tracks`，5 首非冠軍長青歌在 2026 年每一週各地的名次走勢（回答「沒進過冠軍的歌，名次是穩定持平還是緩慢下滑」）。
- 維度：歌+地區+週；列的顆粒度：每1首每1個地區每1週屬 1 列。

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
- 同一首歌可能同時出現在多區的 Top 5，最可能是 BTS 的歌，因為不少團員有各自出單曲。若沒選定單一區域時，`rank_int` 會被跨區匯總而失真，所以篩選器應限定單選（見文末）。

---

## 3. 第三層：代表歌手的上榜歌曲佔比圓餅圖

### `v_all_pie_top_artists_track_share`

- 語意：[5 首冠軍長青歌](#2-1-v_all_top5_rank1_tracks)、加上 [5 首非冠軍長青歌](#2-2-v_all_top5_longest_tracks)，總計 10 首歌的所屬歌手，他們手上有幾首能夠進入 2026 年某一區域的週榜單，佔據整年度多少百分比（回答「這 10 首的歌手的多數歌曲都很熱門還是靠一首闖天下」）。
- 維度：歌手+地區；列的顆粒度：每1位歌手每1個地區屬 1 列。
- 每片的百分比計算邏輯為：
  ```
  1. 分子：從 view 表 2-1 與 2-2 的 10 首歌曲找出所屬歌手，然後一個歌手在 2026 年的某地區上榜過歌曲數 (A)。
  2. 分母：該地區 2026 年全年度上榜歌曲數量 (B)
  3. 每片的比例 = 分子 / 分母；然後在 Data Studio 圓餅圖設置時轉為百分比%
  4. 每一片的類別名稱是歌手名稱，因此可推導 10 首歌的歌手在一個地區的上榜歌曲總數 (10 種 A) 並不會剛好湊滿 B，但為了要能合理表達 100%，
     差額就歸類在 "Others" 獨立一片，"Others" 表示"其他歌手"，其數值表示"其他歌手上榜歌曲數量"。
  ```
> 再次提醒，以上都針對一個地區內獨立計算，不會去算多區域下的百分比。

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

- 每個 view 都有同名欄位 `region`，因此可在 Data Studio 加入篩選器控制項選 `region` 當控制欄位。
- **建議 `region` 篩選器設為單選並給預設值**（例如預設 `global`）。原因是KPI 卡片、折線圖、圓餅圖都控制在「單一區域內」分析才有機會看出各地文化差異。如果同時選多區，Data Studio 會把三區加總（例如 `total_tracks` 相加、同一首歌的 `rank_int` 被合併），結果會失真。
- 製作儀表板時，篩選器要記得都有作用到 Data Studio dashboard 連結中所有 view 表，因此所有 view 表的 `region` 欄位名稱不要隨便改，否則篩選器會無法正常作用在某幾張圖表。
