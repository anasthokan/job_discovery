USE ezyjob_master;

CREATE TABLE IF NOT EXISTS job_discovery_jobs (
  id VARCHAR(255) NOT NULL,
  title VARCHAR(512) NOT NULL,
  company VARCHAR(512) NOT NULL,
  skills JSON NULL,
  state VARCHAR(128) NULL,
  location VARCHAR(512) NULL,
  platform VARCHAR(128) NULL,
  days_ago INT NOT NULL DEFAULT 0,
  url VARCHAR(512) NULL,
  posted_at VARCHAR(64) NULL,
  e_verified VARCHAR(16) NOT NULL DEFAULT 'unknown',
  e_verify_name VARCHAR(512) NULL,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_jd_jobs_url (url),
  KEY idx_jd_jobs_platform (platform),
  KEY idx_jd_jobs_last_seen (last_seen_at),
  KEY idx_jd_jobs_everify (e_verified)
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
