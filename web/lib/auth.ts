import { getServerSession as nextAuthSession } from 'next-auth'
import { authOptions } from '@/app/api/auth/[...nextauth]/route'
export type Session = { userId: string; email: string }
export async function getSession(): Promise<Session | null> { const session = await nextAuthSession(authOptions); const userId = session?.user?.id; const email = session?.user?.email; return userId && email ? { userId, email } : null }
export async function requireSession(): Promise<Session> { const session = await getSession(); if (!session) throw new Error('AUTHENTICATION_REQUIRED'); return session }
