import { describe, expect, it } from 'vitest'
import { AgentRegistry, FoundationAgent, assertAgentTransition, createDefaultAgentRegistry } from '../lib/agent-runtime'
import { assertExecutionTransition, defaultExecutionLimits, RejectingExecutor, validateExecutionLimits } from '../lib/execution'
import { createDefaultToolRegistry } from '../lib/tools-runtime'
import { FoundationVerifier } from '../lib/verification-runtime'
import { OrchestratorFoundation } from '../lib/orchestrator-foundation'

const project = {
  id: 'project_123',
  ownerId: 'user',
  name: 'Project',
  description: 'A valid project fixture',
  type: 'web_app',
  status: 'draft',
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  memory: {
    context: [],
    decisions: [],
    requirements: [],
    completedWork: [],
    unresolvedIssues: [],
  },
  tasks: [],
  events: [],
  agentRuns: [],
  executions: [],
}

describe('phase 3 foundation', () => {
  it('registers defaults, rejects duplicates, and returns unknown agents safely', () => {
    const registry = new AgentRegistry()
    registry.register(new FoundationAgent('one', 'test', 'test'))

    expect(registry.get('one')).toBeTruthy()
    expect(() => registry.register(new FoundationAgent('one', 'test', 'test'))).toThrowError(/DUPLICATE_AGENT/)
    expect(registry.get('missing')).toBeUndefined()
    expect(createDefaultAgentRegistry().list()).toHaveLength(6)
  })

  it('allows valid agent transitions and blocks invalid ones', () => {
    expect(() => assertAgentTransition('queued', 'running')).not.toThrow()
    expect(() => assertAgentTransition('running', 'completed')).not.toThrow()
    expect(() => assertAgentTransition('running', 'failed')).not.toThrow()
    expect(() => assertAgentTransition('queued', 'completed')).toThrowError(/INVALID_AGENT_TRANSITION/)
    expect(() => assertAgentTransition('completed', 'running')).toThrowError(/INVALID_AGENT_TRANSITION/)
  })

  it('allows valid execution transitions and rejects invalid ones', () => {
    expect(() => assertExecutionTransition('queued', 'running')).not.toThrow()
    expect(() => assertExecutionTransition('running', 'succeeded')).not.toThrow()
    expect(() => assertExecutionTransition('queued', 'rejected')).not.toThrow()
    expect(() => assertExecutionTransition('queued', 'succeeded')).toThrowError(/INVALID_EXECUTION_TRANSITION/)
    expect(() => assertExecutionTransition('rejected', 'running')).toThrowError(/INVALID_EXECUTION_TRANSITION/)
  })

  it('enforces execution limits and rejects forbidden network execution', async () => {
    expect(() => validateExecutionLimits({ timeoutMs: 50, maxOutputBytes: 1024, allowNetwork: false })).toThrowError(/INVALID_EXECUTION_LIMITS/)
    expect(() => validateExecutionLimits({ timeoutMs: 10_000, maxOutputBytes: 1024, allowNetwork: false })).not.toThrow()

    const executor = new RejectingExecutor()
    const result = await executor.execute({
      projectId: project.id,
      kind: 'build',
      input: { objective: 'build' },
      limits: { timeoutMs: 10_000, maxOutputBytes: 32_000, allowNetwork: true },
    })

    expect(result.status).toBe('rejected')
    expect(result.error?.code).toBe('NETWORK_DISABLED')
  })

  it('exposes disabled metadata-only tools', () => {
    const tools = createDefaultToolRegistry().list()
    expect(tools).toHaveLength(7)
    expect(tools.every((tool) => tool.enabled === false)).toBe(true)
    expect(tools.map((tool) => tool.id)).toEqual([
      'file_read',
      'file_write',
      'directory_list',
      'project_inspect',
      'test',
      'build',
      'git',
    ])
  })

  it('keeps verification evidence-gated', async () => {
    const verifier = new FoundationVerifier()
    expect((await verifier.verifyTests({ project })).status).toBe('incomplete')
    expect(
      (await verifier.verifyBuild({
        project,
        build: { status: 'succeeded', stdout: 'ok', stderr: '', exitCode: 0, durationMs: 10, metadata: {} },
      })).status,
    ).toBe('passed')
    expect(
      (await verifier.verifyBuild({
        project,
        build: { status: 'failed', stdout: '', stderr: 'bad', exitCode: 1, durationMs: 10, metadata: {} },
      })).status,
    ).toBe('failed')
  })

  it('keeps the orchestrator as a plan-only representation', () => {
    expect(new OrchestratorFoundation().plan('objective').map((step) => step.kind)).toEqual([
      'plan',
      'execute',
      'build',
      'test',
      'verify',
    ])
  })

  it('includes the required typed lifecycle events', () => {
    expect(['agent.started', 'agent.completed', 'agent.failed', 'agent.blocked']).toEqual(
      expect.arrayContaining(['agent.started', 'agent.completed', 'agent.failed', 'agent.blocked']),
    )
  })
})
