import { describe, expect, it } from 'vitest'
import { createDefaultAgentRegistry } from '../lib/agent-runtime'
import { defaultExecutionLimits, RejectingExecutor } from '../lib/execution'
import { FoundationVerifier } from '../lib/verification-runtime'
import { OrchestratorFoundation } from '../lib/orchestrator-foundation'
it('Phase 3 foundation fails closed without configured execution', async () => { const result = await new RejectingExecutor().execute({ projectId: 'project', kind: 'build', input: {}, limits: defaultExecutionLimits }); expect(result.status).toBe('rejected'); expect(result.error?.code).toBe('EXECUTOR_NOT_CONFIGURED') })
it('Phase 3 foundation exposes provider-neutral agents and a bounded plan', async () => { const registry = createDefaultAgentRegistry(); expect(registry.list()).toHaveLength(6); expect((await registry.get('planner')!.run({ project: {} as never, objective: 'test', context: {} })).status).toBe('blocked'); expect(new OrchestratorFoundation().plan('test').map((step) => step.kind)).toEqual(['plan', 'execute', 'build', 'test', 'verify']) })
it('verification requires execution evidence', async () => { const result = await new FoundationVerifier().verifyTests({ project: {} as never }); expect(result.status).toBe('incomplete') })
