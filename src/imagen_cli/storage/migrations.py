"""Numbered database schema; never confuse this version with machine schemas."""

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
 version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
 current_turn_id TEXT REFERENCES turns(id), deleted_at TEXT, payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE turns (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 parent_turn_id TEXT REFERENCES turns(id), job_id TEXT REFERENCES jobs(id),
 creation_state TEXT NOT NULL DEFAULT 'queued',
 publication_state TEXT NOT NULL DEFAULT 'not_attempted', payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE batches (id TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE profiles (id TEXT PRIMARY KEY, name TEXT UNIQUE, payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE capabilities (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, family TEXT NOT NULL, model TEXT NOT NULL,
 model_revision TEXT NOT NULL DEFAULT '', operation TEXT NOT NULL,
 payload TEXT NOT NULL DEFAULT '{}', UNIQUE(provider,family,model,model_revision,operation));
CREATE TABLE plans (
 id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
 parent_turn_id TEXT REFERENCES turns(id),
 payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id),
 batch_id TEXT REFERENCES batches(id), plan_id TEXT UNIQUE REFERENCES plans(id),
 state TEXT NOT NULL DEFAULT 'queued', dispatch_state TEXT NOT NULL DEFAULT 'not_started',
 remote_state TEXT NOT NULL DEFAULT 'not_submitted',
 local_save_state TEXT NOT NULL DEFAULT 'not_started',
 payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE batch_children (
 batch_id TEXT NOT NULL REFERENCES batches(id), job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 position INTEGER NOT NULL CHECK(position>=0), PRIMARY KEY(batch_id,position));
CREATE TABLE artifacts (
 id TEXT PRIMARY KEY, producer_turn_id TEXT REFERENCES turns(id),
 producer_job_id TEXT REFERENCES jobs(id), managed_relpath TEXT UNIQUE NOT NULL,
 save_state TEXT NOT NULL DEFAULT 'staging'
 CHECK(save_state IN ('staging','published','save_failed')),
 payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE artifact_references (
 session_id TEXT NOT NULL REFERENCES sessions(id), turn_id TEXT NOT NULL REFERENCES turns(id),
 artifact_id TEXT NOT NULL REFERENCES artifacts(id), role TEXT NOT NULL
 CHECK(role IN ('input','mask','reference','output')), "index" INTEGER NOT NULL CHECK("index">=0),
 payload TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(turn_id,role,"index"));
CREATE TABLE native_contexts (
 id TEXT PRIMARY KEY, source_turn_id TEXT REFERENCES turns(id), payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE approvals (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL UNIQUE REFERENCES plans(id),
 execution_job_id TEXT UNIQUE REFERENCES jobs(id), state TEXT NOT NULL DEFAULT 'pending',
 payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE events (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
 job_seq INTEGER NOT NULL CHECK(job_seq>0),
 event_type TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
 UNIQUE(job_id,job_seq));
CREATE TABLE workers (id TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '{}');
CREATE INDEX turns_session ON turns(session_id);
CREATE INDEX artifacts_job ON artifacts(producer_job_id);
CREATE INDEX references_artifact ON artifact_references(artifact_id);
CREATE INDEX jobs_state ON jobs(state);
CREATE TRIGGER turn_parent_insert BEFORE INSERT ON turns
 WHEN NEW.parent_turn_id IS NOT NULL AND NOT EXISTS
 (SELECT 1 FROM turns WHERE id=NEW.parent_turn_id AND session_id=NEW.session_id)
 BEGIN SELECT RAISE(ABORT,'parent belongs to another session'); END;
CREATE TRIGGER turn_parent_update BEFORE UPDATE ON turns
 WHEN NEW.parent_turn_id IS NOT NULL AND NOT EXISTS
 (SELECT 1 FROM turns WHERE id=NEW.parent_turn_id AND session_id=NEW.session_id)
 BEGIN SELECT RAISE(ABORT,'parent belongs to another session'); END;
CREATE TRIGGER reference_session BEFORE INSERT ON artifact_references
 WHEN NOT EXISTS (SELECT 1 FROM turns WHERE id=NEW.turn_id AND session_id=NEW.session_id)
 BEGIN SELECT RAISE(ABORT,'reference belongs to another session'); END;
CREATE TRIGGER current_turn_update BEFORE UPDATE OF current_turn_id ON sessions
 WHEN NEW.current_turn_id IS NOT NULL AND NOT EXISTS
 (SELECT 1 FROM turns WHERE id=NEW.current_turn_id
 AND session_id=NEW.id AND creation_state='succeeded')
 BEGIN SELECT RAISE(ABORT,'current turn must be successful and belong to session'); END;
"""


SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS provider_models (
 id TEXT PRIMARY KEY,
 profile_id TEXT NOT NULL REFERENCES profiles(id),
 model_id TEXT NOT NULL, display_name TEXT,
 fetched_at TEXT NOT NULL, source_url TEXT,
 evidence TEXT NOT NULL CHECK(evidence IN ('discovered','manual')),
 payload TEXT NOT NULL DEFAULT '{}',
 UNIQUE(profile_id, model_id));
CREATE INDEX IF NOT EXISTS provider_models_profile ON provider_models(profile_id);
CREATE TABLE IF NOT EXISTS artifact_links (
 session_id TEXT NOT NULL REFERENCES sessions(id),
 artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 role TEXT NOT NULL CHECK(role IN ('input','mask','reference','output')),
 created_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(session_id, artifact_id, role));
CREATE INDEX IF NOT EXISTS artifact_links_artifact ON artifact_links(artifact_id);
"""


SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS remote_jobs (
 id TEXT PRIMARY KEY,
 provider TEXT NOT NULL,
 provider_job_id TEXT,
 queue_id TEXT,
 endpoint_host TEXT NOT NULL,
 operation TEXT NOT NULL,
 input_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 input_sha256 TEXT NOT NULL,
 request_canonical TEXT NOT NULL,
 job_digest TEXT NOT NULL,
 pipeline TEXT,
 gpu TEXT,
 max_seconds INTEGER NOT NULL CHECK(max_seconds>0),
 license TEXT,
 worker_image_digest TEXT,
 status TEXT NOT NULL,
 artifact_id TEXT REFERENCES artifacts(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 payload TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS remote_jobs_status ON remote_jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS remote_jobs_digest ON remote_jobs(job_digest);
CREATE INDEX IF NOT EXISTS remote_jobs_provider ON remote_jobs(provider);
"""

SCHEMA_V4 = """
CREATE TABLE remote_jobs_v4 (
 id TEXT PRIMARY KEY,
 provider TEXT NOT NULL,
 provider_job_id TEXT,
 queue_id TEXT,
 endpoint_host TEXT NOT NULL,
 operation TEXT NOT NULL,
 input_artifact_id TEXT NOT NULL,
 input_sha256 TEXT NOT NULL,
 request_canonical TEXT NOT NULL,
 job_digest TEXT NOT NULL,
 pipeline TEXT,
 gpu TEXT,
 max_seconds INTEGER NOT NULL CHECK(max_seconds>0),
 license TEXT,
 worker_image_digest TEXT,
 status TEXT NOT NULL,
 artifact_id TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 payload TEXT NOT NULL DEFAULT '{}'
);
INSERT INTO remote_jobs_v4
 SELECT id,provider,provider_job_id,queue_id,endpoint_host,operation,input_artifact_id,
 input_sha256,request_canonical,job_digest,pipeline,gpu,max_seconds,license,
 worker_image_digest,status,artifact_id,created_at,updated_at,payload
 FROM remote_jobs;
DROP TABLE remote_jobs;
ALTER TABLE remote_jobs_v4 RENAME TO remote_jobs;
CREATE INDEX remote_jobs_status ON remote_jobs(status);
CREATE UNIQUE INDEX remote_jobs_digest ON remote_jobs(job_digest);
CREATE INDEX remote_jobs_provider ON remote_jobs(provider);
"""


def migrate(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise ValueError("migration requires no active transaction")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError("database schema is newer than supported")
    if version == SCHEMA_VERSION:
        return
    filename = connection.execute("PRAGMA database_list").fetchone()[2]
    has_tables = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
    ).fetchone()
    if filename and has_tables:
        backup_path = Path(f"{filename}.v{version}.bak")
        fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        backup = sqlite3.connect(backup_path)
        try:
            connection.backup(backup)
        finally:
            backup.close()
    # Each step owns its transaction so DDL and the version advance are atomic.
    if version == 0:
        steps = [(1, SCHEMA), (2, SCHEMA_V2), (3, SCHEMA_V3), (4, SCHEMA_V4)]
    elif version == 1:
        steps = [(2, SCHEMA_V2), (3, SCHEMA_V3), (4, SCHEMA_V4)]
    elif version == 2:
        steps = [(3, SCHEMA_V3), (4, SCHEMA_V4)]
    else:
        steps = [(4, SCHEMA_V4)]
    try:
        for target, script in steps:
            if target <= version:
                continue
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + script + f"\nPRAGMA user_version={target};\nCOMMIT;"
            )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
