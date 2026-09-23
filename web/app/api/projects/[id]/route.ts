import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { getProject } from '@/lib/projects'
import { toErrorResponse } from '@/lib/errors'
export async function GET(_request:Request,{params}:{params:{id:string}}){try{const p=getProject(requireSession().userId,params.id); if(!p)return NextResponse.json({error:{code:'NOT_FOUND',message:'Project not found',category:'validation',retryable:false}},{status:404}); return NextResponse.json({project:p})}catch(e){return NextResponse.json(toErrorResponse(e),{status:401})}}
