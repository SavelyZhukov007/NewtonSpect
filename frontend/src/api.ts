import type {
  Artifact,
  Chapter,
  FactCheckItem,
  FormatCapabilitiesResponse,
  GlossaryTerm,
  JobLibraryResponse,
  JobView,
  KnowledgeBaseStatus,
  KeyQuote,
  PersonProfile,
  PersonRegistryEntry,
  QualityScore,
  RunComparison,
  ShortsExport,
  SubtitleRevision,
  TranscriptSegment,
  TranslationTrack,
  VideoReport
} from './types'

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

export function jobUploadWebSocketUrl(): string {
  return `${wsBaseFromApi(API_BASE)}/api/v1/jobs/ws/upload`
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
  streamingMode: 'dual_pass_hq' | 'final_only_hq' | 'live_only_fast'
  cameraMode: boolean
  autoStopSeconds: number
  showFaceMaskPreview: boolean
  outputVideoFormat: string
  subtitleEmbedMode: 'auto' | 'embedded' | 'sidecar' | 'burned'
  subtitleStyle: Record<string, unknown>
  exportFormats: string[]
  generateShorts: boolean
  shortsPreset: Record<string, unknown>
  privacyMode: 'auto_risk' | 'enabled' | 'disabled'
  translateLanguages: string[]
  enableFactCheck: boolean
  enableChapters: boolean
  enableQuotes: boolean
  enableQualityScore: boolean
  platformPresets: string[]
  enableLiveDraft: boolean
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
  formData.append('streaming_mode', params.streamingMode)
  formData.append('camera_mode', String(params.cameraMode))
  formData.append('auto_stop_seconds', String(params.autoStopSeconds))
  formData.append('show_face_mask_preview', String(params.showFaceMaskPreview))
  formData.append('output_video_format', params.outputVideoFormat)
  formData.append('subtitle_embed_mode', params.subtitleEmbedMode)
  formData.append('subtitle_style_json', JSON.stringify(params.subtitleStyle))
  formData.append('export_formats', params.exportFormats.join(','))
  formData.append('generate_shorts', String(params.generateShorts))
  formData.append('shorts_preset_json', JSON.stringify(params.shortsPreset))
  formData.append('privacy_mode', params.privacyMode)
  formData.append('translate_languages', params.translateLanguages.join(','))
  formData.append('enable_fact_check', String(params.enableFactCheck))
  formData.append('enable_chapters', String(params.enableChapters))
  formData.append('enable_quotes', String(params.enableQuotes))
  formData.append('enable_quality_score', String(params.enableQualityScore))
  formData.append('platform_presets', params.platformPresets.join(','))
  formData.append('enable_live_draft', String(params.enableLiveDraft))

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
  streamingMode: 'dual_pass_hq' | 'final_only_hq' | 'live_only_fast'
  cameraMode: boolean
  autoStopSeconds: number
  showFaceMaskPreview: boolean
  outputVideoFormat: string
  subtitleEmbedMode: 'auto' | 'embedded' | 'sidecar' | 'burned'
  subtitleStyle: Record<string, unknown>
  exportFormats: string[]
  generateShorts: boolean
  shortsPreset: Record<string, unknown>
  privacyMode: 'auto_risk' | 'enabled' | 'disabled'
  translateLanguages: string[]
  enableFactCheck: boolean
  enableChapters: boolean
  enableQuotes: boolean
  enableQualityScore: boolean
  platformPresets: string[]
  enableLiveDraft: boolean
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
                ui_locale: params.uiLocale,
                streaming_mode: params.streamingMode,
                camera_mode: params.cameraMode,
                auto_stop_seconds: params.autoStopSeconds,
                show_face_mask_preview: params.showFaceMaskPreview,
                output_video_format: params.outputVideoFormat,
                subtitle_embed_mode: params.subtitleEmbedMode,
                subtitle_style: params.subtitleStyle,
                generate_shorts: params.generateShorts,
                shorts_preset: params.shortsPreset,
                privacy_mode: params.privacyMode,
                translate_languages: params.translateLanguages,
                enable_fact_check: params.enableFactCheck,
                enable_chapters: params.enableChapters,
                enable_quotes: params.enableQuotes,
                enable_quality_score: params.enableQualityScore,
                platform_presets: params.platformPresets,
                enable_live_draft: params.enableLiveDraft
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

export async function fetchFormatCapabilities(): Promise<FormatCapabilitiesResponse> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/capabilities/formats`)
  assertOk(response, 'Failed to fetch format capabilities')
  return (await response.json()) as FormatCapabilitiesResponse
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

export async function fetchChapters(jobId: string): Promise<Chapter[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/chapters`)
  assertOk(response, 'Failed to fetch chapters')
  const payload = await response.json()
  return payload.chapters as Chapter[]
}

export async function fetchQuotes(jobId: string): Promise<KeyQuote[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/quotes`)
  assertOk(response, 'Failed to fetch quotes')
  const payload = await response.json()
  return payload.quotes as KeyQuote[]
}

export async function fetchQuality(jobId: string): Promise<QualityScore> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/quality`)
  assertOk(response, 'Failed to fetch quality')
  const payload = await response.json()
  return payload.quality as QualityScore
}

export async function fetchComparison(jobId: string): Promise<RunComparison> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/comparison`)
  assertOk(response, 'Failed to fetch comparison')
  const payload = await response.json()
  return payload.comparison as RunComparison
}

export async function fetchSubtitles(jobId: string): Promise<{
  segments: TranscriptSegment[]
  revisions: SubtitleRevision[]
}> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/subtitles`)
  assertOk(response, 'Failed to fetch subtitles')
  const payload = await response.json()
  return {
    segments: payload.segments as TranscriptSegment[],
    revisions: payload.revisions as SubtitleRevision[]
  }
}

