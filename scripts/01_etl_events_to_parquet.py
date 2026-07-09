#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType, LongType, DoubleType

ALLOWED = ["view", "cart", "remove_from_cart", "purchase"]

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input-glob", default="data/raw/*.csv")
    p.add_argument("--output-dir", default="data/processed/events_parquet")
    p.add_argument("--driver-memory", default="8g")
    p.add_argument("--shuffle-partitions", type=int, default=200)
    p.add_argument("--repartition", type=int, default=120)
    return p.parse_args()

def resolve_files(pattern: str):
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No files matched: {pattern}")
    return [str(Path(f).resolve()) for f in files]

def schema():
    return StructType([
        StructField("event_time", StringType(), True),
        StructField("event_type", StringType(), True),
        StructField("product_id", LongType(), True),
        StructField("category_id", LongType(), True),
        StructField("category_code", StringType(), True),
        StructField("brand", StringType(), True),
        StructField("price", DoubleType(), True),
        StructField("user_id", LongType(), True),
        StructField("user_session", StringType(), True),
    ])

def norm_str(c):
    x = F.lower(F.trim(F.col(c)))
    return F.when(x.isNull() | (x == "") | (x == "nan"), F.lit(None)).otherwise(x)

def main():
    args = parse_args()
    files = resolve_files(args.input_glob)
    print(f"Matched {len(files)} file(s). Example: {files[0]}")

    spark = (
        SparkSession.builder
        .appName("ecom-etl-silver")
        .master("local[*]")
        .config("spark.driver.memory", args.driver_memory)
        .config("spark.sql.shuffle.partitions", str(args.shuffle_partitions))
        .config("spark.driver.extraJavaOptions", "-Dlog4j2.configurationFile=conf/log4j2.properties")
        .getOrCreate()
    )
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.sparkContext.setLogLevel("WARN")

    df = spark.read.option("header", True).schema(schema()).csv(files)

    clean = (
        df
        .withColumn("event_type", F.lower(F.trim(F.col("event_type"))))
        .withColumn("brand", norm_str("brand"))
        .withColumn("category_code", norm_str("category_code"))
        .withColumn("user_session", F.trim(F.col("user_session")))
        .withColumn("event_time_str", F.regexp_replace(F.col("event_time"), r"\s+UTC$", ""))
        .withColumn("event_time", F.to_timestamp(F.col("event_time_str"), "yyyy-MM-dd HH:mm:ss"))
        .withColumn("event_date", F.to_date(F.col("event_time")))
        .withColumn("event_hour", F.hour(F.col("event_time")))
        .withColumn("event_month", F.date_format(F.col("event_time"), "yyyy-MM"))
        .filter(F.col("event_time").isNotNull())
        .filter(F.col("event_date").isNotNull())
        .filter(F.col("event_type").isin(ALLOWED))
        .filter(F.col("price").isNotNull() & (F.col("price") >= 0) & (F.col("price") < 1_000_000))
        .drop("event_time_str")
    )

    if args.repartition and args.repartition > 0:
        clean = clean.repartition(args.repartition, F.col("event_date"))

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (clean.write
        .mode("overwrite")
        .partitionBy("event_date")
        .parquet(str(out_dir))
    )

    spark.stop()
    print("Wrote parquet to:", out_dir.resolve())

if __name__ == "__main__":
    main()
