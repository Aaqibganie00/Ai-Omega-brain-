# Architecture

The web application is a bounded control plane. A request enters a validated API route, is associated with a server-side session, and reaches an ownership-aware project repository. The UI displays project state and structured events; it does not execute tools or expose provider credentials.

```text
Next.js route/UI
  -> auth boundary
  -> Zod input validation
  -> project repository
  -> orchestrator plan
       -> agent registry
       -> model provider (not configured by default)
       -> tool registry (risk/policy boundary)
  -> task state + activity events
  -> verification result
```

The current repository adapter is process-local and exists only for development. A PostgreSQL adapter should implement the same operations with `users`, `projects`, `tasks`, `project_memory`, `artifacts`, and `activity_events` tables. Ownership must be enforced in the query predicate, not only in UI state.

Execution tools must eventually be dispatched to an isolated sandbox with resource, network, filesystem, and time limits. The web process must never accept arbitrary shell commands from a public client.
