'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/browser'
import type { EditPlan } from '@/lib/editPlan'

type Project = {
  id: string
  name: string
  created_at: string
  data: EditPlan
}

export default function ProjectsPage() {
  const [isElectron, setIsElectron] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [expandedMenu, setExpandedMenu] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const router = useRouter()

  useEffect(() => { setIsElectron(!!(window as any).electronAPI?.isElectron) }, [])

  useEffect(() => {
    const supabase = createClient()
    supabase.auth.getUser().then(async (result: any) => {
      if (!result.data.user) {
        router.push('/auth')
        return
      }

      const { data, error } = await supabase
        .from('projects')
        .select('*')
        .eq('user_id', result.data.user.id)
        .order('created_at', { ascending: false })

      if (!error && data) {
        setProjects(data as Project[])
      }
      setLoading(false)
    })
  }, [router])

  const handleExport = async (project: Project) => {
    try {
      const response = await fetch('/api/projects/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan: project.data, projectName: project.name }),
      })
      if (!response.ok) throw new Error('Export failed')
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${project.name.replace(/[^a-z0-9]/gi, '_')}.yap`
      a.click()
      URL.revokeObjectURL(url)
      setExpandedMenu(null)
    } catch (e) {
      alert('Failed to export project')
    }
  }

  const handleImportClick = () => {
    fileInputRef.current?.click()
  }

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setImporting(true)
    setImportError(null)

    try {
      const formData = new FormData()
      formData.append('file', file)
      const response = await fetch('/api/projects/import', { method: 'POST', body: formData })
      if (!response.ok) throw new Error('Import failed')
      const { plan, projectName } = await response.json()

      // Save to Supabase
      const saveResponse = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan, projectName }),
      })
      if (!saveResponse.ok) throw new Error('Save failed')
      const { id } = await saveResponse.json()

      // Reload projects and go to the imported one
      const supabase = createClient()
      const result = await supabase.auth.getUser()
      const { data } = await supabase
        .from('projects')
        .select('*')
        .eq('user_id', result.data.user?.id)
        .order('created_at', { ascending: false })
      if (data) setProjects(data as Project[])

      router.push(`/?project=${id}`)
    } catch (e) {
      setImportError(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen" style={{ background: 'var(--background)' }}>
        <style>{`
          @keyframes shimmer { 0%{opacity:0.4} 50%{opacity:0.7} 100%{opacity:0.4} }
          .sk { animation: shimmer 1.6s ease-in-out infinite; background: var(--secondary); border-radius: 6px; }
        `}</style>
        <header className="border-b flex items-center justify-between py-4" style={{ borderColor: 'var(--border)', background: 'rgba(8,8,9,0.9)', paddingLeft: isElectron ? 84 : 24, paddingRight: 24 }}>
          <div className="flex items-center gap-3">
            <div style={{ width: 28, height: 28, borderRadius: 7, background: 'var(--primary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <svg width="18" height="18" viewBox="0 0 52 52" fill="none">
                <path d="M7 25 L7 7 L25 7" stroke="white" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M27 45 L45 45 L45 27" stroke="white" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <span style={{ fontFamily: "'Syne', sans-serif", fontWeight: 800, fontSize: 20, letterSpacing: '-0.04em', color: 'var(--foreground)', lineHeight: 1 }}>yap</span>
            <span style={{ fontSize: 13, color: 'var(--muted-foreground)' }}>/ projects</span>
          </div>
        </header>
        <main className="max-w-4xl mx-auto px-4 sm:px-8 py-10">
          <div style={{ width: 100, height: 10, marginBottom: 28 }} className="sk" />
          {[0, 1, 2].map(i => (
            <div key={i} className="p-4 rounded border mb-3" style={{ borderColor: 'var(--border)', background: 'var(--card)', animationDelay: `${i * 0.12}s` }}>
              <div className="flex items-center justify-between">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ width: 160 + i * 40, height: 13 }} className="sk" />
                  <div style={{ width: 80, height: 9 }} className="sk" />
                </div>
                <div style={{ width: 52, height: 22, borderRadius: 6 }} className="sk" />
              </div>
            </div>
          ))}
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <header
        className="border-b flex items-center justify-between py-4"
        style={{
          borderColor: 'var(--border)',
          background: 'rgba(8,8,9,0.9)',
          backdropFilter: 'blur(8px)',
          position: 'sticky',
          top: 0,
          zIndex: 50,
          WebkitAppRegion: 'drag',
          paddingLeft: isElectron ? 84 : 24,
          paddingRight: 24,
        } as any}
      >
        <div className="flex items-center gap-3">
          <div style={{ width: 28, height: 28, borderRadius: 7, background: 'var(--primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <svg width="18" height="18" viewBox="0 0 52 52" fill="none">
              <path d="M7 25 L7 7 L25 7" stroke="white" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M27 45 L45 45 L45 27" stroke="white" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <span style={{ fontFamily: "'Syne', sans-serif", fontWeight: 800, fontSize: 20, letterSpacing: '-0.04em', color: 'var(--foreground)', lineHeight: 1 }}>yap</span>
          <span style={{ fontSize: 13, color: 'var(--muted-foreground)' }}>/ projects</span>
        </div>

        <button
          onClick={() => router.push('/')}
          className="text-xs px-3 py-1.5 rounded border transition-all duration-150"
          style={{ borderColor: 'var(--border)', color: 'var(--muted-foreground)', WebkitAppRegion: 'no-drag' } as any}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--foreground)'; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = 'var(--muted-foreground)'; }}
        >
          Back to Editor
        </button>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-8 py-10">
        <input
          ref={fileInputRef}
          type="file"
          accept=".yap,.json"
          onChange={handleImportFile}
          style={{ display: 'none' }}
        />

        {importError && (
          <div
            className="mb-4 p-4 rounded text-sm"
            style={{
              background: 'rgba(220, 38, 38, 0.1)',
              color: 'var(--destructive)',
              border: '1px solid var(--destructive)',
            }}
          >
            {importError}
          </div>
        )}

        <div className="mb-6">
          <button
            onClick={handleImportClick}
            disabled={importing}
            className="text-xs px-4 py-2 rounded border transition-all"
            style={{
              borderColor: 'var(--border)',
              color: 'var(--foreground)',
              opacity: importing ? 0.5 : 1,
            }}
          >
            {importing ? 'Importing...' : 'Import Project'}
          </button>
        </div>

        {projects.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-lg text-muted-foreground">No projects yet</p>
            <button
              onClick={() => router.push('/')}
              className="mt-4 px-4 py-2 rounded"
              style={{
                background: 'var(--primary)',
                color: 'var(--primary-foreground)',
              }}
            >
              Create your first project
            </button>
          </div>
        ) : (
          <div className="grid gap-4">
            {projects.map((project) => (
              <div
                key={project.id}
                className="p-4 rounded border transition-all"
                style={{
                  borderColor: expandedMenu === project.id ? 'var(--primary)' : 'var(--border)',
                  background: 'var(--card)',
                  position: 'relative',
                }}
              >
                <div className="flex items-start justify-between">
                  <button
                    onClick={() => router.push(`/?project=${project.id}`)}
                    className="text-left flex-1 hover:opacity-75 transition-opacity"
                  >
                    <h3 className="font-semibold" style={{ color: 'var(--foreground)' }}>
                      {project.name}
                    </h3>
                    <p className="text-xs" style={{ color: 'var(--muted-foreground)', marginTop: '4px' }}>
                      {new Date(project.created_at).toLocaleDateString()}
                    </p>
                  </button>
                  <div className="flex items-center gap-3 ml-4">
                    <span className="text-xs px-2 py-1 rounded" style={{ background: 'var(--secondary)', color: 'var(--secondary-foreground)' }}>
                      {project.data.editedDuration}
                    </span>
                    <div style={{ position: 'relative' }}>
                      <button
                        onClick={() => setExpandedMenu(expandedMenu === project.id ? null : project.id)}
                        className="text-xs px-2 py-1 rounded border"
                        style={{
                          borderColor: 'var(--border)',
                          color: 'var(--muted-foreground)',
                        }}
                      >
                        ⋮
                      </button>
                      {expandedMenu === project.id && (
                        <div
                          style={{
                            position: 'absolute',
                            top: '100%',
                            right: 0,
                            marginTop: '4px',
                            background: 'var(--card)',
                            border: '1px solid var(--border)',
                            borderRadius: '8px',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                            zIndex: 10,
                            minWidth: '120px',
                          }}
                        >
                          <button
                            onClick={() => handleExport(project)}
                            className="w-full px-4 py-2 text-xs text-left"
                            style={{
                              color: 'var(--muted-foreground)',
                              borderBottom: '1px solid var(--border)',
                            }}
                            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--secondary)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--foreground)'; }}
                            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--muted-foreground)'; }}
                          >
                            Download
                          </button>
                          <button
                            onClick={async () => {
                              if (!confirm('Delete this project?')) return
                              await fetch(`/api/projects/${project.id}`, { method: 'DELETE' })
                              setProjects(projects.filter(p => p.id !== project.id))
                              setExpandedMenu(null)
                            }}
                            className="w-full px-4 py-2 text-xs text-left"
                            style={{ color: 'var(--destructive)' }}
                            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--secondary)'; }}
                            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
