import type { Project } from './domain'
export type AgentInput = { project:Project; taskId:string; objective:string; context:Record<string,unknown> }
export type AgentOutput = { status:'completed'|'blocked'|'failed'; summary:string; artifacts:string[]; issues:string[] }
export interface Agent { readonly id:string; readonly name:string; readonly role:string; run(input:AgentInput):Promise<AgentOutput> }
export class AgentRegistry { private readonly agents=new Map<string,Agent>(); register(agent:Agent){this.agents.set(agent.id,agent);return this} get(id:string){return this.agents.get(id)} list(){return [...this.agents.values()]} }
class FoundationAgent implements Agent { constructor(public readonly id:string,public readonly name:string,public readonly role:string){} async run():Promise<AgentOutput>{return {status:'blocked',summary:'This foundation agent is registered but has no execution adapter yet.',artifacts:[],issues:['No model/tool execution adapter configured']}} }
export function createDefaultAgentRegistry():AgentRegistry { const r=new AgentRegistry(); return ['planner','researcher','coder','tester','debugger','critic'].reduce((x,id)=>x.register(new FoundationAgent(id,`${id[0].toUpperCase()+id.slice(1)} Agent`,`${id} responsibilities`)),r) }
