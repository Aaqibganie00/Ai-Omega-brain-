import type { AgentRegistry } from './agent-runtime'
import { createDefaultAgentRegistry } from './agent-runtime'
import { defaultExecutionLimits, type ControlledExecutor, RejectingExecutor } from './execution'

export type PlanStep = {
  id: string
  kind: 'plan' | 'execute' | 'build' | 'test' | 'verify'
  description: string
  requiresApproval: boolean
}

export class OrchestratorFoundation {
  constructor(
    readonly agents: AgentRegistry = createDefaultAgentRegistry(),
    readonly executor: ControlledExecutor = new RejectingExecutor(),
    readonly limits = defaultExecutionLimits,
  ) {}

  plan(objective: string): PlanStep[] {
    return [
      { id: 'plan', kind: 'plan', description: `Plan: ${objective}`, requiresApproval: false },
      { id: 'execute', kind: 'execute', description: 'Execute the approved plan.', requiresApproval: true },
      { id: 'build', kind: 'build', description: 'Build the work product.', requiresApproval: true },
      { id: 'test', kind: 'test', description: 'Run tests.', requiresApproval: true },
      { id: 'verify', kind: 'verify', description: 'Verify evidence without executing code.', requiresApproval: true },
    ]
  }
}
