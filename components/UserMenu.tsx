'use client'

import { useEffect, useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/browser'
import { signOut } from '@/app/actions/auth'

export function UserMenu() {
  const [user, setUser] = useState<{ email?: string; user_metadata?: any } | null>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const router = useRouter()

  useEffect(() => {
    const supabase = createClient()
    supabase.auth.getUser().then((result: any) => {
      setUser(result.data.user)
      setLoading(false)
    }).catch(() => {
      setLoading(false)
    })

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  if (loading) return null

  // Not signed in
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

  // Signed in
  const displayName = user.user_metadata?.full_name || user.email?.split('@')[0] || 'User'
  const initials = displayName.split(' ').map((n: string) => n[0]).join('').toUpperCase().slice(0, 2)

  return (
    <div ref={menuRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          width: '32px',
          height: '32px',
          borderRadius: '50%',
          background: 'var(--primary)',
          color: 'var(--primary-foreground)',
          border: 'none',
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: '600',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'transform 0.2s',
        }}
        title={user.email}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1.1)'; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)'; }}
      >
        {initials}
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: '8px',
            background: 'var(--card)',
            border: '1px solid var(--border)',
            borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            zIndex: 1000,
            minWidth: '200px',
          }}
        >
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--foreground)' }}>
              {displayName}
            </div>
            <div style={{ fontSize: '12px', color: 'var(--muted-foreground)', marginTop: '4px' }}>
              {user.email}
            </div>
          </div>
          <button
            onClick={() => signOut()}
            style={{
              width: '100%',
              padding: '12px 16px',
              background: 'transparent',
              border: 'none',
              color: 'var(--muted-foreground)',
              cursor: 'pointer',
              fontSize: '13px',
              textAlign: 'left',
              transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = 'var(--secondary)'
              ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--foreground)'
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = 'transparent'
              ;(e.currentTarget as HTMLButtonElement).style.color = 'var(--muted-foreground)'
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
