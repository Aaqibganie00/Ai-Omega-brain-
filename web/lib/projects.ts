import { randomUUID } from 'crypto'
import type { Project, ProjectStatus, ProjectType, Task, ActivityEvent } from './domain'

type ProjectInput = { name: string; description: string; type: ProjectType }
const projects = new Map<string, Project>()

export function listProjects(ownerId: string): Project[] {
  return [...projects.values()].filter((project) => project.ownerId === ownerId).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
}

export function getProject(ownerId: string, id: string): Project | undefined {
  const project = projects.get(id)
  return project?.ownerId === ownerId ? project : undefined
}

export function createProject(ownerId: string, input: ProjectInput): Project {
  const now = new Date().toISOString()
  const project: Project = {
    id: randomUUID(), ownerId, ...input, status: 'draft', createdAt: now, updatedAt: now,
    memory: { context: [input.description], decisions: [], requirements: [], unresolvedIssues: [] },
    tasks: [], activity: [], artifacts: [],
  }
  projects.set(project.id, project)
  return project
}

export function addActivity(project: Project, event: Omit<ActivityEvent, 'id' | 'createdAt' | 'projectId'>): ActivityEvent {
  const item = { ...event, id: randomUUID(), projectId: project.id, createdAt: new Date().toISOString() }
  project.activity.unshift(item)
  project.updatedAt = item.createdAt
  return item
}

export function addTask(project: Project, title: string, assignedAgent?: string): Task {
  const now = new Date().toISOString()
  const task: Task = { id: randomUUID(), projectId: project.id, title, status: 'pending', dependencies: [], assignedAgent, retryCount: 0, createdAt: now, updatedAt: now }
  project.tasks.push(task)
  project.updatedAt = now
  return task
}

export function setProjectStatus(project: Project, status: ProjectStatus): void {
  project.status = status
  project.updatedAt = new Date().toISOString()
}
