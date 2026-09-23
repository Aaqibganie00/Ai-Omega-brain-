import { z } from 'zod'

export const projectTypeSchema = z.enum(['website', 'web_app', 'mobile_prototype', 'game_2d', 'game_3d', 'backend', 'agent', 'automation', 'data'])
export const projectStatusSchema = z.enum(['draft', 'planning', 'active', 'paused', 'completed', 'failed'])
export const taskStatusSchema = z.enum(['pending', 'planning', 'running', 'waiting', 'testing', 'failed', 'completed'])
export const projectInputSchema = z.object({ name: z.string().trim().min(2).max(80), description: z.string().trim().min(10).max(2000), type: projectTypeSchema })
export const activityEventTypeSchema = z.enum(['project.created', 'task.created', 'task.started', 'task.completed', 'task.failed', 'agent.started', 'agent.completed', 'verification.started', 'verification.failed', 'verification.passed'])
export type ProjectType = z.infer<typeof projectTypeSchema>
export type ProjectStatus = z.infer<typeof projectStatusSchema>
export type TaskStatus = z.infer<typeof taskStatusSchema>
export type ActivityEventType = z.infer<typeof activityEventTypeSchema>
export type ProjectMemory = { context: string[]; decisions: string[]; requirements: string[]; completedWork: string[]; unresolvedIssues: string[] }
export type StructuredError = { code: string; message: string; category: 'validation' | 'authentication' | 'provider' | 'tool' | 'execution' | 'test' | 'internal'; retryable: boolean }
export type Task = { id: string; projectId: string; title: string; description: string; status: TaskStatus; dependencies: string[]; assignedAgent?: string; input?: unknown; output?: unknown; error?: StructuredError; retryCount: number; createdAt: string; updatedAt: string }
export type ActivityEvent = { id: string; projectId: string; type: ActivityEventType; actor: string; message: string; metadata: Record<string, unknown>; createdAt: string }
export type Artifact = { id: string; name: string; kind: 'file' | 'report' | 'log'; status: 'draft' | 'verified' | 'failed'; createdAt: string }
export type Project = { id: string; ownerId: string; name: string; description: string; type: ProjectType; status: ProjectStatus; createdAt: string; updatedAt: string; memory: ProjectMemory; tasks: Task[]; activity: ActivityEvent[]; artifacts: Artifact[] }
