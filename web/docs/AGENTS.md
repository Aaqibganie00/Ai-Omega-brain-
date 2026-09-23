# Agent architecture

Agents implement the `Agent` interface in `lib/agents.ts`: immutable identity metadata plus `run(AgentInput): Promise<AgentOutput>`. The registry is the only lookup mechanism used by orchestration. Agents return structured outcomes and cannot claim completion without downstream verification.

Initial roles are planner, researcher, coder, tester, debugger, and critic. They are registration-only in this milestone; no model or tool execution is implied. Add a real implementation by registering an adapter, not by changing the orchestrator's role logic.
