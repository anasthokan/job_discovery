USE ezyjob;

CREATE TABLE IF NOT EXISTS jobs (
  id BIGINT NOT NULL AUTO_INCREMENT,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  public_id CHAR(32) NOT NULL,
  title VARCHAR(512) NOT NULL,
  location VARCHAR(512) NOT NULL,
  description LONGTEXT NOT NULL,
  posted_at DATETIME(6) NULL,
  is_active TINYINT(1) NOT NULL,
  company_ref_id BIGINT NULL,
  platform VARCHAR(120) NOT NULL,
  days_ago INT UNSIGNED NULL,
  company VARCHAR(255) NULL,
  e_verified VARCHAR(64) NOT NULL,
  e_verify_name VARCHAR(255) NOT NULL,
  first_seen_at DATETIME(6) NULL,
  last_seen_at DATETIME(6) NULL,
  skills JSON NOT NULL,
  source_id VARCHAR(255) NULL,
  state VARCHAR(255) NOT NULL,
  url VARCHAR(1000) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_jobs_public_id (public_id),
  UNIQUE KEY uk_jobs_source_id (source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS job_discovery_scrape_runs (
  id BIGINT NOT NULL AUTO_INCREMENT,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  job_count INT NOT NULL DEFAULT 0,
  keywords VARCHAR(512) NULL,
  state VARCHAR(128) NULL,
  platform VARCHAR(128) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  error TEXT NULL,
  PRIMARY KEY (id),
  KEY idx_scrape_runs_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS job_discovery_everify_employers (
  name_key VARCHAR(255) NOT NULL,
  employer VARCHAR(512) NOT NULL,
  dba VARCHAR(512) NULL,
  account_status VARCHAR(64) NULL,
  everify_plus VARCHAR(32) NULL,
  date_enrolled VARCHAR(64) NULL,
  hiring_sites VARCHAR(512) NULL,
  PRIMARY KEY (name_key),
  KEY idx_jd_everify_status (account_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
