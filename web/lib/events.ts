import type { ActivityEvent, Project } from './domain'
export const eventTypes = ['PROJECT_CREATED','PLAN_CREATED','AGENT_STARTED','AGENT_COMPLETED','TOOL_EXECUTION','TEST_STARTED','TEST_FAILED','BUG_DETECTED','FIX_APPLIED','VERIFICATION_COMPLETED'] as const
export function emit(project:Project,type:string,message:string,actor='orchestrator',metadata:Record<string,unknown>={}): ActivityEvent { return {id:crypto.randomUUID(),projectId:project.id,type,actor,message,metadata,createdAt:new Date().toISOString()} }
