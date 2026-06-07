'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { signIn, signUp } from '@/app/actions/auth'
import { GoogleButton } from '@/components/auth/GoogleButton'
import { createClient } from '@/lib/supabase/browser'

export default function AuthPage() {
  const [isSignUp, setIsSignUp] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [oauthPending, setOauthPending] = useState(false)
  const router = useRouter()

  // Listen for yap:// deep link from Electron (Google OAuth callback)
  useEffect(() => {
    const api = (window as any).electronAPI
    if (!api?.onDeepLink) return

    setOauthPending(false)

    const cleanup = api.onDeepLink(async (url: string) => {
      setOauthPending(true)
      try {
        const parsed = new URL(url)
        const code = parsed.searchParams.get('code')
        if (code) {
          const supabase = createClient()
          const { error } = await supabase.auth.exchangeCodeForSession(code)
          if (error) {
            setError('Google sign-in failed. Try again.')
          } else {
            router.push('/')
          }
        }
      } catch {
        setError('Google sign-in failed. Try again.')
      } finally {
        setOauthPending(false)
      }
    })

    return cleanup
  }, [router])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      if (isSignUp) {
        const result = await signUp(email, password)
        if (result?.error) setError(result.error)
      } else {
        const result = await signIn(email, password)
        if (result?.error) setError(result.error)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Auth error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-full max-w-md p-8 border border-border rounded-lg bg-card">
        <div className="flex items-center justify-center gap-3 mb-8">
          <div style={{ width: 36, height: 36, borderRadius: 9, background: 'var(--primary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="22" height="22" viewBox="0 0 52 52" fill="none">
              <path d="M7 25 L7 7 L25 7" stroke="white" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M27 45 L45 45 L45 27" stroke="white" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <span style={{ fontFamily: "'Syne', sans-serif", fontWeight: 800, fontSize: 26, letterSpacing: '-0.04em', color: 'var(--foreground)', lineHeight: 1 }}>yap</span>
        </div>
        <h1 className="text-base font-medium mb-6 text-center" style={{ color: 'var(--muted-foreground)' }}>
          {isSignUp ? 'Create an account' : 'Sign in to continue'}
        </h1>

        <GoogleButton />
        {oauthPending && (
          <p className="text-xs text-center mt-2" style={{ color: 'var(--muted-foreground)' }}>
            Completing sign-in…
          </p>
        )}

        <div className="flex items-center gap-3 my-4">
          <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
          <span className="text-xs text-muted-foreground">or</span>
          <div className="flex-1 h-px" style={{ background: "var(--border)" }} />
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-4 py-2 border border-border rounded bg-background text-foreground"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="w-full px-4 py-2 border border-border rounded bg-background text-foreground"
          />

          {error && <p className="text-sm text-destructive">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 bg-primary text-primary-foreground rounded font-medium disabled:opacity-50"
          >
            {loading ? 'Loading...' : isSignUp ? 'Sign Up' : 'Sign In'}
          </button>
        </form>

        <button
          onClick={() => setIsSignUp(!isSignUp)}
          className="w-full mt-4 text-sm text-muted-foreground hover:text-foreground"
        >
          {isSignUp ? 'Already have an account? Sign in' : 'Need an account? Sign up'}
        </button>
      </div>
    </div>
  )
}
