-- Phase 4: owned schema with row-level security.
-- Idempotent. Applied by `make migrate` as the owner role.
--
-- The enforcement point is the database, not the application. Retrieval runs as a
-- restricted role that cannot see chunks the caller's groups do not grant. A bug in
-- the Python layer cannot widen what a query returns.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    doc_id          text        NOT NULL,
    version         int         NOT NULL,
    title           text        NOT NULL,
    department      text        NOT NULL,
    classification  text        NOT NULL,
    status          text        NOT NULL DEFAULT 'active',
    effective_date  date,
    owner           text,
    source_path     text,
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_id, version)
);

-- One row per group grant. A chunk is visible only through its document's grants.
CREATE TABLE IF NOT EXISTS doc_acl (
    doc_id     text NOT NULL,
    version    int  NOT NULL,
    group_name text NOT NULL,
    PRIMARY KEY (doc_id, version, group_name),
    FOREIGN KEY (doc_id, version) REFERENCES documents (doc_id, version) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    uuid PRIMARY KEY,
    doc_id      text NOT NULL,
    version     int  NOT NULL,
    chunk_index int  NOT NULL,
    content     text NOT NULL,
    embedding   vector(768) NOT NULL,
    FOREIGN KEY (doc_id, version) REFERENCES documents (doc_id, version) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS chunks_fts_idx
    ON chunks USING gin (to_tsvector('english', content));
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_acl_group_idx ON doc_acl (group_name);

-- Caller groups arrive as a session setting, set with SET LOCAL inside the request
-- transaction. current_setting(..., true) returns NULL when unset, so an unset session
-- matches no rows: fail closed by construction, not by an application check.
CREATE OR REPLACE FUNCTION caller_groups() RETURNS text[]
LANGUAGE sql STABLE AS $$
    SELECT string_to_array(coalesce(current_setting('app.groups', true), ''), ',')
$$;

ALTER TABLE chunks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_acl   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chunks_by_acl ON chunks;
CREATE POLICY chunks_by_acl ON chunks FOR SELECT USING (
    EXISTS (
        SELECT 1 FROM doc_acl a
        WHERE a.doc_id = chunks.doc_id
          AND a.version = chunks.version
          AND a.group_name = ANY (caller_groups())
    )
);

DROP POLICY IF EXISTS documents_by_acl ON documents;
CREATE POLICY documents_by_acl ON documents FOR SELECT USING (
    EXISTS (
        SELECT 1 FROM doc_acl a
        WHERE a.doc_id = documents.doc_id
          AND a.version = documents.version
          AND a.group_name = ANY (caller_groups())
    )
);

-- doc_acl is readable so the policies above can evaluate; it holds no document content.
DROP POLICY IF EXISTS doc_acl_readable ON doc_acl;
CREATE POLICY doc_acl_readable ON doc_acl FOR SELECT USING (true);

-- The query role. NOBYPASSRLS is the point: the owner role is exempt from policies,
-- so retrieval must never run as the owner.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_app') THEN
        CREATE ROLE rag_app LOGIN PASSWORD 'rag_app' NOBYPASSRLS;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO rag_app;
GRANT SELECT ON documents, doc_acl, chunks TO rag_app;
REVOKE INSERT, UPDATE, DELETE ON documents, doc_acl, chunks FROM rag_app;

-- Phase 5: every model call is recorded, allowed or denied.
-- A denied call is evidence too — it is how you prove the policy was applied
-- rather than merely configured.
CREATE TABLE IF NOT EXISTS model_calls (
    call_id          uuid PRIMARY KEY,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    correlation_id   uuid        NOT NULL,
    principal        text,
    requested_model  text        NOT NULL,
    resolved_model   text,
    classification   text        NOT NULL,
    decision         text        NOT NULL,   -- allow | deny
    reason           text        NOT NULL,
    rule_id          text,
    cache_hit        boolean     NOT NULL DEFAULT false,
    prompt_tokens    int,
    completion_tokens int,
    cost_usd         numeric(12, 6),
    latency_ms       int
);

CREATE INDEX IF NOT EXISTS model_calls_occurred_idx ON model_calls (occurred_at DESC);
CREATE INDEX IF NOT EXISTS model_calls_correlation_idx ON model_calls (correlation_id);

CREATE TABLE IF NOT EXISTS response_cache (
    cache_key   text PRIMARY KEY,
    model_id    text        NOT NULL,
    response    text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    hits        int         NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS response_cache_created_idx ON response_cache (created_at);
