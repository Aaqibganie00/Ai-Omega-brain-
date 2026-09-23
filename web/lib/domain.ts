import { z } from 'zod'
export const activityEventTypes = ['project.created', 'task.created', 'task.started', 'task.completed', 'task.failed', 'agent.started', 'agent.completed', 'agent.failed', 'agent.blocked', 'verification.started', 'verification.failed', 'verification.passed'] as const
export type ActivityEventType = typeof activityEventTypes[number]
export const activityEventTypeSchema = z.enum(activityEventTypes)
export const agentEventTypes = ['agent.started', 'agent.completed', 'agent.failed', 'agent.blocked'] as const
export type ProjectType = 'website' | 'web_app' | 'mobile_prototype' | 'game_2d' | 'game_3d' | 'backend' | 'automation' | 'agent' | 'data'
export type ProjectStatus = 'draft' | 'planning' | 'active' | 'paused' | 'completed' | 'failed'
export type TaskStatus = 'pending' | 'planning' | 'running' | 'waiting' | 'testing' | 'failed' | 'completed'
export type ProjectMemory = { context: string[]; decisions: string[]; requirements: string[]; completedWork: string[]; unresolvedIssues: string[] }
export type StructuredError = { code: string; message: string; category: 'validation' | 'authentication' | 'provider' | 'tool' | 'execution' | 'test' | 'internal'; retryable: boolean }
export type Task = { id: string; projectId: string; title: string; description: string; status: TaskStatus; dependencies: string[]; assignedAgent?: string; input?: unknown; output?: unknown; error?: StructuredError; retryCount: number; createdAt: string; updatedAt: string }
export type ActivityEvent = { id: string; projectId: string; type: ActivityEventType; actor: string; message: string; metadata: Record<string, unknown>; createdAt: string }
export type Artifact = { id: string; name: string; kind: 'file' | 'report' | 'log'; status: 'draft' | 'verified' | 'failed'; createdAt: string }
export type Project = { id: string; ownerId: string; name: string; description: string; type: ProjectType; status: ProjectStatus; createdAt: string; updatedAt: string; memory: ProjectMemory; tasks: Task[]; activity: ActivityEvent[]; artifacts: Artifact[] }
