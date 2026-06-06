'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/browser'
import { signOut } from '@/app/actions/auth'

export function HeaderAuth() {
  const [user, setUser] = useState<{ email?: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    const supabase = createClient()
    supabase.auth.getUser().then((result: any) => {
      setUser(result.data.user)
      setLoading(false)
    }).catch(() => {
      setLoading(false)
    })
  }, [])

  if (loading) return null

  if (!user) {
    return (
      <button
        onClick={() => router.push('/auth')}
        className="text-xs px-3 py-1.5 rounded border transition-all duration-150"
        style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--foreground)"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--muted-foreground)"; }}
      >
        Sign in
      </button>
    )
  }

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
        {user.email}
      </span>
      <button
        onClick={() => signOut()}
        className="text-xs px-3 py-1.5 rounded border transition-all duration-150"
        style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--foreground)"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = "var(--muted-foreground)"; }}
      >
        Sign out
      </button>
    </div>
  )
}
