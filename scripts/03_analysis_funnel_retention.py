#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


RETENTION_OFFSETS_DEFAULT = "1,7,30"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--user-daily", default="data/processed/user_daily", help="Gold table: user_daily")
    p.add_argument("--session-funnel", default="data/processed/session_funnel", help="Gold table: session_funnel")
    p.add_argument("--out-dir", default="data/outputs", help="Output root dir (tables/figures)")
    p.add_argument("--driver-memory", default="8g")
    p.add_argument("--shuffle-partitions", type=int, default=200)
    p.add_argument("--retention-offsets", default=RETENTION_OFFSETS_DEFAULT, help="Comma-separated days, e.g. 1,7,30")
    p.add_argument("--plots", action="store_true", help="If set, also save simple plots (requires pandas+matplotlib).")
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


def ensure_dirs(out_dir: str):
    out_root = Path(out_dir)
    tables_dir = out_root / "tables"
    figs_dir = out_root / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    return tables_dir, figs_dir


def safe_div(numer_col, denom_col):
    return F.when(denom_col == 0, F.lit(None)).otherwise(numer_col / denom_col)


def main():
    args = parse_args()
    tables_dir, figs_dir = ensure_dirs(args.out_dir)

    offsets = [int(x.strip()) for x in args.retention_offsets.split(",") if x.strip()]

    spark = build_spark("ecom-analysis", args.driver_memory, args.shuffle_partitions)

    # -------------------------
    # 1) Funnel (session_funnel)
    # -------------------------
    session_funnel = spark.read.parquet(args.session_funnel)

    # Expect columns:
    # has_view, has_cart, has_purchase, views_cnt, carts_cnt, purchases_cnt, revenue, session_date ...
    funnel_overall = (
        session_funnel.agg(
            F.count("*").alias("sessions_total"),
            F.sum(F.col("has_view")).alias("sessions_with_view"),
            F.sum(F.col("has_cart")).alias("sessions_with_cart"),
            F.sum(F.col("has_purchase")).alias("sessions_with_purchase"),
        )
        .withColumn("view_rate", safe_div(F.col("sessions_with_view"), F.col("sessions_total")))
        .withColumn("cart_rate", safe_div(F.col("sessions_with_cart"), F.col("sessions_total")))
        .withColumn("purchase_rate", safe_div(F.col("sessions_with_purchase"), F.col("sessions_total")))
        .withColumn("view_to_cart", safe_div(F.col("sessions_with_cart"), F.col("sessions_with_view")))
        .withColumn("cart_to_purchase", safe_div(F.col("sessions_with_purchase"), F.col("sessions_with_cart")))
        .withColumn("view_to_purchase", safe_div(F.col("sessions_with_purchase"), F.col("sessions_with_view")))
    )

    funnel_path = tables_dir / "funnel_overall.csv"
    funnel_overall.coalesce(1).write.mode("overwrite").option("header", True).csv(str(funnel_path))
    print("Wrote:", funnel_path.resolve())

    # Also useful: daily funnel trend (by session_date)
    funnel_daily = (
        session_funnel.groupBy("session_date")
        .agg(
            F.count("*").alias("sessions_total"),
            F.sum(F.col("has_view")).alias("sessions_with_view"),
            F.sum(F.col("has_cart")).alias("sessions_with_cart"),
            F.sum(F.col("has_purchase")).alias("sessions_with_purchase"),
        )
        .withColumn("view_to_cart", safe_div(F.col("sessions_with_cart"), F.col("sessions_with_view")))
        .withColumn("cart_to_purchase", safe_div(F.col("sessions_with_purchase"), F.col("sessions_with_cart")))
        .withColumn("view_to_purchase", safe_div(F.col("sessions_with_purchase"), F.col("sessions_with_view")))
        .orderBy("session_date")
    )

    funnel_daily_path = tables_dir / "funnel_daily.csv"
    funnel_daily.coalesce(1).write.mode("overwrite").option("header", True).csv(str(funnel_daily_path))
    print("Wrote:", funnel_daily_path.resolve())

    # -------------------------
    # 2) Cohort Retention (user_daily)
    # -------------------------
    user_daily = spark.read.parquet(args.user_daily)

    # Define "active" day: any event (views+carts+removes+purchases) > 0
    user_daily = user_daily.withColumn(
        "active",
        (F.col("views") + F.col("carts") + F.col("removes") + F.col("purchases") > 0).cast("int")
    )

    # cohort = first_seen_date per user
    first_seen = user_daily.groupBy("user_id").agg(F.min("event_date").alias("first_seen_date"))

    ud = (
        user_daily.join(first_seen, on="user_id", how="inner")
        .withColumn("day_index", F.datediff(F.col("event_date"), F.col("first_seen_date")))
        .filter(F.col("day_index") >= 0)
    )

    # cohort size (day 0 active users)
    cohort_size = (
        ud.filter(F.col("day_index") == 0)
        .groupBy("first_seen_date")
        .agg(F.countDistinct("user_id").alias("cohort_size"))
    )

    # retention counts per offset
    retention_counts = None
    for d in offsets:
        cnt = (
            ud.filter((F.col("day_index") == d) & (F.col("active") == 1))
            .groupBy("first_seen_date")
            .agg(F.countDistinct("user_id").alias(f"retained_d{d}"))
        )
        retention_counts = cnt if retention_counts is None else retention_counts.join(cnt, on="first_seen_date", how="left")

    retention_by_cohort = cohort_size.join(retention_counts, on="first_seen_date", how="left")

    # rates
    for d in offsets:
        retention_by_cohort = retention_by_cohort.withColumn(
            f"retention_d{d}",
            safe_div(F.col(f"retained_d{d}"), F.col("cohort_size"))
        )

    retention_by_cohort = retention_by_cohort.orderBy("first_seen_date")

    retention_path = tables_dir / "retention_by_cohort.csv"
    retention_by_cohort.coalesce(1).write.mode("overwrite").option("header", True).csv(str(retention_path))
    print("Wrote:", retention_path.resolve())

    # Overall retention (weighted by cohort size)
    # sum(retained)/sum(cohort_size)
    agg_exprs = [F.sum("cohort_size").alias("total_users")]
    for d in offsets:
        agg_exprs.append(F.sum(F.col(f"retained_d{d}")).alias(f"total_retained_d{d}"))

    retention_overall = retention_by_cohort.agg(*agg_exprs)
    for d in offsets:
        retention_overall = retention_overall.withColumn(
            f"overall_retention_d{d}",
            safe_div(F.col(f"total_retained_d{d}"), F.col("total_users"))
        )

    retention_overall_path = tables_dir / "retention_overall.csv"
    retention_overall.coalesce(1).write.mode("overwrite").option("header", True).csv(str(retention_overall_path))
    print("Wrote:", retention_overall_path.resolve())

    # -------------------------
    # Optional plots (small data)
    # -------------------------
    if args.plots:
        try:
            import pandas as pd
            import matplotlib.pyplot as plt
        except Exception as e:
            print("Plots requested but pandas/matplotlib not available:", e)
            spark.stop()
            return

        # Spark writes CSV as a folder. Grab the part file.
        def read_single_csv_dir(csv_dir: Path) -> pd.DataFrame:
            part = next(csv_dir.glob("part-*.csv"))
            return pd.read_csv(part)

        # Funnel overall plot (bar-like)
        f_overall = read_single_csv_dir(funnel_path)
        # retention overall plot
        r_overall = read_single_csv_dir(retention_overall_path)

        # Plot 1: Funnel conversion rates
        fig1 = plt.figure(figsize=(8, 4))
        labels = ["view_to_cart", "cart_to_purchase", "view_to_purchase"]
        values = [float(f_overall.loc[0, x]) if x in f_overall.columns and pd.notna(f_overall.loc[0, x]) else 0.0 for x in labels]
        plt.bar(labels, values)
        plt.title("Funnel conversion rates (session-level)")
        plt.ylabel("Rate")
        plt.xticks(rotation=20)
        fig1.tight_layout()
        fig1_path = figs_dir / "funnel_rates.png"
        fig1.savefig(fig1_path, dpi=200)
        plt.close(fig1)
        print("Wrote:", fig1_path.resolve())

        # Plot 2: Overall retention
        fig2 = plt.figure(figsize=(8, 4))
        labels2 = [f"overall_retention_d{d}" for d in offsets]
        values2 = [float(r_overall.loc[0, x]) if x in r_overall.columns and pd.notna(r_overall.loc[0, x]) else 0.0 for x in labels2]
        plt.bar(labels2, values2)
        plt.title("Overall retention (cohort-weighted)")
        plt.ylabel("Rate")
        plt.xticks(rotation=20)
        fig2.tight_layout()
        fig2_path = figs_dir / "retention_overall.png"
        fig2.savefig(fig2_path, dpi=200)
        plt.close(fig2)
        print("Wrote:", fig2_path.resolve())

    spark.stop()
    print("Done.")


if __name__ == "__main__":
    main()
