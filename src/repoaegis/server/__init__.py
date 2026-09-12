"""Skill line 2: the backend.

Hand-rolled state machine (plan -> approval -> solve -> verify -> deliver),
tool-call binding for approvals, persistence behind an adapter (SQLite in
development, PostgreSQL in production), an event bus fanned out to logs,
audit table and SSE, and the FastAPI surface the console talks to.
"""
