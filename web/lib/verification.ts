import type { Project, Task } from './domain'
export type VerificationResult={status:'approved'|'rejected'|'incomplete'; checks:{name:string;passed:boolean;detail:string}[]}
export interface Verifier { verify(project:Project,tasks:Task[]):Promise<VerificationResult> }
export class FoundationVerifier implements Verifier { async verify(project:Project,tasks:Task[]):Promise<VerificationResult>{const checks=[{name:'tasks have outcomes',passed:tasks.every(t=>t.status==='completed'),detail:'Generated plans are not execution evidence.'},{name:'artifacts verified',passed:project.artifacts.every(a=>a.status==='verified'),detail:'No artifact is considered verified by default.'}]; return {status:checks.every(c=>c.passed)?'approved':'incomplete',checks}} }
