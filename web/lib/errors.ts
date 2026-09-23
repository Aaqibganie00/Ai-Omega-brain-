import type { StructuredError } from './domain'
export class AppError extends Error { constructor(public readonly details:StructuredError){super(details.message); this.name='AppError'} }
export function validationError(message:string): AppError { return new AppError({code:'INVALID_INPUT',message,category:'validation',retryable:false}) }
export function toErrorResponse(error:unknown): { error:StructuredError } { if(error instanceof AppError)return {error:error.details}; return {error:{code:'INTERNAL_ERROR',message:'An unexpected server error occurred.',category:'internal',retryable:false}} }
