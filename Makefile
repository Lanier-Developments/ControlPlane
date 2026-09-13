Q ?= How many PTO days do full-time employees get?
USER ?= sam

up:            ; docker compose up -d db
up-local-llm:  ; docker compose --profile local-llm up -d db ollama
pull-models:   ; ollama pull nomic-embed-text && ollama pull llama3.1:8b
migrate:       ; python -m provenance.ingest --migrate
ingest:        ; python -m provenance.ingest
reset:         ; python -m provenance.ingest --reset
reset-db:      ; docker compose down -v && docker compose up -d db
rls-test:      ; python -m provenance.rls_test
ask:           ; python -m provenance.chain "$(Q)" --user $(USER)
api:           ; uvicorn provenance.api:app --reload
down:          ; docker compose --profile local-llm --profile api down
eval:          ; python -m provenance.evals --tier retrieval
eval-full:     ; python -m provenance.evals --tier full
baseline:      ; python -m provenance.evals --tier full --write-baseline
compare:       ; python -m provenance.compare vector hybrid
policy-test:   ; python -m provenance.policy_test
usage:         ; python -m provenance.usage --hours 24
verify:        ; python -m provenance.audit verify
trace:         ; python -m provenance.audit trace $(ID)
tamper-demo:   ; python -m provenance.audit tamper-demo
append-only:   ; python -m provenance.audit append-only
redteam:       ; python -m provenance.redteam --user sam
judge:         ; python -m provenance.evals --tier full --judge
repo-meta-check: ; python -m provenance.repo_meta_check
