import type { Metadata } from 'next'
import './globals.css'
export const metadata:Metadata={title:'BLINDBRAIN AI',description:'AI creation and agent orchestration workspace'}
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
