import NextAuth, { type NextAuthOptions } from 'next-auth'
import CredentialsProvider from 'next-auth/providers/credentials'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'

export const authOptions: NextAuthOptions = {
  session: { strategy: 'jwt' },
  pages: { signIn: '/auth/signin' },
  providers: [CredentialsProvider({
    name: 'Email and password',
    credentials: { email: { label: 'Email', type: 'email' }, password: { label: 'Password', type: 'password' } },
    async authorize(credentials) {
      if (!credentials?.email || !credentials.password) return null
      const user = await prisma.user.findUnique({ where: { email: credentials.email.toLowerCase() } })
      if (!user || !(await bcrypt.compare(credentials.password, user.passwordHash))) return null
      return { id: user.id, email: user.email }
    },
  })],
  secret: process.env.NEXTAUTH_SECRET,
  callbacks: {
    async jwt({ token, user }) { if (user?.id) token.userId = user.id; return token },
    async session({ session, token }) { if (session.user && token.userId) session.user.id = token.userId; return session },
  },
}
export const authHandler = NextAuth(authOptions)
export { authHandler as GET, authHandler as POST }
