import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { createProject, listProjects } from '@/lib/projects'
import { projectInputSchema } from '@/lib/domain'
import { toErrorResponse } from '@/lib/errors'
export async function GET() { try { return NextResponse.json({ projects: await listProjects((await requireSession()).userId) }) } catch (error) { return NextResponse.json(toErrorResponse(error), { status: 401 }) } }
export async function POST(request: Request) { try { const input = projectInputSchema.parse(await request.json()); const project = await createProject((await requireSession()).userId, input); return NextResponse.json({ project }, { status: 201 }) } catch (error) { return NextResponse.json(toErrorResponse(error), { status: 400 }) } }
