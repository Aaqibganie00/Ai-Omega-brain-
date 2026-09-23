import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { Prisma } from '@prisma/client'
import { prisma } from '@/lib/prisma'
import { signUpSchema } from '@/lib/validation'
import { toErrorResponse } from '@/lib/errors'
export async function POST(request: Request) { try { const input = signUpSchema.parse(await request.json()); const email = input.email.toLowerCase(); const user = await prisma.user.create({ data: { email, passwordHash: await bcrypt.hash(input.password, 12) }, select: { id: true, email: true, createdAt: true } }); return NextResponse.json({ user }, { status: 201 }) } catch (error) { if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === 'P2002') return NextResponse.json({ error: { code: 'EMAIL_EXISTS', message: 'An account already exists for this email.', category: 'validation', retryable: false } }, { status: 409 }); return NextResponse.json(toErrorResponse(error), { status: 400 }) } }
