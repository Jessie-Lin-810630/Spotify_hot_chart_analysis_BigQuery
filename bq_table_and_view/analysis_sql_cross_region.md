# Spotify Weekly Chart 跨地區交叉分析 SQL（BigQuery）

將 N05（`src/in_colab(deprecated)/N05_Data_Visualization.ipynb`）的跨地區分析改寫為 view，供 Data Studio 使用：

| N05 原分析 | 對應 view |
|---|---|
| cell 29：三區歌曲集合的文氏圖（`venn3`） | `v_all_cross_venn` |
| cell 31–34：兩區／三區重疊歌曲清單，含 streams 與各區名次 | `v_all_cross_overlap_tracks` |
| cell 38–42：三區都上榜的歌曲，各區每週名次熱圖與在榜週數 | `v_all_cross_three_region_rank_history` |

## 共通規則

- 所有物件皆為 view，建立於同一 dataset `spotify_data`，命名原則為: `v_all_cross_xxx`。
- `v_all_cross_xxx` 的 `cross` 表示這張表在比較三個地區之間的重疊關係，而非單一地區內部的表現。
- 所有 view 皆從 [`analysis_sql_all.md`](/bq_table_and_view/analysis_sql_all.md) 的 `v_all_base` 往下接，不直接查基底資料表 (table)。
- 資料範圍由 `period` 欄位區分成兩種統計期間，Data Studio 以篩選器切換：
  - `ytd_2026`：2026-01-01 至今，與其他 dashboard 的範圍一致。
  - `latest_week`：最新一週，即 `v_all_base` 中最大的 `date_interval_end`，也是 N05 原本採用的範圍。
- 邏輯建模注意事項：
  - 歌曲以 `track_id` 識別，一首歌在某區「上榜」的定義 = 在該期間內至少出現一週，不論週數，也不論名次。
  - 地區的顯示名稱改用 `Global` / `Japan` / `South Korea`，與基底資料表的 `global` / `jp` / `kr` 不同。
  - 地區組合以 ` & ` 串接，順序固定為 Global → Japan → South Korea，例如 `Global & Japan`。
- view 表之間有相依，請**依本文件順序**建立。

## View 相依關係

```
v_all_base (view)
└── v_all_cross_scoped
    └── v_all_cross_track_membership
        ├── v_all_cross_venn
        ├── v_all_cross_overlap_tracks        （另 JOIN v_all_cross_scoped）
        └── v_all_cross_three_region_rank_history（另 JOIN v_all_base）
```

### View 業務語意初步說明

| view 表名稱 | 語意 | 維度與列的顆粒度 |
|------------|------|-----|
| v_all_cross_scoped | 把 `v_all_base` 依兩種統計期間 (`ytd_2026`、`latest_week`) 各展開一份的週榜單總集<br>屬於最新一週的資料列會同時出現在兩個期間中 | 維度：期間+歌+週+地區；<br>列的顆粒度：每1個期間每1個地區每1週每1首屬1列 |
| v_all_cross_track_membership | 一首歌在某個統計期間裡，究竟在哪幾個地區上過榜 (回答「這首歌是只紅一地，還是能跨地區通吃」) | 維度：期間+歌；<br>列的顆粒度：每1個期間每1首屬1列 |
| v_all_cross_venn | 某個統計期間裡，每一種地區集合 (3 個單區 + 4 個交集) 各自涵蓋多少首歌 (回答「三個市場的口味有多少重疊」) | 維度：期間+地區集合；<br>列的顆粒度：每1個期間每1種地區集合屬1列，故每1個期間貢獻 7 列 |
| v_all_cross_overlap_tracks | 某個統計期間裡，至少在 2 個地區都上過榜的歌曲清單，附上它在各區的最佳名次、streams 與在榜週數 (回答「跨地區通吃的歌，在各地紅的程度是否一致」) | 維度：期間+歌；<br>列的顆粒度：每1個期間每1首屬1列 |
| v_all_cross_three_region_rank_history | 三區都上過榜的歌曲，在 2026 年每一週於各地的名次走勢 (回答「同一首歌在三個市場的熱度是同步起落，還是一地先紅、他地後跟」) | 維度：期間+歌+地區+週；<br>列的顆粒度：每1個期間每1首每1個地區每1週屬1列 |

---

## 0. 共用 views

### 0-1. `v_all_cross_scoped`

- 語意：把 `v_all_base` 依兩種統計期間各展開一份的週榜單總集。屬於最新一週的資料列會同時出現在 `ytd_2026` 與 `latest_week` 兩個期間中。
- 維度：期間+歌+週+地區；列的顆粒度：每1個期間每1個地區每1週每1首屬1列。

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_cross_scoped AS
WITH latest AS (
  SELECT MAX(date_interval_end) AS latest_date
  FROM spotify_data.v_all_base
)
SELECT 'ytd_2026' AS period, b.*
FROM spotify_data.v_all_base AS b
UNION ALL
SELECT 'latest_week' AS period, b.*
FROM spotify_data.v_all_base AS b
JOIN latest
  ON b.date_interval_end = latest.latest_date;
