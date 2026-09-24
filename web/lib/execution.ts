import type { ExecutionStatus } from '@prisma/client'
import type { StructuredError } from './domain'
import { AppError } from './errors'

export type ExecutionLimits = { timeoutMs: number; maxOutputBytes: number; allowNetwork: boolean }

export const defaultExecutionLimits: ExecutionLimits = {
  timeoutMs: 10_000,
  maxOutputBytes: 64_000,
  allowNetwork: false,
}

export function validateExecutionLimits(limits: ExecutionLimits): ExecutionLimits {
  if (!Number.isInteger(limits.timeoutMs) || limits.timeoutMs < 100 || limits.timeoutMs > 120_000) {
    throw new AppError({
      code: 'INVALID_EXECUTION_LIMITS',
      message: 'timeoutMs must be an integer between 100 and 120000.',
      category: 'validation',
      retryable: false,
    })
  }

  if (!Number.isInteger(limits.maxOutputBytes) || limits.maxOutputBytes < 1024 || limits.maxOutputBytes > 1_000_000) {
    throw new AppError({
      code: 'INVALID_EXECUTION_LIMITS',
      message: 'maxOutputBytes must be an integer between 1024 and 1000000.',
      category: 'validation',
      retryable: false,
    })
  }

  if (typeof limits.allowNetwork !== 'boolean') {
    throw new AppError({
      code: 'INVALID_EXECUTION_LIMITS',
      message: 'allowNetwork must be a boolean.',
      category: 'validation',
      retryable: false,
    })
  }

  return { ...limits }
}

export type ExecutionKind = 'file_read' | 'file_write' | 'list' | 'inspect' | 'test' | 'build' | 'git'

export type ExecutionRequest = {
  projectId: string
  taskId?: string
  agentRunId?: string
  kind: ExecutionKind
  input: Record<string, unknown>
  limits: ExecutionLimits
}

export type ExecutionResult = {
  status: ExecutionStatus
  stdout: string
  stderr: string
  exitCode?: number
  durationMs: number
  error?: StructuredError
  metadata: Record<string, unknown>
}

export interface ControlledExecutor {
  execute(request: ExecutionRequest): Promise<ExecutionResult>
}

export class RejectingExecutor implements ControlledExecutor {
  async execute(request: ExecutionRequest): Promise<ExecutionResult> {
    const limits = validateExecutionLimits(request.limits)
    if (limits.allowNetwork) {
      return {
        status: 'rejected',
        stdout: '',
        stderr: 'Network execution is disabled by policy.',
        durationMs: 0,
        metadata: { kind: request.kind, projectId: request.projectId },
        error: {
          code: 'NETWORK_DISABLED',
          message: 'Network execution is disabled by default and must not be explicitly enabled.',
          category: 'execution',
          retryable: false,
        },
      }
    }

    return {
      status: 'rejected',
      stdout: '',
      stderr: 'Execution is denied in this foundation runtime.',
      durationMs: 0,
      metadata: { kind: request.kind, projectId: request.projectId },
      error: {
        code: 'EXECUTION_REJECTED',
        message: 'Execution is intentionally rejected in this fail-closed foundation.',
        category: 'execution',
        retryable: false,
      },
    }
  }
}

export const executionTransitions: Record<ExecutionStatus, readonly ExecutionStatus[]> = {
  queued: ['queued', 'running', 'rejected'],
  running: ['running', 'succeeded', 'failed', 'timed_out', 'rejected'],
  succeeded: ['succeeded'],
  failed: ['failed'],
  timed_out: ['timed_out'],
  rejected: ['rejected'],
}

export function assertExecutionTransition(from: ExecutionStatus, to: ExecutionStatus): void {
  const allowed = executionTransitions[from]
  if (!allowed || !allowed.includes(to)) {
    throw new AppError({
      code: 'INVALID_EXECUTION_TRANSITION',
      message: `Invalid execution transition from ${from} to ${to}.`,
      category: 'validation',
      retryable: false,
    })
  }
}
