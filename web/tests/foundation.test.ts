import { describe, expect, it } from 'vitest'
import { AgentRegistry, createDefaultAgentRegistry } from '../lib/agents'
import { FoundationVerifier } from '../lib/verification'
import { createProject } from '../lib/projects'
import { Orchestrator } from '../lib/orchestrator'

describe('BLINDBRAIN foundation',()=>{it('registers the initial agent roles',()=>expect(createDefaultAgentRegistry().list()).toHaveLength(6));it('creates an owned project with structured memory',()=>expect(createProject('u',{name:'Test project',description:'A sufficiently detailed project description',type:'web_app'}).memory.context).toHaveLength(1));it('plans without claiming execution',async()=>{const p=createProject('u',{name:'Plan project',description:'A sufficiently detailed project description',type:'agent'});const plan=await new Orchestrator().plan(p,'create an agent');expect(plan.honestLimitations.length).toBeGreaterThan(0);expect(p.tasks).toHaveLength(3)});it('does not approve unverified output',async()=>{const p=createProject('u',{name:'Verify project',description:'A sufficiently detailed project description',type:'website'});expect((await new FoundationVerifier().verify(p,p.tasks)).status).toBe('incomplete')})})