```

### 0-2. `v_all_cross_track_membership`

- 語意：一首歌在某個統計期間裡，究竟在哪幾個地區上過榜（回答「這首歌是只紅一地，還是能跨地區通吃」）。
- 維度：期間+歌；列的顆粒度：每1個期間每1首屬1列。

| 欄位 | 說明 |
|---|---|
| `in_global` / `in_jp` / `in_kr` | 該期間是否在該區上榜 |
| `region_count` | 上榜區域數（1–3） |
| `region_combo` | 上榜區域組合，例如 `Global & Japan`，即文氏圖中該歌曲所屬的互斥區塊 |

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_cross_track_membership AS
WITH flags AS (
  SELECT
    period,
    track_id,
    ANY_VALUE(track_name) AS track_name,
    ANY_VALUE(artist_names) AS artist_names,
    LOGICAL_OR(region = 'global') AS in_global,
    LOGICAL_OR(region = 'jp') AS in_jp,
    LOGICAL_OR(region = 'kr') AS in_kr
  FROM spotify_data.v_all_cross_scoped
  GROUP BY period, track_id
)
SELECT
  flags.*,
  CONCAT(track_name, ' - ', artist_names) AS track_label,
  CAST(in_global AS INT64) + CAST(in_jp AS INT64) + CAST(in_kr AS INT64) AS region_count,
  ARRAY_TO_STRING(
    [IF(in_global, 'Global', NULL), IF(in_jp, 'Japan', NULL), IF(in_kr, 'South Korea', NULL)],
    ' & '
  ) AS region_combo
FROM flags;
```

---

## 1. 文氏圖

### `v_all_cross_venn`

- 語意：某個統計期間裡，每一種地區集合（3 個單區 + 4 個交集）各自涵蓋多少首歌（回答「三個市場的口味有多少重疊」）。
- 維度：期間+地區集合；列的顆粒度：每1個期間每1種地區集合屬1列，故每1個期間貢獻 7 列。

同時提供兩種計數，依社群套件要求的資料格式擇一使用：

| 欄位 | 說明 | 適用 |
|---|---|---|
| `exclusive_track_count` | 該期間內，**剛好只**在 `set_label` 所列地區上榜、未在其他地區上榜的歌曲數。即文氏圖上 7 片互不重疊的區塊各自的數字（= N05 `venn3` 圖上顯示的數字）。7 列可加總，加總 = 不重複歌曲總數 | 需要各區塊數字的套件，或作為圖上標籤核對 |
| `inclusive_track_count` | 該期間內，在 `set_label` 所列地區**都有**上榜的歌曲數，不論是否也在其他地區上榜（= 該集合的 exclusive + 同時在其他地區上榜的歌）。單區 = 整個圓；兩區 = 兩圓整塊交疊區；三區 = 與 exclusive 相同。同一首歌會被計入多個集合，7 列**不可加總** | venn.js 類套件（依整個圓的大小畫圓、依交疊區大小決定重疊面積） |

例：若 7 片 exclusive 為 Global 只有 100、Japan 只有 80、South Korea 只有 60、Global & Japan 只有 20、Global & South Korea 只有 15、Japan & South Korea 只有 5、三區皆有 10，則 inclusive 的 `Global` = 100 + 20 + 15 + 10 = 145，`Global & Japan` = 20 + 10 = 30，`Global & Japan & South Korea` = 10。

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_cross_venn AS
WITH subsets AS (
  SELECT *
  FROM UNNEST([
    STRUCT(1 AS set_order, 'Global' AS set_label, TRUE AS has_global, FALSE AS has_jp, FALSE AS has_kr),
    (2, 'Japan', FALSE, TRUE, FALSE),
    (3, 'South Korea', FALSE, FALSE, TRUE),
    (4, 'Global & Japan', TRUE, TRUE, FALSE),
    (5, 'Global & South Korea', TRUE, FALSE, TRUE),
    (6, 'Japan & South Korea', FALSE, TRUE, TRUE),
    (7, 'Global & Japan & South Korea', TRUE, TRUE, TRUE)
  ])
)
SELECT
  m.period,
  s.set_order,
  s.set_label,
  CAST(s.has_global AS INT64) + CAST(s.has_jp AS INT64) + CAST(s.has_kr AS INT64) AS region_count,
  COUNTIF(
    (m.in_global OR NOT s.has_global)
    AND (m.in_jp OR NOT s.has_jp)
    AND (m.in_kr OR NOT s.has_kr)
  ) AS inclusive_track_count,
  COUNTIF(
    m.in_global = s.has_global
    AND m.in_jp = s.has_jp
    AND m.in_kr = s.has_kr
  ) AS exclusive_track_count
FROM spotify_data.v_all_cross_track_membership AS m
CROSS JOIN subsets AS s
GROUP BY m.period, s.set_order, s.set_label, region_count;
```

**核對查詢**：每個 `period` 的 `exclusive_track_count` 加總應等於該期間三區不重複歌曲數。

```sql
SELECT
  v.period,
  v.exclusive_sum,
  m.distinct_tracks
