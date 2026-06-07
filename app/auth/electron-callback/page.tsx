'use client'

import { useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { Suspense } from 'react'

function ElectronCallbackInner() {
  const searchParams = useSearchParams()
  const [status, setStatus] = useState<'redirecting' | 'done' | 'error'>('redirecting')

  useEffect(() => {
    const code = searchParams.get('code')
    const error = searchParams.get('error')

    if (error) {
      setStatus('error')
      return
    }

    if (!code) {
      setStatus('error')
      return
    }

    // Forward the code to the Electron app via the yap:// custom scheme
    // This triggers app.on('open-url') in electron/main.js
    const yapUrl = `yap://auth/callback?code=${encodeURIComponent(code)}`
    window.location.href = yapUrl

    // Give the OS a moment to hand off to Electron, then show the done state
    setTimeout(() => setStatus('done'), 800)
  }, [searchParams])

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#080809',
        color: '#fafafa',
        fontFamily: '-apple-system, BlinkMacSystemFont, sans-serif',
        gap: '16px',
        textAlign: 'center',
        padding: '32px',
      }}
    >
      {status === 'redirecting' && (
        <>
          <div style={{ fontSize: '28px', fontWeight: 700, letterSpacing: '-1px' }}>Yap</div>
          <div style={{ fontSize: '14px', color: '#888' }}>Signing you in…</div>
        </>
      )}
      {status === 'done' && (
        <>
          <div style={{ fontSize: '28px', fontWeight: 700, letterSpacing: '-1px' }}>Yap</div>
          <div
            style={{
              width: '40px',
              height: '40px',
              borderRadius: '50%',
              background: 'rgba(99,102,241,0.15)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M4 10l4.5 4.5L16 6" stroke="#6366f1" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div style={{ fontSize: '15px', fontWeight: 600 }}>Signed in successfully</div>
          <div style={{ fontSize: '13px', color: '#666' }}>You can close this tab and return to Yap.</div>
        </>
      )}
      {status === 'error' && (
        <>
          <div style={{ fontSize: '28px', fontWeight: 700, letterSpacing: '-1px' }}>Yap</div>
          <div style={{ fontSize: '14px', color: '#ef4444' }}>Sign-in failed. Please try again in the app.</div>
        </>
      )}
    </div>
  )
}

export default function ElectronCallbackPage() {
  return (
    <Suspense>
      <ElectronCallbackInner />
    </Suspense>
  )
}
