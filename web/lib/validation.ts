import { z } from 'zod'
export const signUpSchema = z.object({ email: z.string().email().max(320), password: z.string().min(12).max(128) })
export const taskInputSchema = z.object({ title: z.string().trim().min(2).max(160), description: z.string().trim().max(2000).default(''), assignedAgent: z.string().trim().max(80).optional(), dependencies: z.array(z.string()).max(50).default([]) })
export const memorySchema = z.object({ requirements: z.array(z.string().max(2000)).max(100).optional(), decisions: z.array(z.string().max(2000)).max(100).optional(), context: z.array(z.string().max(2000)).max(100).optional(), completedWork: z.array(z.string().max(2000)).max(100).optional(), unresolvedIssues: z.array(z.string().max(2000)).max(100).optional() })