FROM (
  SELECT period, SUM(exclusive_track_count) AS exclusive_sum
  FROM spotify_data.v_all_cross_venn
  GROUP BY period
) AS v
JOIN (
  SELECT period, COUNT(*) AS distinct_tracks
  FROM spotify_data.v_all_cross_track_membership
  GROUP BY period
) AS m
  USING (period);
```
> [畫圖教學](https://datastudio.google.com/reporting/bbded460-eeaf-46c3-ab9b-db862b1034fc/page/lLgbB)
---

## 2. 重疊歌曲清單（表格）

### `v_all_cross_overlap_tracks`

- 語意：某個統計期間裡，至少在 2 個地區都上過榜的歌曲清單，附上它在各區的最佳名次、streams 與在榜週數（回答「跨地區通吃的歌，在各地紅的程度是否一致」）。
- 維度：期間+歌；列的顆粒度：每1個期間每1首屬1列。
- N05 將兩區、三區分成四張表（cell 31–34），這裡合成一張，以 `region_combo` 篩選即可得到原本任一張表。

| 欄位 | `ytd_2026` | `latest_week` |
|---|---|---|
| `best_rank_global` / `_jp` / `_kr` | 該區 2026 年最佳名次 | 該區當週名次 |
| `streams_global` / `_jp` / `_kr` | 該區 2026 年累計 streams | 該區當週 streams |
| `weeks_global` / `_jp` / `_kr` | 該區 2026 年在榜週數 | 0 或 1 |

未在該區上榜時為 NULL（週數為 0）。

> 與 N05 的差異：N05 把各區 streams 相加成 `sum_of_streams`。但 Global 榜的 streams 本身已包含日本、韓國聽眾，相加會重複計算，所以這裡改為各區分開列出。

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_cross_overlap_tracks AS
SELECT
  m.period,
  m.region_combo,
  m.region_count,
  m.track_id,
  m.track_name,
  m.artist_names,
  MIN(IF(s.region = 'global', s.rank_int, NULL)) AS best_rank_global,
  MIN(IF(s.region = 'jp', s.rank_int, NULL)) AS best_rank_jp,
  MIN(IF(s.region = 'kr', s.rank_int, NULL)) AS best_rank_kr,
  SUM(IF(s.region = 'global', s.streams, NULL)) AS streams_global,
  SUM(IF(s.region = 'jp', s.streams, NULL)) AS streams_jp,
  SUM(IF(s.region = 'kr', s.streams, NULL)) AS streams_kr,
  COUNT(DISTINCT IF(s.region = 'global', s.date_interval_end, NULL)) AS weeks_global,
  COUNT(DISTINCT IF(s.region = 'jp', s.date_interval_end, NULL)) AS weeks_jp,
  COUNT(DISTINCT IF(s.region = 'kr', s.date_interval_end, NULL)) AS weeks_kr
FROM spotify_data.v_all_cross_track_membership AS m
JOIN spotify_data.v_all_cross_scoped AS s
  ON s.period = m.period
  AND s.track_id = m.track_id
WHERE m.region_count >= 2
GROUP BY m.period, m.region_combo, m.region_count, m.track_id, m.track_name, m.artist_names;
```

---

## 3. 三區皆上榜歌曲的名次熱圖

### `v_all_cross_three_region_rank_history`

- 語意：三區都上過榜的歌曲，在 2026 年每一週於各地的名次走勢（回答「同一首歌在三個市場的熱度是同步起落，還是一地先紅、他地後跟」）。
- 維度：期間+歌+地區+週；列的顆粒度：每1個期間每1首每1個地區每1週屬1列。
- 歌曲名單依 `period` 決定（`latest_week` = N05 原本就在算的：最新一週三區皆上榜的歌）。
- 名次軌跡一律取 2026 年全部週次，不受 `period` 限制。

```sql
CREATE OR REPLACE VIEW spotify_data.v_all_cross_three_region_rank_history AS
SELECT
  m.period,
  m.track_label,
  b.region,
  b.week_num,
  b.date_interval_end,
  b.rank_int
FROM spotify_data.v_all_cross_track_membership AS m
JOIN spotify_data.v_all_base AS b
  ON b.track_id = m.track_id
WHERE m.region_count = 3;
```

**Data Studio 設定提示**

- 用「資料透視表（含熱圖）」：列維度 `track_label`、欄維度 `week_num`、指標 `rank_int`（匯總 MIN），依 `region` 篩選或做成三張表。
- 熱圖顏色要設定為「數值越小顏色越深」，rank 1 才會最醒目。
- N05 的「各區在榜週數」（cell 38）：同一資料來源，維度 `track_label` + `region`，指標 `date_interval_end` 的 COUNT DISTINCT。

---

## 4. Data Studio 頁面注意事項

- **建議把跨地區分析放在獨立頁面。** 原本頁面的 `region` 單選篩選器，會作用到帶有 `region` 欄位的 `v_all_cross_three_region_rank_history`；而文氏圖與重疊清單本來就是跨區，不該被單一區域篩選。
- **此頁新增 `period` 篩選器，設為單選**（預設 `ytd_2026` 或 `latest_week`）。不選時兩個期間的數字會被加總，文氏圖會失真。
