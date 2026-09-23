import { redirect } from 'next/navigation'
import { getSession } from '@/lib/auth'
import DashboardClient from './dashboard-client'
export default async function Page() { const session = await getSession(); if (!session) redirect('/auth/signin'); return <DashboardClient /> }
