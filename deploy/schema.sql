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
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_jd_jobs_url (url),
  KEY idx_jd_jobs_platform (platform),
  KEY idx_jd_jobs_last_seen (last_seen_at)
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
  KEY idx_jd_scrape_runs_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
