import { Prisma } from '@prisma/client'
import type { AgentRunStatus, ExecutionStatus } from '@prisma/client'
import type { ActivityEventType } from './domain'
import { AppError } from './errors'
import { authorizeAgentRun, authorizeProject, authorizeTask, asJson } from './authorization'
import { assertAgentTransition } from './agent-runtime'
import { assertExecutionTransition } from './execution'
import { prisma } from './prisma'

export type AgentRunInput = {
  projectId: string
  taskId?: string
  agentId: string
  role: string
  input?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export async function createAgentRun(ownerId: string, input: AgentRunInput) {
  await authorizeProject(ownerId, input.projectId)
  if (input.taskId) {
    await authorizeTask(ownerId, input.projectId, input.taskId)
  }

  return prisma.agentRun.create({
    data: {
      projectId: input.projectId,
      taskId: input.taskId,
      agentId: input.agentId,
      role: input.role,
      status: 'queued',
      input: asJson(input.input),
      metadata: asJson(input.metadata),
    },
  })
}

export async function updateAgentRun(
  ownerId: string,
  projectId: string,
  id: string,
  status: AgentRunStatus,
  output?: Record<string, unknown>,
  error?: Record<string, unknown>,
) {
  const run = await authorizeAgentRun(ownerId, projectId, id)
  assertAgentTransition(run.status, status)

  return prisma.agentRun.update({
    where: { id },
    data: {
      status,
      output: asJson(output ?? run.output ?? undefined),
      error: asJson(error ?? run.error ?? undefined),
      startedAt: status === 'running' && run.status === 'queued' ? new Date() : run.startedAt,
      completedAt:
        status === 'completed' || status === 'failed' || status === 'blocked' ? new Date() : run.completedAt,
    },
  })
}

export type ExecutionRecordInput = {
  projectId: string
  taskId?: string
  agentRunId?: string
  kind: string
  result: {
    status: ExecutionStatus
    stdout: string
    stderr: string
    exitCode?: number
    durationMs?: number
    error?: Record<string, unknown>
    metadata?: Record<string, unknown>
  }
}

async function authorizeExecution(ownerId: string, input: ExecutionRecordInput) {
  await authorizeProject(ownerId, input.projectId)

  if (input.taskId) {
    await authorizeTask(ownerId, input.projectId, input.taskId)
  }

  if (input.agentRunId) {
    const agentRun = await authorizeAgentRun(ownerId, input.projectId, input.agentRunId)
    if (input.taskId && agentRun.taskId && agentRun.taskId !== input.taskId) {
      throw new AppError({
        code: 'INVALID_INPUT',
        message: 'Execution taskId and agentRun.taskId must match for the same project.',
        category: 'validation',
        retryable: false,
      })
    }
    if (input.taskId && agentRun.taskId === null) {
      throw new AppError({
        code: 'INVALID_INPUT',
        message: 'Execution agentRun is missing the required taskId.',
        category: 'validation',
        retryable: false,
      })
    }
  }
}

export async function createExecutionRecord(ownerId: string, input: ExecutionRecordInput) {
  await authorizeExecution(ownerId, input)

  return prisma.executionRecord.create({
    data: {
      projectId: input.projectId,
      taskId: input.taskId,
      agentRunId: input.agentRunId,
      kind: input.kind,
      status: input.result.status,
      stdout: input.result.stdout,
      stderr: input.result.stderr,
      exitCode: input.result.exitCode,
      durationMs: input.result.durationMs,
      error: asJson(input.result.error),
      metadata: asJson(input.result.metadata),
      startedAt: new Date(),
      completedAt: new Date(),
    },
  })
}

export async function updateExecutionRecord(
  ownerId: string,
  projectId: string,
  id: string,
  status: ExecutionStatus,
  patch: {
    stdout?: string
    stderr?: string
    exitCode?: number
    durationMs?: number
    error?: Record<string, unknown>
    metadata?: Record<string, unknown>
  },
) {
  await authorizeProject(ownerId, projectId)

  const record = await prisma.executionRecord.findFirst({
    where: { id, projectId },
    select: { id: true, projectId: true, status: true },
  })

  if (!record) {
    throw new AppError({
      code: 'FORBIDDEN',
      message: 'Access denied.',
      category: 'authentication',
      retryable: false,
    })
  }

  assertExecutionTransition(record.status, status)

  return prisma.executionRecord.update({
    where: { id },
    data: {
      status,
      stdout: patch.stdout ?? undefined,
      stderr: patch.stderr ?? undefined,
      exitCode: patch.exitCode ?? undefined,
      durationMs: patch.durationMs ?? undefined,
      error: asJson(patch.error ?? undefined),
      metadata: asJson(patch.metadata ?? undefined),
      completedAt: new Date(),
    },
  })
}
