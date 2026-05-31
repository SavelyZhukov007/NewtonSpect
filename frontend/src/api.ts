import type { Artifact, JobLibraryResponse, JobView, PersonProfile, VideoReport } from './types'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'
const DEFAULT_CHUNK_SIZE = 1024 * 1024

function assertOk(response: Response, message: string) {
  if (!response.ok) {
    throw new Error(`${message} (${response.status})`)
  }
}

function wsBaseFromApi(apiBase: string): string {
  if (apiBase.startsWith('https://')) {
    return `wss://${apiBase.slice('https://'.length)}`
  }
  if (apiBase.startsWith('http://')) {
    return `ws://${apiBase.slice('http://'.length)}`
  }
  return apiBase
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

export interface UploadProgress {
  receivedBytes: number
  totalSize: number | null
  percent: number | null
  speedBytesPerSec: number | null
  etaSeconds: number | null
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

export async function createJobChunked(params: {
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
  onProgress?: (progress: UploadProgress) => void
  chunkSize?: number
}): Promise<JobView> {
  const chunkSize = params.chunkSize ?? DEFAULT_CHUNK_SIZE
  const wsUrl = `${wsBaseFromApi(API_BASE)}/api/v1/jobs/ws/upload`
  const ws = new WebSocket(wsUrl)
  ws.binaryType = 'arraybuffer'

  return new Promise<JobView>((resolve, reject) => {
    let uploadStarted = false
    let settled = false

    const fail = (message: string) => {
      if (settled) return
      settled = true
      try {
        ws.close()
      } catch {
        // ignored
      }
      reject(new Error(message))
    }

    const finish = (job: JobView) => {
      if (settled) return
      settled = true
      resolve(job)
    }

    ws.onerror = () => {
      fail('WebSocket upload failed')
    }

    ws.onclose = () => {
      if (!settled) {
        fail('WebSocket closed before upload completion')
      }
    }

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: 'start',
          filename: params.file.name,
          total_size: params.file.size
        })
      )
    }

    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') {
        return
      }
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(event.data) as Record<string, unknown>
      } catch {
        fail('Server returned invalid WebSocket payload')
        return
      }
      const messageType = payload.type
      if (messageType === 'error') {
        fail(String(payload.message || 'Upload failed'))
        return
      }
      if (messageType === 'started') {
        if (uploadStarted) return
        uploadStarted = true
        void (async () => {
          for (let offset = 0; offset < params.file.size; offset += chunkSize) {
            const chunk = params.file.slice(offset, Math.min(offset + chunkSize, params.file.size))
            const buffer = await chunk.arrayBuffer()
            ws.send(buffer)
            while (ws.bufferedAmount > chunkSize * 4) {
              await delay(14)
            }
          }
          ws.send(
            JSON.stringify({
              type: 'finish',
              options: {
                language: params.language ?? null,
                auto_detect_language: params.autoDetectLanguage,
                quality_preset: params.qualityPreset,
                export_formats: params.exportFormats,
                detect_people: params.detectPeople,
                generate_summary: params.generateSummary,
                enable_active_speaker_model: params.enableActiveSpeakerModel,
                enable_subtitles: params.enableSubtitles,
                enable_burned_video: params.enableBurnedVideo,
                enable_mask_overlay: params.enableMaskOverlay,
                ui_locale: params.uiLocale
              }
            })
          )
        })().catch((err) => {
          fail(`Upload failed: ${(err as Error).message}`)
        })
        return
      }
      if (messageType === 'progress') {
        params.onProgress?.({
          receivedBytes: Number(payload.received_bytes ?? 0),
          totalSize: payload.total_size == null ? null : Number(payload.total_size),
          percent: payload.percent == null ? null : Number(payload.percent),
          speedBytesPerSec:
            payload.speed_bytes_per_sec == null ? null : Number(payload.speed_bytes_per_sec),
          etaSeconds: payload.eta_seconds == null ? null : Number(payload.eta_seconds)
        })
        return
      }
      if (messageType === 'completed') {
        const job = payload.job as JobView | undefined
        if (!job) {
          fail('Server returned completed response without job payload')
          return
        }
        finish(job)
      }
    }
  })
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
