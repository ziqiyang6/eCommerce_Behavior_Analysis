#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--events-dir", default="data/processed/events_parquet", help="Silver table path")
    p.add_argument("--out-user-daily", default="data/processed/user_daily", help="Gold: user_daily path")
    p.add_argument("--out-session-funnel", default="data/processed/session_funnel", help="Gold: session_funnel path")
    p.add_argument("--driver-memory", default="8g")
    p.add_argument("--shuffle-partitions", type=int, default=200)
    p.add_argument("--repartition", type=int, default=120, help="Repartition by event_date before write; 0 disables")
    return p.parse_args()

def build_spark(app_name: str, driver_memory: str, shuffle_partitions: int) -> SparkSession:
    spark = (
        SparkSession.builder
        .appName(app_name)
        .master("local[*]")
        .config("spark.driver.memory", driver_memory)
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        .config("spark.driver.extraJavaOptions", "-Dlog4j2.configurationFile=conf/log4j2.properties")
        .getOrCreate()
    )
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.sparkContext.setLogLevel("WARN")
    return spark

def ensure_dir(p: str) -> str:
    Path(p).mkdir(parents=True, exist_ok=True)
    return p

def main():
    args = parse_args()
    spark = build_spark("ecom-gold", args.driver_memory, args.shuffle_partitions)

    events_dir = args.events_dir
    print("Reading Silver events from:", Path(events_dir).resolve())
    events = spark.read.parquet(events_dir)

    # -----------------------------
    # Gold table 1: user_daily
    # Key: (user_id, event_date)
    # -----------------------------
    user_daily = (
        events.groupBy("user_id", "event_date")
        .agg(
            F.sum(F.when(F.col("event_type") == "view", 1).otherwise(0)).alias("views"),
            F.sum(F.when(F.col("event_type") == "cart", 1).otherwise(0)).alias("carts"),
            F.sum(F.when(F.col("event_type") == "remove_from_cart", 1).otherwise(0)).alias("removes"),
            F.sum(F.when(F.col("event_type") == "purchase", 1).otherwise(0)).alias("purchases"),
            F.countDistinct("user_session").alias("sessions"),
            F.sum(F.when(F.col("event_type") == "purchase", F.col("price")).otherwise(F.lit(0.0))).alias("revenue"),
            F.countDistinct(F.when(F.col("event_type") == "view", F.col("product_id"))).alias("unique_products_viewed"),
            F.countDistinct(F.when(F.col("event_type") == "purchase", F.col("product_id"))).alias("unique_products_purchased"),
        )
    )

    if args.repartition and args.repartition > 0:
        user_daily = user_daily.repartition(args.repartition, F.col("event_date"))

    out_user_daily = ensure_dir(args.out_user_daily)
    (user_daily.write
        .mode("overwrite")
        .partitionBy("event_date")
        .parquet(out_user_daily)
    )
    print("Wrote Gold user_daily to:", Path(out_user_daily).resolve())

    # -----------------------------
    # Gold table 2: session_funnel
    # Key: (user_id, user_session)
    # -----------------------------
    session_funnel = (
        events.groupBy("user_id", "user_session")
        .agg(
            F.min("event_time").alias("session_start"),
            F.max("event_time").alias("session_end"),
            F.count("*").alias("events_cnt"),
            F.sum(F.when(F.col("event_type") == "view", 1).otherwise(0)).alias("views_cnt"),
            F.sum(F.when(F.col("event_type") == "cart", 1).otherwise(0)).alias("carts_cnt"),
            F.sum(F.when(F.col("event_type") == "remove_from_cart", 1).otherwise(0)).alias("removes_cnt"),
            F.sum(F.when(F.col("event_type") == "purchase", 1).otherwise(0)).alias("purchases_cnt"),
            F.sum(F.when(F.col("event_type") == "purchase", F.col("price")).otherwise(F.lit(0.0))).alias("revenue"),
            F.countDistinct("product_id").alias("unique_products"),
            F.countDistinct("category_id").alias("unique_categories"),
        )
        .withColumn("session_minutes", (F.unix_timestamp("session_end") - F.unix_timestamp("session_start")) / 60.0)
        .withColumn("has_view", (F.col("views_cnt") > 0).cast("int"))
        .withColumn("has_cart", (F.col("carts_cnt") > 0).cast("int"))
        .withColumn("has_purchase", (F.col("purchases_cnt") > 0).cast("int"))
        .withColumn("session_date", F.to_date("session_start"))
    )

    if args.repartition and args.repartition > 0:
        session_funnel = session_funnel.repartition(args.repartition, F.col("session_date"))

    out_session_funnel = ensure_dir(args.out_session_funnel)
    (session_funnel.write
        .mode("overwrite")
        .partitionBy("session_date")
        .parquet(out_session_funnel)
    )
    print("Wrote Gold session_funnel to:", Path(out_session_funnel).resolve())

    spark.stop()
    print("Done.")

if __name__ == "__main__":
    main()
