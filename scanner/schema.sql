-- PR Scanner — metadata schema.
-- Run once against the RDS database (see README for how).

CREATE TABLE IF NOT EXISTS scans (
    scan_id           UUID PRIMARY KEY,
    repo              TEXT        NOT NULL,
    pr_number         INTEGER     NOT NULL,
    head_sha          TEXT        NOT NULL,
    status            TEXT        NOT NULL,
    total_findings    INTEGER     NOT NULL DEFAULT 0,
    blocking_findings INTEGER     NOT NULL DEFAULT 0,
    s3_key            TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scans_repo_pr ON scans (repo, pr_number);
CREATE INDEX IF NOT EXISTS idx_scans_created ON scans (created_at DESC);
