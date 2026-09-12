Q ?= How many PTO days do full-time employees get?
USER ?= sam

up:            ; docker compose up -d db
up-local-llm:  ; docker compose --profile local-llm up -d db ollama
pull-models:   ; ollama pull nomic-embed-text && ollama pull llama3.1:8b
ingest:        ; python -m provenance.ingest
reset:         ; python -m provenance.ingest --reset
ask:           ; python -m provenance.chain "$(Q)" --user $(USER)
api:           ; uvicorn provenance.api:app --reload
down:          ; docker compose --profile local-llm --profile api down
eval:          ; python -m provenance.evals --tier retrieval
eval-full:     ; python -m provenance.evals --tier full
baseline:      ; python -m provenance.evals --tier full --write-baseline
