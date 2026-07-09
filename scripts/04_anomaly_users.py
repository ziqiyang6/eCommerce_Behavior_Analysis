#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--user-daily", default="data/processed/user_daily", help="Gold table: user_daily")
    p.add_argument("--session-funnel", default="data/processed/session_funnel", help="Gold table: session_funnel")
    p.add_argument("--out-dir", default="data/outputs", help="Output root dir (tables/figures)")
    p.add_argument("--driver-memory", default="8g")
    p.add_argument("--shuffle-partitions", type=int, default=200)

    # thresholds as percentiles (more robust than hard numbers)
    p.add_argument("--p-daily", type=float, default=0.999, help="Percentile for daily spikes (e.g., 0.999)")
    p.add_argument("--p-session", type=float, default=0.999, help="Percentile for session spikes (e.g., 0.999)")

    # mismatch rule (absolute thresholds)
    p.add_argument("--min-carts-mismatch", type=int, default=50, help="Min carts_cnt to flag mismatch")
    p.add_argument("--max-purchases-mismatch", type=int, default=0, help="Max purchases_cnt to flag mismatch (0 means none)")

    # output size control
    p.add_argument("--top-n-per-rule", type=int, default=200, help="Top N users per rule to output")
    return p.parse_args()


def build_spark(app_name: str, driver_memory: str, shuffle_partitions: int) -> SparkSession:
    spark = (
        SparkSession.builder
        .appName(app_name)
        .master("local[*]")
        .config("spark.driver.memory", driver_memory)
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        .getOrCreate()
    )
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.sparkContext.setLogLevel("WARN")
    return spark


def ensure_tables_dir(out_dir: str) -> Path:
    out_root = Path(out_dir)
    tables_dir = out_root / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    return tables_dir


def approx_threshold(df, colname: str, p: float) -> float:
    # returns approximate percentile threshold
    return float(df.select(F.expr(f"percentile_approx({colname}, {p}) as thr")).collect()[0]["thr"])


def main():
    args = parse_args()
    spark = build_spark("ecom-anomaly", args.driver_memory, args.shuffle_partitions)
    tables_dir = ensure_tables_dir(args.out_dir)

    user_daily = spark.read.parquet(args.user_daily)
    session_funnel = spark.read.parquet(args.session_funnel)

    # -----------------------------
    # Prepare daily metrics
    # -----------------------------
    user_daily = user_daily.withColumn(
        "events_day",
        (F.col("views") + F.col("carts") + F.col("removes") + F.col("purchases")).cast("long")
    )

    thr_daily_events = approx_threshold(user_daily, "events_day", args.p_daily)
    thr_daily_views = approx_threshold(user_daily, "views", args.p_daily)
    print(f"[threshold] daily events_day p={args.p_daily}: {thr_daily_events}")
    print(f"[threshold] daily views p={args.p_daily}: {thr_daily_views}")

    daily_high_events = (
        user_daily.filter(F.col("events_day") >= F.lit(thr_daily_events))
        .select(
            F.col("user_id"),
            F.col("event_date").alias("as_of_date"),
            F.lit("daily_high_events").alias("anomaly_type"),
            F.col("events_day").cast("double").alias("score"),
            F.concat(F.lit("events_day="), F.col("events_day").cast("string"),
                     F.lit(", threshold="), F.lit(str(thr_daily_events))).alias("evidence"),
        )
        .orderBy(F.col("score").desc())
        .limit(args.top_n_per_rule)
    )

    daily_high_views = (
        user_daily.filter(F.col("views") >= F.lit(thr_daily_views))
        .select(
            F.col("user_id"),
            F.col("event_date").alias("as_of_date"),
            F.lit("daily_high_views").alias("anomaly_type"),
            F.col("views").cast("double").alias("score"),
            F.concat(F.lit("views="), F.col("views").cast("string"),
                     F.lit(", threshold="), F.lit(str(thr_daily_views))).alias("evidence"),
        )
        .orderBy(F.col("score").desc())
        .limit(args.top_n_per_rule)
    )

    # -----------------------------
    # Prepare session metrics
    # -----------------------------
    # We use events_cnt as spike measure
    thr_session_events = approx_threshold(session_funnel, "events_cnt", args.p_session)
    print(f"[threshold] session events_cnt p={args.p_session}: {thr_session_events}")

    session_spike_users = (
        session_funnel.filter(F.col("events_cnt") >= F.lit(thr_session_events))
        .groupBy("user_id")
        .agg(
            F.max("events_cnt").alias("max_events_cnt"),
            F.count("*").alias("spike_sessions_cnt"),
            F.min("session_start").alias("first_spike_time"),
            F.max("session_end").alias("last_spike_time"),
        )
        .select(
            F.col("user_id"),
            F.to_date("last_spike_time").alias("as_of_date"),
            F.lit("session_spike").alias("anomaly_type"),
            F.col("max_events_cnt").cast("double").alias("score"),
            F.concat(
                F.lit("max_events_cnt="), F.col("max_events_cnt").cast("string"),
                F.lit(", spike_sessions_cnt="), F.col("spike_sessions_cnt").cast("string"),
                F.lit(", threshold="), F.lit(str(thr_session_events))
            ).alias("evidence"),
        )
        .orderBy(F.col("score").desc())
        .limit(args.top_n_per_rule)
    )

    # -----------------------------
    # Funnel mismatch: many carts but no purchases (session-level)
    # -----------------------------
    mismatch_users = (
        session_funnel.filter(
            (F.col("carts_cnt") >= F.lit(args.min_carts_mismatch)) &
            (F.col("purchases_cnt") <= F.lit(args.max_purchases_mismatch))
        )
        .groupBy("user_id")
        .agg(
            F.sum("carts_cnt").alias("sum_carts_cnt"),
            F.sum("purchases_cnt").alias("sum_purchases_cnt"),
            F.count("*").alias("mismatch_sessions_cnt"),
            F.max("session_end").alias("last_time"),
        )
        .select(
            F.col("user_id"),
            F.to_date("last_time").alias("as_of_date"),
            F.lit("funnel_mismatch").alias("anomaly_type"),
            F.col("sum_carts_cnt").cast("double").alias("score"),
            F.concat(
                F.lit("sum_carts_cnt="), F.col("sum_carts_cnt").cast("string"),
                F.lit(", sum_purchases_cnt="), F.col("sum_purchases_cnt").cast("string"),
                F.lit(", mismatch_sessions_cnt="), F.col("mismatch_sessions_cnt").cast("string"),
                F.lit(", rule=carts>="), F.lit(str(args.min_carts_mismatch)),
                F.lit(" & purchases<="), F.lit(str(args.max_purchases_mismatch))
            ).alias("evidence"),
        )
        .orderBy(F.col("score").desc())
        .limit(args.top_n_per_rule)
    )

    # -----------------------------
    # Union all anomalies
    # -----------------------------
    anomalies = daily_high_events.unionByName(daily_high_views).unionByName(session_spike_users).unionByName(mismatch_users)

    out_path = tables_dir / "anomalous_users.csv"
    (anomalies.coalesce(1)
        .write.mode("overwrite")
        .option("header", True)
        .csv(str(out_path))
    )

    print("Wrote:", out_path.resolve())
    spark.stop()
    print("Done.")


if __name__ == "__main__":
    main()
