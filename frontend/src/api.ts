import type { Artifact, JobLibraryResponse, JobView, PersonProfile, VideoReport } from './types'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

function assertOk(response: Response, message: string) {
  if (!response.ok) {
    throw new Error(`${message} (${response.status})`)
  }
}

export async function createJob(params: {
  file: File
  language?: string
  autoDetectLanguage: boolean
  qualityPreset: 'max_quality' | 'balanced' | 'max_speed'
  detectPeople: boolean
  generateSummary: boolean
  enableActiveSpeakerModel: boolean
  enableSubtitles: boolean
  enableBurnedVideo: boolean
  enableMaskOverlay: boolean
  uiLocale: 'ru' | 'en'
  exportFormats: string[]
}): Promise<JobView> {
  const formData = new FormData()
  formData.append('video', params.file)
  formData.append('language', params.language ?? '')
  formData.append('auto_detect_language', String(params.autoDetectLanguage))
  formData.append('quality_preset', params.qualityPreset)
  formData.append('detect_people', String(params.detectPeople))
  formData.append('generate_summary', String(params.generateSummary))
  formData.append('enable_active_speaker_model', String(params.enableActiveSpeakerModel))
  formData.append('enable_subtitles', String(params.enableSubtitles))
  formData.append('enable_burned_video', String(params.enableBurnedVideo))
  formData.append('enable_mask_overlay', String(params.enableMaskOverlay))
  formData.append('ui_locale', params.uiLocale)
  formData.append('export_formats', params.exportFormats.join(','))

  const response = await fetch(`${API_BASE}/api/v1/jobs`, {
    method: 'POST',
    body: formData
  })
  assertOk(response, 'Failed to create job')
  const payload = await response.json()
  return payload.job as JobView
}

export async function fetchJobLibrary(limit = 100): Promise<JobView[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs?limit=${limit}`)
  assertOk(response, 'Failed to fetch job library')
  const payload = (await response.json()) as JobLibraryResponse
  return payload.items
}

export async function fetchJob(jobId: string): Promise<JobView> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}`)
  assertOk(response, 'Failed to fetch job')
  return (await response.json()) as JobView
}

export async function fetchArtifacts(jobId: string): Promise<Artifact[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/artifacts`)
  assertOk(response, 'Failed to fetch artifacts')
  const payload = await response.json()
  return payload.artifacts as Artifact[]
}

export async function fetchPeople(jobId: string): Promise<PersonProfile[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/people`)
  assertOk(response, 'Failed to fetch people')
  const payload = await response.json()
  return payload.people as PersonProfile[]
}

export async function fetchReport(jobId: string): Promise<VideoReport> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/report`)
  assertOk(response, 'Failed to fetch report')
  const payload = await response.json()
  return payload.report as VideoReport
}

export async function requestExportBundle(jobId: string, formats: string[]): Promise<Artifact[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ formats })
  })
  assertOk(response, 'Failed to create export bundle')
  const payload = await response.json()
  return payload.artifacts as Artifact[]
}

export function artifactDownloadUrl(jobId: string, artifactName: string): string {
  return `${API_BASE}/api/v1/jobs/${jobId}/artifacts/${encodeURIComponent(artifactName)}/download`
}
