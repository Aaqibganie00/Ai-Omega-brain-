import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { addTask } from '@/lib/projects'
import { taskInputSchema } from '@/lib/validation'
import { toErrorResponse } from '@/lib/errors'
export async function POST(request: Request, { params }: { params: { id: string } }) { try { const input = taskInputSchema.parse(await request.json()); const task = await addTask((await requireSession()).userId, params.id, input); return task ? NextResponse.json({ task }, { status: 201 }) : NextResponse.json({ error: { code: 'NOT_FOUND', message: 'Project not found', category: 'validation', retryable: false } }, { status: 404 }) } catch (error) { return NextResponse.json(toErrorResponse(error), { status: 400 }) } }
