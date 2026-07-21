.DEFAULT_GOAL := help
.PHONY: help start up stop build rebuild logs ollama clean dirs

help:
	@echo "make start    Build if needed and run the app at http://localhost:3000"
	@echo "make up       Start without rebuilding"
	@echo "make stop     Stop the app (your data in ./data is untouched)"
	@echo "make build    Build the images"
	@echo "make rebuild  Build from scratch, ignoring the cache, then start"
	@echo "make logs     Follow the logs (ctrl-c to stop watching)"
	@echo "make ollama   Start with the optional local-model service"
	@echo "make clean    Remove containers and images. Never touches ./data"

# Both must exist before compose runs: Docker creates missing bind-mount paths
# as root, and a missing env_file is a hard error rather than a warning.
dirs:
	@mkdir -p data/db data/outputs

backend/.env:
	@cp backend/.env.example backend/.env
	@echo "Created backend/.env from the example — add your API keys, then re-run."

start: dirs backend/.env
	docker compose up --build -d
	@echo "Running at http://localhost:3000"

up: dirs backend/.env
	docker compose up -d
	@echo "Running at http://localhost:3000"

stop:
	docker compose down

build: backend/.env
	docker compose build

rebuild: dirs backend/.env
	docker compose build --no-cache
	docker compose up -d
	@echo "Running at http://localhost:3000"

logs:
	docker compose logs -f

ollama: dirs backend/.env
	docker compose --profile ollama up --build -d
	@echo "Running at http://localhost:3000 (ollama on :11434)"
	@echo "The service starts with NO models — the local tier stays inactive"
	@echo "until at least one is pulled into the volume:"
	@echo "  docker compose exec ollama ollama pull phi4-mini"

# --rmi local removes the images this project built. There is deliberately no
# -v anywhere in this file: -v deletes named volumes, which would wipe the
# downloaded Ollama models. Generated projects and the databases are bind
# mounts under ./data, which no docker command here can reach.
clean:
	docker compose down --rmi local
	@echo "Removed containers and images. ./data untouched."
