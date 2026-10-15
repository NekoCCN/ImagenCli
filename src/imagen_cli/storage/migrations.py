"""Numbered database schema; never confuse this version with machine schemas."""

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

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
    # executescript owns the transaction so DDL and version advance are atomic.
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + SCHEMA + "\nPRAGMA user_version=1;\nCOMMIT;"
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
