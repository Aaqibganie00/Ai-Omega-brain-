import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { signUpSchema } from '@/lib/validation'
import { toErrorResponse } from '@/lib/errors'
export async function POST(request: Request) { try { const input = signUpSchema.parse(await request.json()); const email = input.email.toLowerCase(); const exists = await prisma.user.findUnique({ where: { email } }); if (exists) return NextResponse.json({ error: { code: 'EMAIL_EXISTS', message: 'An account already exists for this email.', category: 'validation', retryable: false } }, { status: 409 }); const user = await prisma.user.create({ data: { email, passwordHash: await bcrypt.hash(input.password, 12) }, select: { id: true, email: true, createdAt: true } }); return NextResponse.json({ user }, { status: 201 }) } catch (error) { return NextResponse.json(toErrorResponse(error), { status: 400 }) } }
