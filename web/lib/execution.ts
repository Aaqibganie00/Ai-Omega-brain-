import type { AgentRunStatus, ExecutionStatus } from '@prisma/client'
import type { StructuredError } from './domain'

export type ExecutionLimits = { timeoutMs: number; maxOutputBytes: number; allowNetwork: boolean }
export type ExecutionRequest = { projectId: string; taskId?: string; agentRunId?: string; kind: 'file_read' | 'file_write' | 'list' | 'inspect' | 'test' | 'build' | 'git'; input: Record<string, unknown>; limits: ExecutionLimits }
export type ExecutionResult = { status: ExecutionStatus; stdout: string; stderr: string; exitCode?: number; durationMs: number; error?: StructuredError; metadata: Record<string, unknown> }
export interface ControlledExecutor { execute(request: ExecutionRequest): Promise<ExecutionResult> }
export class RejectingExecutor implements ControlledExecutor { async execute(request: ExecutionRequest): Promise<ExecutionResult> { return { status: 'rejected', stdout: '', stderr: '', durationMs: 0, error: { code: 'EXECUTOR_NOT_CONFIGURED', message: `Controlled executor for ${request.kind} is not configured.`, category: 'execution', retryable: false }, metadata: { projectId: request.projectId, taskId: request.taskId } } } }
export const defaultExecutionLimits: ExecutionLimits = { timeoutMs: 10_000, maxOutputBytes: 64_000, allowNetwork: false }
