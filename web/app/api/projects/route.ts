import { NextResponse } from 'next/server'
import { z } from 'zod'
import { requireSession } from '@/lib/auth'
import { createProject, listProjects } from '@/lib/projects'
import { projectInputSchema } from '@/lib/domain'
import { toErrorResponse } from '@/lib/errors'
export async function GET(){try{return NextResponse.json({projects:listProjects(requireSession().userId)})}catch(e){return NextResponse.json(toErrorResponse(e),{status:401})}}
export async function POST(request:Request){try{const input=projectInputSchema.parse(await request.json()); const project=createProject(requireSession().userId,input); return NextResponse.json({project},{status:201})}catch(e){return NextResponse.json(toErrorResponse(e),{status:400})}}
