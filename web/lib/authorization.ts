import { Prisma } from '@prisma/client'
import { AppError } from './errors'
import { prisma } from './prisma'

function denyAccess(): AppError {
  return new AppError({
    code: 'FORBIDDEN',
    message: 'Access denied.',
    category: 'authentication',
    retryable: false,
  })
}

export async function authorizeProject(ownerId: string, projectId: string) {
  const project = await prisma.project.findFirst({
    where: { id: projectId, ownerId },
    select: { id: true, ownerId: true },
  })

  if (!project) {
    throw denyAccess()
  }

  return project
}

export async function authorizeTask(ownerId: string, projectId: string, taskId: string) {
  await authorizeProject(ownerId, projectId)

  const task = await prisma.task.findFirst({
    where: { id: taskId, projectId },
    select: { id: true, projectId: true },
  })

  if (!task) {
    throw denyAccess()
  }

  return task
}

export async function authorizeAgentRun(ownerId: string, projectId: string, agentRunId: string) {
  await authorizeProject(ownerId, projectId)

  const agentRun = await prisma.agentRun.findFirst({
    where: { id: agentRunId, projectId },
    select: { id: true, projectId: true, taskId: true, status: true },
  })

  if (!agentRun) {
    throw denyAccess()
  }

  return agentRun
}

export function asJson(value: unknown): Prisma.InputJsonValue | undefined {
  return value === undefined ? undefined : (value as Prisma.InputJsonValue)
}
