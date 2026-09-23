import type { ExecutionResult } from './execution'
import type { Project, Task } from './domain'
export type VerificationStatus = 'pending' | 'passed' | 'failed' | 'incomplete'
export type VerificationResult = { status: VerificationStatus; checks: Array<{ name: string; passed: boolean; detail: string }>; execution?: ExecutionResult }
export type VerificationRequest = { project: Project; task?: Task; build?: ExecutionResult; test?: ExecutionResult }
export interface BuildVerifier { verifyBuild(request: VerificationRequest): Promise<VerificationResult> }
export interface TestVerifier { verifyTests(request: VerificationRequest): Promise<VerificationResult> }
export class FoundationVerifier implements BuildVerifier, TestVerifier { async verifyBuild(request: VerificationRequest): Promise<VerificationResult> { return this.result(request.build, 'build') } async verifyTests(request: VerificationRequest): Promise<VerificationResult> { return this.result(request.test, 'test') } private result(execution: ExecutionResult | undefined, name: string): VerificationResult { if (!execution) return { status: 'incomplete', checks: [{ name, passed: false, detail: 'No execution evidence was supplied.' }] }; const passed = execution.status === 'succeeded' && execution.exitCode === 0; return { status: passed ? 'passed' : 'failed', checks: [{ name, passed, detail: passed ? 'Execution completed successfully.' : 'Execution did not provide a passing result.' }], execution } } }
