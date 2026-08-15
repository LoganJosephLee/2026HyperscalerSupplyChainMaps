.PHONY: help setup refresh fetch extract verify build review serve test neo4j-up neo4j-load clean

UV ?= uv
EXTRACTIONS ?= data/extractions.json

help:
	@echo "make setup     install dependencies"
	@echo "make refresh   rebuild the dataset end to end (fetch -> extract -> verify -> build)"
	@echo "make review    build the entity resolution queue for a human"
	@echo "make serve     serve the site locally at http://localhost:8000"
	@echo "make test      run the test suite"
	@echo "make neo4j-up  start Neo4j in Docker for Cypher work"
	@echo ""
	@echo "Set HSCM_EDGAR_CONTACT to your email before fetching (SEC fair-access policy)."
	@echo "Set HSCM_EXTRACTOR=anthropic and ANTHROPIC_API_KEY to use the real extractor."

setup:
	$(UV) sync --extra dev

# The one documented command that rebuilds the dataset end to end.
# Deliberately manual: no scheduled workflow, so nothing changes under you.
refresh: fetch extract verify build
	@echo ""
	@echo "Dataset rebuilt. Every page carries the newest filing date in the data."

fetch:
	$(UV) run hscm fetch

extract:
	$(UV) run hscm extract --out $(EXTRACTIONS)

# Fails the build if any claimed sentence is not in the filing it cites.
verify:
	$(UV) run hscm verify $(EXTRACTIONS) --out data/verification-report.json

build:
	$(UV) run hscm build --extractions $(EXTRACTIONS)

review:
	$(UV) run hscm review build --extractions $(EXTRACTIONS)
	@echo "Edit data/review/entity_review_queue.csv, then: $(UV) run hscm review apply"

serve:
	@echo "http://localhost:8000 — Ctrl-C to stop"
	@cd site && python3 -m http.server 8000

test:
	$(UV) run pytest -q

neo4j-up:
	docker run -d --name hscm-neo4j -p 7474:7474 -p 7687:7687 \
		-e NEO4J_AUTH=neo4j/neo4jneo4j neo4j:5-community
	@echo "Browser: http://localhost:7474 (neo4j / neo4jneo4j)"

neo4j-load:
	$(UV) run hscm neo4j-load --extractions $(EXTRACTIONS)

clean:
	rm -rf data/cache data/extractions.json data/verification-report.json
