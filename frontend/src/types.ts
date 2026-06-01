export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export type ExportFormat = string

export interface JobOptions {
  language: string | null
  auto_detect_language: boolean
  quality_preset: 'max_quality' | 'balanced' | 'max_speed'
  whisper_model: string
  export_formats: ExportFormat[]
  detect_people: boolean
  generate_summary: boolean
  enable_active_speaker_model: boolean
  enable_mask_overlay: boolean
  mask_model_names: string[]
  enable_subtitles: boolean
  enable_burned_video: boolean
  ui_locale: 'ru' | 'en'
  streaming_mode: 'dual_pass_hq' | 'final_only_hq' | 'live_only_fast'
  camera_mode: boolean
  auto_stop_seconds: number
  show_face_mask_preview: boolean
  output_video_format: string
  subtitle_embed_mode: 'auto' | 'embedded' | 'sidecar' | 'burned'
  subtitle_style: Record<string, unknown>
  generate_shorts: boolean
  shorts_preset: Record<string, unknown>
  privacy_mode: 'auto_risk' | 'enabled' | 'disabled'
  translate_languages: string[]
  enable_fact_check: boolean
  enable_chapters: boolean
  enable_quotes: boolean
  enable_quality_score: boolean
  platform_presets: string[]
  enable_live_draft: boolean
}

export interface StageRuntime {
  step: string
  progress: number
  speed: number | null
  speed_unit: string | null
  eta_seconds: number | null
  message: string | null
  started_at: string | null
  updated_at: string | null
  completed: boolean
}

export interface JobRuntime {
  stages: Record<string, StageRuntime>
  overall_eta_seconds: number | null
  current_speed: number | null
  current_speed_unit: string | null
}

export interface Artifact {
  name: string
  kind: string
  path: string
  mime_type: string
  created_at: string
}

export interface JobView {
  id: string
  original_filename: string
  created_by_device: string
  locale: 'ru' | 'en'
  status: JobStatus
  progress: number
  current_step: string | null
  error_message: string | null
  options: JobOptions
  artifacts: Artifact[]
  runtime: JobRuntime
  created_at: string
  updated_at: string
}

export interface PersonTrackStats {
  screen_time_seconds: number
  first_seen: number
  last_seen: number
  avg_confidence: number
  speaking_seconds: number
}

export interface PersonProfile {
  person_id: string
  display_name: string | null
  display_name_confidence: number
  portrait_path: string | null
  track_stats: PersonTrackStats
  key_comments: string[]
}

export interface VideoReport {
  summary_md: string
  latex_blocks: string[]
  key_topics: string[]
  people_highlights: Record<string, string[]>
  raw_markdown: string
}

export interface Chapter {
  chapter_id: string
  title: string
  start: number
  end: number
  confidence: number
}

export interface KeyQuote {
  quote_id: string
  start: number
  end: number
  text: string
  score: number
  speaker_ref: string | null
}

export interface SpeakerTimelineItem {
  speaker_ref: string
  start: number
  end: number
  duration: number
}

export interface QualityScore {
  overall: number
  asr_confidence: number
  subtitle_coverage: number
  speaker_stability: number
  people_stability: number
  report_completeness: number
  notes: string[]
  speaker_timeline: SpeakerTimelineItem[]
}

export interface RunComparison {
  current_job_id: string
  previous_job_id: string | null
  wer_like_delta: number
  people_delta: number
  subtitle_coverage_delta: number
  speaker_stability_delta: number
  duration_speech_delta: number
  summary_md: string
}

export interface SubtitleRevision {
  revision_id: number
  job_id: string
  editor_device: string
  note: string
  created_at: string
}

export interface TranslationTrack {
  language: string
  segments: TranscriptSegment[]
}

export interface TranscriptSegment {
  id?: string | null
  start: number
  end: number
  text: string
  confidence: number
  speaker_ref: string | null
}

export interface GlossaryTerm {
  term_id: string
  source: string
  target: string
  locale: string
  created_at: string
  updated_at: string
}

export interface PersonRegistryEntry {
  registry_id: string
  display_name: string
  aliases: string[]
  portrait_path: string | null
  linked_job_ids: string[]
  confidence: number
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseStatus {
  documents: number
  chunks: number
  indexed_at: string | null
  kb_root: string
}

export interface FactCheckItem {
  claim: string
  status: 'supported' | 'contradicted' | 'not_found'
  reason: string
  evidence_refs: string[]
}

export interface ShortsExport {
  short_id: string
  job_id: string
  label: string
  path: string
  start: number
  end: number
  created_at: string
}

export interface JobLibraryResponse {
  items: JobView[]
}

export interface VideoFormatCapability {
  format: string
  ffmpeg_muxer: string
  curated: boolean
  can_embed_subtitles: boolean
  preferred_subtitle_codec: string | null
  notes: string | null
}

export interface FormatCapabilitiesResponse {
  curated: VideoFormatCapability[]
  all_muxers: VideoFormatCapability[]
}
