// Authentication boundary. Replace this adapter with Auth.js/another session provider.
// No client code receives provider credentials. Every repository call requires ownerId.
export type Session = { userId:string; email:string }
export function getServerSession(): Session | null { return process.env.AUTH_DISABLED === 'true' ? null : { userId: 'local-development-user', email: 'developer@local.invalid' } }
export function requireSession(): Session { const session=getServerSession(); if(!session) throw new Error('AUTHENTICATION_REQUIRED'); return session }
