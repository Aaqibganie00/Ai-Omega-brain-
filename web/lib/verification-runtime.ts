import type { ExecutionResult } from './execution'
import type { Project, Task } from './domain'

export type VerificationStatus = 'pending' | 'passed' | 'failed' | 'incomplete'
export type VerificationResult = {
  status: VerificationStatus
  checks: Array<{ name: string; passed: boolean; detail: string }>
  execution?: ExecutionResult
}
export type VerificationRequest = { project: Project; task?: Task; build?: ExecutionResult; test?: ExecutionResult }

export interface BuildVerifier {
  verifyBuild(request: VerificationRequest): Promise<VerificationResult>
}

export interface TestVerifier {
  verifyTests(request: VerificationRequest): Promise<VerificationResult>
}

export class FoundationVerifier implements BuildVerifier, TestVerifier {
  private static toResult(execution: ExecutionResult | undefined, kind: 'build' | 'test'): VerificationResult {
    if (!execution) {
      return {
        status: 'incomplete',
        checks: [{ name: kind, passed: false, detail: `No ${kind} execution evidence was provided.` }],
        execution,
      }
    }

    const passed = execution.status === 'succeeded' && execution.exitCode === 0
    return {
      status: passed ? 'passed' : execution.status === 'succeeded' ? 'failed' : 'incomplete',
      checks: [
        {
          name: kind,
          passed,
          detail: passed ? `${kind} passed with exitCode 0.` : `Execution did not satisfy the evidence gate: status=${execution.status}, exitCode=${execution.exitCode ?? 'n/a'}.`,
        },
      ],
      execution,
    }
  }

  async verifyBuild(request: VerificationRequest): Promise<VerificationResult> {
    return FoundationVerifier.toResult(request.build, 'build')
  }

  async verifyTests(request: VerificationRequest): Promise<VerificationResult> {
    return FoundationVerifier.toResult(request.test, 'test')
  }
}
