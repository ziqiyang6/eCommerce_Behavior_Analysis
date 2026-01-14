# eCommerce_Behavior_Analysis

Create the virtual Environment 
```
# This is command you may need for conda environment
source /opt/anaconda3/etc/profile.d/conda.sh
conda create -n ecom-ws python=3.11 -y
conda activate ecom-ws
python -V
```

# Project Structure
```
ecom-work-sample/
  README.md
  environment.yml (or requirements.txt)
  .gitignore

  data/
    raw/                 # 原始CSV（不进git）
    processed/
      events_parquet/    # Silver
      user_daily/        # Gold
      session_funnel/    # Gold
      features/          # Optional
    outputs/
      figures/
      tables/

  scripts/               # 可执行入口（pipeline steps）
    00_prepare_data.py
    01_etl_events_to_parquet.py
    02_build_gold_tables.py
    03_anomaly_detection.py   # or 03_funnel_retention.py
    04_report_assets.py       # optional: export csv/figures

  src/                   # 可复用模块（被 scripts 调用）
    __init__.py
    config.py
    spark_utils.py
    schemas.py
    etl.py
    gold.py
    anomalies.py

  notebooks/             # 只放探索/演示，不作为主执行
    00_explore_sample.ipynb
    01_results_story.ipynb
```
