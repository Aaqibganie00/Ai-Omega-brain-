import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { updateTask } from '@/lib/projects'
import { taskUpdateSchema } from '@/lib/validation'
import { toErrorResponse } from '@/lib/errors'
export async function PATCH(request: Request, { params }: { params: { id: string; taskId: string } }) { try { const input = taskUpdateSchema.parse(await request.json()); const task = await updateTask((await requireSession()).userId, params.id, params.taskId, input); return task ? NextResponse.json({ task }) : NextResponse.json({ error: { code: 'NOT_FOUND', message: 'Task not found', category: 'validation', retryable: false } }, { status: 404 }) } catch (error) { return NextResponse.json(toErrorResponse(error), { status: 400 }) } }