export async function updateSubtitles(
  jobId: string,
  segments: TranscriptSegment[],
  note: string
): Promise<{ segments: TranscriptSegment[]; revisions: SubtitleRevision[] }> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/subtitles`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ segments, note })
  })
  assertOk(response, 'Failed to update subtitles')
  const payload = await response.json()
  return {
    segments: payload.segments as TranscriptSegment[],
    revisions: payload.revisions as SubtitleRevision[]
  }
}

export async function fetchTranslations(jobId: string): Promise<TranslationTrack[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/translations`)
  assertOk(response, 'Failed to fetch translations')
  const payload = await response.json()
  return payload.tracks as TranslationTrack[]
}

export async function fetchFactCheck(jobId: string): Promise<FactCheckItem[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/fact-check`)
  assertOk(response, 'Failed to fetch fact-check')
  const payload = await response.json()
  return payload.items as FactCheckItem[]
}

export async function buildShorts(
  jobId: string,
  clipCount: number,
  clipDurationSeconds: number
): Promise<ShortsExport[]> {
  const response = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/shorts`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      clip_count: clipCount,
      clip_duration_seconds: clipDurationSeconds
    })
  })
  assertOk(response, 'Failed to generate shorts')
  const payload = await response.json()
  return payload.shorts as ShortsExport[]
}

export async function listGlossary(): Promise<GlossaryTerm[]> {
  const response = await fetch(`${API_BASE}/api/v1/glossary`)
  assertOk(response, 'Failed to fetch glossary')
  const payload = await response.json()
  return payload.items as GlossaryTerm[]
}

export async function upsertGlossary(params: {
  source: string
  target: string
  locale: string
}): Promise<GlossaryTerm[]> {
  const response = await fetch(`${API_BASE}/api/v1/glossary`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(params)
  })
  assertOk(response, 'Failed to upsert glossary term')
  const payload = await response.json()
  return payload.items as GlossaryTerm[]
}

export async function deleteGlossary(termId: string): Promise<GlossaryTerm[]> {
  const response = await fetch(`${API_BASE}/api/v1/glossary/${encodeURIComponent(termId)}`, {
    method: 'DELETE'
  })
  assertOk(response, 'Failed to delete glossary term')
  const payload = await response.json()
  return payload.items as GlossaryTerm[]
}

export async function fetchPersonRegistry(): Promise<PersonRegistryEntry[]> {
  const response = await fetch(`${API_BASE}/api/v1/person-registry`)
  assertOk(response, 'Failed to fetch person registry')
  const payload = await response.json()
  return payload.items as PersonRegistryEntry[]
}

export async function mergePersonRegistry(
  sourceRegistryId: string,
  targetRegistryId: string
): Promise<PersonRegistryEntry[]> {
  const response = await fetch(`${API_BASE}/api/v1/person-registry/merge`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      source_registry_id: sourceRegistryId,
      target_registry_id: targetRegistryId
    })
  })
  assertOk(response, 'Failed to merge person registry')
  const payload = await response.json()
  return payload.items as PersonRegistryEntry[]
}

export async function splitPersonRegistry(
  registryId: string,
  aliasToSplit: string
): Promise<PersonRegistryEntry[]> {
  const response = await fetch(`${API_BASE}/api/v1/person-registry/split`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      registry_id: registryId,
      alias_to_split: aliasToSplit
    })
  })
  assertOk(response, 'Failed to split person registry')
  const payload = await response.json()
  return payload.items as PersonRegistryEntry[]
}

export async function fetchKnowledgeBaseStatus(): Promise<KnowledgeBaseStatus> {
  const response = await fetch(`${API_BASE}/api/v1/kb/status`)
  assertOk(response, 'Failed to fetch KB status')
  const payload = await response.json()
  return payload.status as KnowledgeBaseStatus
}

export async function reindexKnowledgeBase(): Promise<KnowledgeBaseStatus> {
  const response = await fetch(`${API_BASE}/api/v1/kb/reindex`, {
    method: 'POST'
  })
  assertOk(response, 'Failed to reindex KB')
  const payload = await response.json()
  return payload.status as KnowledgeBaseStatus
}

export function artifactDownloadUrl(jobId: string, artifactName: string): string {
  return `${API_BASE}/api/v1/jobs/${jobId}/artifacts/${encodeURIComponent(artifactName)}/download`
}

function parseFilenameFromHeader(value: string | null): string | null {
  if (!value) return null
  const utfMatch = /filename\*=UTF-8''([^;]+)/i.exec(value)
  if (utfMatch?.[1]) return decodeURIComponent(utfMatch[1])
  const basicMatch = /filename="?([^";]+)"?/i.exec(value)
  return basicMatch?.[1] ?? null
}

export interface ArtifactBlobPayload {
  blob: Blob
  filename: string
}

export async function fetchArtifactBlob(
  jobId: string,
  artifactName: string
): Promise<ArtifactBlobPayload> {
  const response = await fetch(artifactDownloadUrl(jobId, artifactName))
  assertOk(response, 'Failed to download artifact')
  const blob = await response.blob()
  const filename =
    parseFilenameFromHeader(response.headers.get('Content-Disposition')) || artifactName
  return { blob, filename }
}

export async function fetchArtifactText(jobId: string, artifactName: string): Promise<string> {
  const payload = await fetchArtifactBlob(jobId, artifactName)
  return payload.blob.text()
}

export async function downloadArtifact(jobId: string, artifactName: string): Promise<string> {
  const payload = await fetchArtifactBlob(jobId, artifactName)
  const objectUrl = URL.createObjectURL(payload.blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = payload.filename
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1500)
  return payload.filename
}
