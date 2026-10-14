# eCommerce_Behavior_Analysis

A local PySpark pipeline that turns ~110M raw clickstream events from a multi-category online store
(Oct–Nov 2019, [Kaggle dataset](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store))
into analysis-ready tables, then measures the **conversion funnel**, **cohort retention**, and flags **anomalous users**.

```
raw CSV (14 GB) ──01──▶ Silver: events_parquet ──02──▶ Gold: user_daily
                                                     └─▶ Gold: session_funnel ──03──▶ funnel / retention tables
                                                                              └─04──▶ anomalous users
```

## Quick start

Requirements: Python 3.11+, **Java 17** (needed by Spark).

```bash
# This is command you may need for conda environment
source /opt/anaconda3/etc/profile.d/conda.sh

# Run this if you need to create the environment
conda create -n ecom-ws python=3.11 openjdk=17 -y
conda activate ecom-ws
pip install -r requirements.txt
```

Run the whole pipeline on the bundled sample (~1 minute, no Kaggle account needed):

```bash
./run_pipeline.sh
```

Results land in `data/sample_run/outputs/tables/`.

To reproduce on the full dataset (downloads ~14 GB via `kagglehub`, needs ~20 GB free disk and 8 GB RAM for Spark):

```bash
./run_pipeline.sh --full
```

Results land in `data/outputs/tables/` (the committed results there come from this full run).

### Sample data

`data/sample/events_sample.csv` (54k events, 7 MB) contains **every event of 2,665 users** (`user_id % 2000 == 0`)
across both months. Sampling by user instead of by row keeps sessions and user histories intact, so funnel and
retention numbers on the sample stay close to the full data.

## Pipeline steps

Steps 01–04 can also be run on their own from the project root; use `--help` to see their options (paths, Spark memory, thresholds).

| Step | Script | What it does |
|---|---|---|
| 00 | `scripts/00_prepare_data.py` | Downloads the dataset with `kagglehub` and syncs the CSVs into `data/raw/` (idempotent). |
| 01 | `scripts/01_etl_events_to_parquet.py` | **Silver.** Reads the CSVs with an explicit schema, normalizes strings, parses UTC timestamps, drops invalid rows (bad time, unknown event type, negative/absurd price), writes parquet partitioned by `event_date`. |
| 02 | `scripts/02_build_gold_tables.py` | **Gold.** `user_daily` (one row per user per day: views, carts, purchases, sessions, revenue, unique products) and `session_funnel` (one row per session: start/end, duration, event counts, `has_view/has_cart/has_purchase` flags). |
| 03 | `scripts/03_analysis_funnel_retention.py` | Session-level funnel (overall + daily) and first-seen cohort retention at D1/D7/D30 (per cohort + cohort-weighted overall). `--plots` also saves bar charts. |
| 04 | `scripts/04_anomaly_users.py` | Flags users with four rules: daily event spike, daily view spike, session event spike (p99.9 thresholds via `percentile_approx`), and funnel mismatch (≥50 carts with no purchase in a session). Output includes the evidence for each flag. |

## Results (full dataset)

**Funnel** — 23.0M sessions

| Metric | Value |
|---|---|
| Sessions with a view | 99.95% |
| View → cart | 10.1% |
| Cart → purchase | 60.6% |
| View → purchase | 6.1% |

**Retention** — 5.3M users, cohort = first day seen

| D1 | D7 | D30 |
|---|---|---|
| 10.6% | 5.0% | 3.0% |

Early-October cohorts retain noticeably better (e.g. 2019-10-01: D1 17.3%, D30 7.7%).

**Anomalies** — p99.9 daily-activity threshold is 137 events/day; the most extreme user logged 4,128 events in a
single day. 783 user flags in total (top 200 per rule), see `data/outputs/tables/anomalous_users.csv/`.

> Spark writes each CSV as a folder; the data is in the `part-*.csv` file inside it.

## Project structure

```
ecommerce_project/
  README.md
  requirements.txt
  run_pipeline.sh                 # one-command entry point (sample or --full)
  conf/log4j2.properties          # quiets Spark logging
  eCommerce behavior data.ipynb   # exploration notebook

  scripts/                        # pipeline steps
    00_prepare_data.py
    01_etl_events_to_parquet.py
    02_build_gold_tables.py
    03_analysis_funnel_retention.py
    04_anomaly_users.py

  data/
    sample/                       # small sample, in git
    raw/                          # full CSVs (not in git)
    processed/                    # Silver + Gold parquet (not in git)
      events_parquet/
      user_daily/
      session_funnel/
    outputs/tables/               # results from the full run, in git
    sample_run/                   # outputs of sample runs (not in git)
```
