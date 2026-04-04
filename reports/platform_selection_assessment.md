# Platform Selection Assessment

**Date:** 2026-04-04
**Decision:** HTMX + FastAPI + Jinja2 + Cytoscape.js

---

## Core UX Requirements (from specs/user_experience_spec.md)

1. Task graph visualization — DAG with node statuses, dependencies, cross-order fulfillment links
2. Ambiguity resolution queue — accept/reject/correct with atomic actions
3. Provisional node review — accept/reject with evidence display
4. Entity metadata editing — aliases, CRM, classifications
5. Staff assignment management — add/remove, multiple per task/node
6. Task overview with filtering — blockers, delays, entity, staff
7. Config editing — escalation profile, availability windows, alert preferences
8. Mobile-friendly — Ashish is on phone between client calls

## Graph Visualization: Cytoscape.js

**Why Cytoscape.js over alternatives:**

| Requirement | Cytoscape.js | React Flow | D3 | Mermaid |
|---|---|---|---|---|
| Hierarchical DAG layout (dagre) | Built-in | Built-in | Manual | Built-in but static |
| Node styling by status | Extensive CSS-like selectors | React components | Full control | Limited |
| Cross-graph edges (fulfillment links) | Native compound/multi-graph | Possible but awkward | Manual | No |
| Click-to-drill-down | Event system | Event system | Manual | No interactivity |
| Mobile touch support | Built-in | Built-in | Manual | N/A |
| Framework dependency | None (vanilla JS) | React only | None | None |
| Automatic layout | dagre, cose, breadthfirst, elk | dagre, elk | Manual | Auto but static |

**Decision:** Cytoscape.js — framework-agnostic, best layout algorithms for DAGs, handles M:N cross-order edges natively, works on mobile.

## Application Framework: HTMX + FastAPI + Jinja2

**Why this stack:**

- FastAPI already deployed on DigitalOcean droplet for ingestion endpoint
- HTMX gives partial page updates without full SPA complexity — critical for accept/reject flows
- Jinja2 server-rendered templates — no JS build step, no node_modules
- Cytoscape.js loaded as standalone script — no bundler needed
- SQLite reads for dashboard views — direct DB access, no API layer needed
- Mobile-friendly with minimal CSS (Pico CSS or similar classless framework)

**Why not alternatives:**

| Alternative | Rejected because |
|---|---|
| Streamlit | Full-page rerun on every interaction — breaks accept/reject UX. Poor layout control. |
| React + React Flow | Heavier stack, JS build pipeline, overkill for this use case |
| Gradio | Chat-centric, not suited for dashboard-style UI |
| Lovable/Bolt.new | External dependency, harder to integrate with existing Python backend |

## Architecture

```
Browser (HTMX + Cytoscape.js)
  ↕ HTML fragments (HTMX partial updates)
FastAPI (Jinja2 templates)
  ↕ Direct SQLite reads
mantri.db (shared with agents)
```

**Key routes:**

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Task overview — filterable table |
| `/task/{id}` | GET | Task detail — Cytoscape graph + items + messages |
| `/ambiguity` | GET | Pending ambiguity queue |
| `/ambiguity/{id}/resolve` | POST | Accept/reject/correct (HTMX swap) |
| `/provisional` | GET | Provisional nodes awaiting review |
| `/provisional/{id}/accept` | POST | Accept provisional (HTMX swap) |
| `/provisional/{id}/reject` | POST | Reject provisional (HTMX swap) |
| `/entity/{id}` | GET | Entity detail — metadata, CRM, aliases |
| `/entity/{id}/edit` | POST | Update entity metadata (HTMX swap) |
| `/config` | GET | Escalation profile, availability, alert prefs |
| `/config/update` | POST | Save config changes |

## Graph Visualization Design

**Task graph (per order):**
- Nodes: template nodes (order_confirmation, dispatched, payment, etc.)
- Node color/shape by status: pending (grey), active (blue), completed (green), blocked (red), provisional (yellow dashed)
- Edges: dependency arrows (requires_all, auto_trigger)
- Items attached as child nodes or tooltips
- Click node → expand detail panel (messages, evidence, items)

**Cross-order fulfillment view:**
- Client order nodes on left, supplier order nodes on right
- Fulfillment link edges between matched items
- Edge thickness/color by match_confidence
- Low-confidence links highlighted for review

**Layout:** dagre (top-down hierarchical) for single task, cose-bilkent for multi-order fulfillment view.

## Implementation Plan

1. Add FastAPI dashboard routes to existing `src/ingestion/ingest.py` or new `src/dashboard/app.py`
2. Jinja2 templates in `src/dashboard/templates/`
3. Cytoscape.js graph component — server sends node/edge JSON, client renders
4. HTMX for all form interactions (accept/reject/edit)
5. Pico CSS or similar for mobile-friendly base styling
6. Deploy alongside existing services on DigitalOcean droplet
