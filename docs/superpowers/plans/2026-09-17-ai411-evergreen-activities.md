# AI 411 evergreen activities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off evergreen “things to do” store and `search_activities` tool without changing dated event search.

**Architecture:** New `activities.py` + JSON allowlist; MCP + inproc bridge + AI411 schema/prompt; voice image COPY. Events store untouched.

**Tech Stack:** Python 3.12, pytest, existing FastMCP wrappers, env flags.

## Global Constraints

- FMWS `demo-websites` only. No fake NAP/hours/dates. Flags default off. No k8s enablement in this pass. Do not scrape UF/city/Eventbrite. Do not modify `events.py` expiry or VG replace semantics.

---

### Task 1: Store, search, ingest (fail-closed)

**Files:**
- Create: `mcp-server/activities.py`
- Create: `mcp-server/activities_allowlist.json`
- Test: `mcp-server/tests/test_activities.py`

- [ ] Implement + tests as specified in the spec; commit `feat(ai411): add evergreen activities store`

### Task 2: MCP + voice wiring

**Files:**
- Modify: `mcp-server/server.py`, `mcp-server/tests/test_server.py`, `mcp-server/README.md`
- Modify: `voice-agent/mcp_bridge.py`, `voice-agent/ai411.py`, `voice-agent/tests/test_agent_mode.py`
- Modify: `voice-agent/Dockerfile`
- Modify: `docs/README.md`, `docs/PRODUCT_LOOP.md`

- [ ] Register `search_activities`; bridge inproc/http; AI411 tool + EVENT DISCOVERY note; COPY `activities.py` + allowlist + `ACTIVITIES_PATH`; commit `feat(ai411): wire evergreen search_activities`
