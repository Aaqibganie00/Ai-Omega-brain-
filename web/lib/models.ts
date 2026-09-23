export type ModelMessage={role:'system'|'user'|'assistant';content:string}
export type ModelRequest={messages:ModelMessage[]; model?:string; temperature?:number; maxTokens?:number}
export type ModelResponse={provider:string; model:string; content:string; finishReason:'stop'|'error'; usage?:{inputTokens:number;outputTokens:number}}
export interface ModelProvider { readonly id:string; isConfigured():boolean; generate(request:ModelRequest):Promise<ModelResponse> }
export class MockModelProvider implements ModelProvider { readonly id='mock'; isConfigured(){return true} async generate(request:ModelRequest):Promise<ModelResponse>{return {provider:this.id,model:'mock-foundation',content:`Planning adapter placeholder for: ${request.messages.at(-1)?.content ?? ''}`,finishReason:'stop'}} }
