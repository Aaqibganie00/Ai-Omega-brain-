import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { getMemory, updateMemory } from '@/lib/projects'
import { memorySchema } from '@/lib/validation'
import { toErrorResponse } from '@/lib/errors'
export async function GET(_request: Request, { params }: { params: { id: string } }) { try { const memory = await getMemory((await requireSession()).userId, params.id); return memory ? NextResponse.json({ memory }) : NextResponse.json({ error: { code: 'NOT_FOUND', message: 'Project not found', category: 'validation', retryable: false } }, { status: 404 }) } catch (error) { return NextResponse.json(toErrorResponse(error), { status: 401 }) } }
export async function PATCH(request: Request, { params }: { params: { id: string } }) { try { const input = memorySchema.parse(await request.json()); const memory = await updateMemory((await requireSession()).userId, params.id, input); return memory ? NextResponse.json({ memory }) : NextResponse.json({ error: { code: 'NOT_FOUND', message: 'Project not found', category: 'validation', retryable: false } }, { status: 404 }) } catch (error) { return NextResponse.json(toErrorResponse(error), { status: 400 }) } }
