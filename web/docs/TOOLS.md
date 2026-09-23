# Tools

Tools are registered as `ToolDefinition` values with an ID, description, risk classification, and execution function. The registry is the future policy boundary. `project_context` is a safe stub; `sandbox_execution` deliberately fails closed until an isolated executor exists.

Future tool adapters must validate input, enforce project ownership, apply permissions, limit resources, emit structured events, and record success/failure. Never place terminal or filesystem execution in a browser component or unrestricted API handler.
