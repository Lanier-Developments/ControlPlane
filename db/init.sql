-- Phase 1: LangChain's PGVector creates and manages its own tables.
-- Phase 4 replaces them with an owned schema (documents, chunks, acl) and row-level security.
CREATE EXTENSION IF NOT EXISTS vector;
