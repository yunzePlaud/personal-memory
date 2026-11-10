-- PMI truth database, schema version 1.
CREATE TABLE sources (
  id              TEXT PRIMARY KEY,
  origin_key      TEXT,
  version         INTEGER NOT NULL DEFAULT 1,
  prev_version_id TEXT REFERENCES sources(id),
  is_latest       INTEGER NOT NULL DEFAULT 1 CHECK (is_latest IN (0, 1)),
  kind            TEXT NOT NULL CHECK (kind IN ('document','note','conversation','agent_output')),
  title           TEXT NOT NULL,
  mime            TEXT,
  text            TEXT NOT NULL,
  text_hash       TEXT NOT NULL,
  file_path       TEXT,
  file_hash       TEXT,
  source_time     TEXT,
  imported_at     TEXT NOT NULL,
  trust           TEXT NOT NULL CHECK (trust IN ('owner','agent','external')),
  metadata        TEXT NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','forgotten')),
  created_by      TEXT NOT NULL
);
CREATE INDEX ix_sources_origin ON sources(origin_key, version);
CREATE INDEX ix_sources_hash ON sources(text_hash);

CREATE TABLE memories (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('episodic','semantic','project','person','decision','procedure','preference')),
  title         TEXT NOT NULL,
  content       TEXT NOT NULL,
  content_key   TEXT NOT NULL,
  slot          TEXT,
  tags          TEXT NOT NULL DEFAULT '[]',
  status        TEXT NOT NULL CHECK (status IN ('active','superseded','disputed','archived','forgotten')),
  verification  TEXT NOT NULL CHECK (verification IN ('user_confirmed','source_backed','inferred')),
  confidence    REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  valid_from    TEXT,
  valid_until   TEXT,
  superseded_by TEXT REFERENCES memories(id) DEFERRABLE INITIALLY DEFERRED,
  version       INTEGER NOT NULL DEFAULT 1,
  created_by    TEXT NOT NULL,
  archived_by   TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_memories_active_slot ON memories(slot) WHERE status = 'active' AND slot IS NOT NULL;
CREATE INDEX ix_memories_content_key ON memories(content_key);
CREATE INDEX ix_memories_status ON memories(status);

CREATE TABLE memory_versions (
  memory_id   TEXT NOT NULL REFERENCES memories(id),
  version     INTEGER NOT NULL,
  snapshot    TEXT NOT NULL,
  change_kind TEXT NOT NULL CHECK (change_kind IN ('create','update','supersede','confirm','dispute','archive','restore','forget')),
  actor       TEXT NOT NULL,
  reason      TEXT,
  proposal_id TEXT,
  changed_at  TEXT NOT NULL,
  PRIMARY KEY (memory_id, version)
);

CREATE TABLE evidence (
  id         INTEGER PRIMARY KEY,
  memory_id  TEXT NOT NULL REFERENCES memories(id),
  source_id  TEXT NOT NULL REFERENCES sources(id),
  quote      TEXT NOT NULL,
  span_start INTEGER NOT NULL,
  span_end   INTEGER NOT NULL,
  added_by   TEXT NOT NULL,
  added_at   TEXT NOT NULL,
  UNIQUE (memory_id, source_id, span_start, span_end)
);
CREATE INDEX ix_evidence_source ON evidence(source_id);

CREATE TABLE entities (
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL CHECK (kind IN ('project','person','org','topic','system','other')),
  name       TEXT NOT NULL,
  norm_name  TEXT NOT NULL,
  aliases    TEXT NOT NULL DEFAULT '[]',
  note       TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (kind, norm_name)
);

CREATE TABLE memory_entities (
  memory_id TEXT NOT NULL REFERENCES memories(id),
  entity_id TEXT NOT NULL REFERENCES entities(id),
  role      TEXT NOT NULL CHECK (role IN ('subject','owner','participant','mentioned')),
  PRIMARY KEY (memory_id, entity_id, role)
);
CREATE INDEX ix_memory_entities_entity ON memory_entities(entity_id);

CREATE TABLE links (
  id         INTEGER PRIMARY KEY,
  from_id    TEXT NOT NULL REFERENCES memories(id),
  to_id      TEXT NOT NULL REFERENCES memories(id),
  type       TEXT NOT NULL CHECK (type IN ('supersedes','contradicts','supports','relates_to','part_of')),
  actor      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (from_id, to_id, type)
);

CREATE TABLE proposals (
  id          TEXT PRIMARY KEY,
  actor       TEXT NOT NULL,
  ops         TEXT NOT NULL,
  reason      TEXT,
  risk        TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('pending','applied','rejected','failed')),
  result      TEXT,
  created_at  TEXT NOT NULL,
  resolved_at TEXT,
  resolved_by TEXT
);
CREATE INDEX ix_proposals_status ON proposals(status);

CREATE TABLE audit_log (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id   TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE changes (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  target_type TEXT NOT NULL CHECK (target_type IN ('source','memory')),
  target_id   TEXT NOT NULL,
  op          TEXT NOT NULL CHECK (op IN ('upsert','delete'))
);
