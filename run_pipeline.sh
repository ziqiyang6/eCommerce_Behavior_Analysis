#!/usr/bin/env bash
# Run the full pipeline end to end.
#   ./run_pipeline.sh          # sample data (data/sample), outputs -> data/sample_run/
#   ./run_pipeline.sh --full   # download full Kaggle dataset, outputs -> data/processed + data/outputs
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--full" ]]; then
  INPUT="data/raw/*.csv"
  PROCESSED="data/processed"
  OUTPUTS="data/outputs"
  echo "==> [00] Downloading dataset from Kaggle"
  python scripts/00_prepare_data.py
else
  INPUT="data/sample/*.csv"
  PROCESSED="data/sample_run/processed"
  OUTPUTS="data/sample_run/outputs"
  echo "==> Running on sample data (use --full for the complete dataset)"
fi

echo "==> [01] ETL: CSV -> Silver parquet"
python scripts/01_etl_events_to_parquet.py \
  --input-glob "$INPUT" --output-dir "$PROCESSED/events_parquet"

echo "==> [02] Build Gold tables"
python scripts/02_build_gold_tables.py \
  --events-dir "$PROCESSED/events_parquet" \
  --out-user-daily "$PROCESSED/user_daily" \
  --out-session-funnel "$PROCESSED/session_funnel"

echo "==> [03] Funnel & retention analysis"
python scripts/03_analysis_funnel_retention.py \
  --user-daily "$PROCESSED/user_daily" \
  --session-funnel "$PROCESSED/session_funnel" \
  --out-dir "$OUTPUTS" --plots

echo "==> [04] Anomalous users"
python scripts/04_anomaly_users.py \
  --user-daily "$PROCESSED/user_daily" \
  --session-funnel "$PROCESSED/session_funnel" \
  --out-dir "$OUTPUTS"

echo "==> Done. Results in $OUTPUTS/tables"
