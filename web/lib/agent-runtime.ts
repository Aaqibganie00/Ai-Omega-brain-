import { AppError } from './errors'
import type { AgentRunStatus } from '@prisma/client'
import type { Project, Task } from './domain'

export type AgentInput = { project: Project; task?: Task; objective: string; context: Record<string, unknown> }
export type AgentOutput = { summary: string; artifacts: string[]; issues: string[]; metadata?: Record<string, unknown> }
export type AgentExecutionResult = { status: AgentRunStatus; output?: AgentOutput; error?: { code: string; message: string; retryable: boolean }; metadata: Record<string, unknown> }
export interface Agent { readonly id: string; readonly role: string; readonly instructions: string; run(input: AgentInput): Promise<AgentExecutionResult> }
export class AgentRegistry { private readonly agents = new Map<string, Agent>(); register(agent: Agent): this { if (this.agents.has(agent.id)) throw new AppError({ code: 'DUPLICATE_AGENT', message: `Agent already registered: ${agent.id}`, category: 'validation', retryable: false }); this.agents.set(agent.id, agent); return this } get(id: string) { return this.agents.get(id) } list() { return [...this.agents.values()] } }
export class FoundationAgent implements Agent { constructor(public readonly id: string, public readonly role: string, public readonly instructions: string) {} async run(): Promise<AgentExecutionResult> { return { status: 'blocked', error: { code: 'AGENT_ADAPTER_NOT_CONFIGURED', message: 'No model or execution adapter is configured.', retryable: false }, metadata: { foundationOnly: true } } } }
export function createDefaultAgentRegistry(): AgentRegistry { const registry = new AgentRegistry(); for (const role of ['planner', 'researcher', 'coder', 'tester', 'debugger', 'critic']) registry.register(new FoundationAgent(role, role, `${role} responsibilities are defined but execution is disabled.`)); return registry }
export const agentTransitions: Record<AgentRunStatus, readonly AgentRunStatus[]> = { queued: ['queued', 'running'], running: ['running', 'completed', 'failed', 'blocked'], completed: ['completed'], failed: ['failed'], blocked: ['blocked'] }
export function assertAgentTransition(from: AgentRunStatus, to: AgentRunStatus): void { if (!agentTransitions[from].includes(to)) throw new AppError({ code: 'INVALID_AGENT_TRANSITION', message: `Cannot move agent run from ${from} to ${to}.`, category: 'validation', retryable: false }) }
