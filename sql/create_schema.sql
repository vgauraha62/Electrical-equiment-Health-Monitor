CREATE TABLE IF NOT EXISTS `PROJECT_ID.DATASET_ID.telemetry_observation` (
  source_file_id STRING NOT NULL,
  source_record_index INT64 NOT NULL,
  psr_id STRING NOT NULL,
  measurement_point_id STRING NOT NULL,
  equipment_type_code STRING NOT NULL,
  measurement_type_code STRING NOT NULL,
  measurement_name STRING NOT NULL,
  measurement_value NUMERIC,
  measurement_unit STRING,
  observed_at STRING NOT NULL,
  dataset_type STRING NOT NULL,
  data_origin STRING NOT NULL,
  source_uri STRING NOT NULL,
  quality_flags STRING
);
