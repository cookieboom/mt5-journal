---
trigger: always_on
description: ALWAYS consult the graphify knowledge graph before responding to ANY question about this project.
---

## graphify — mandatory context loading

This project has a graphify knowledge graph at `graphify-out/`.

**MANDATORY: Before answering ANY question about this codebase, its architecture, files, or functionality, you MUST first consult the knowledge graph for context.**

### Pre-response workflow (execute EVERY time):

1. **Read the graph report first**: At the start of every new conversation or when context is needed, read `graphify-out/GRAPH_REPORT.md` to understand the project's overall architecture, god nodes, communities, and relationships.

2. **Query the graph for specific questions**: When `graphify-out/graph.json` exists, run `graphify query "<question>"` to get a scoped subgraph relevant to the user's question. This is faster and more accurate than grep or raw file reading.

3. **Use specialized commands for deeper exploration**:
   - `graphify path "<A>" "<B>"` — trace relationships between two concepts
   - `graphify explain "<concept>"` — get a focused explanation of a specific node
   - `graphify god-nodes` — identify the most connected architectural hubs

4. **Prefer graph over raw file reading**: The graph provides pre-analyzed relationships, community detection, and edge confidence (EXTRACTED/INFERRED). Always prefer this structured context over reading files directly.

### Rules:
- For codebase or architecture questions, when `graphify-out/graph.json` exists, first run `graphify query "<question>"` (CLI) or `query_graph` (MCP). Use `graphify path "<A>" "<B>"` / `shortest_path` for relationships and `graphify explain "<concept>"` / `get_node` for focused concepts. These return a scoped subgraph, usually much smaller than `GRAPH_REPORT.md` or raw grep output.
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- Read graphify-out/GRAPH_REPORT.md for broad architecture review or when query/path/explain do not surface enough context
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
- **Do NOT skip the graph consultation step** — it provides critical context that prevents hallucination and ensures accurate responses about the codebase
