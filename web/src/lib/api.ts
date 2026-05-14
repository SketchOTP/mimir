import axios, { type AxiosResponse } from "axios";

const api = axios.create({
  baseURL: "/api",
  headers: { "X-API-Key": "local-dev-key" },
});

const asArray = <T>(value: unknown): T[] => Array.isArray(value) ? value as T[] : [];
const asObject = <T extends Record<string, unknown>>(value: unknown, fallback: T): T =>
  value && typeof value === "object" && !Array.isArray(value) ? { ...fallback, ...(value as Partial<T>) } : fallback;
const withData = <T>(response: AxiosResponse, data: T): AxiosResponse<T> =>
  ({ ...response, data }) as AxiosResponse<T>;
const getNormalized = async <T>(url: string, normalize: (value: unknown) => T, config?: object) => {
  const response = await api.get(url, config);
  return withData(response, normalize(response.data));
};

const normalizeDashboard = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return {
    memory_count: typeof raw.memory_count === "number" ? raw.memory_count : 0,
    skill_count: typeof raw.skill_count === "number" ? raw.skill_count : 0,
    pending_approvals: typeof raw.pending_approvals === "number" ? raw.pending_approvals : 0,
    rollback_events: typeof raw.rollback_events === "number" ? raw.rollback_events : 0,
    improvements_promoted: typeof raw.improvements_promoted === "number" ? raw.improvements_promoted : 0,
    retrieval_relevance_score: typeof raw.retrieval_relevance_score === "number" ? raw.retrieval_relevance_score : undefined,
    skill_success_rate: typeof raw.skill_success_rate === "number" ? raw.skill_success_rate : undefined,
    context_token_cost: typeof raw.context_token_cost === "number" ? raw.context_token_cost : undefined,
    recent_rollbacks: asArray<{ id: string; target_id: string; reason: string; created_at: string }>(raw.recent_rollbacks),
    recent_lessons: asArray<string>(raw.recent_lessons),
  };
};

const normalizeTelemetrySnapshot = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return {
    metrics: raw.metrics && typeof raw.metrics === "object" && !Array.isArray(raw.metrics)
      ? raw.metrics as Record<string, number>
      : {},
  };
};

const normalizeRetrievalStats = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  const stats = asObject(raw.stats, {} as Record<string, unknown>);
  return {
    stats: {
      total_sessions: typeof stats.total_sessions === "number" ? stats.total_sessions : 0,
      sessions_with_outcome: typeof stats.sessions_with_outcome === "number" ? stats.sessions_with_outcome : 0,
      outcome_distribution: stats.outcome_distribution && typeof stats.outcome_distribution === "object" && !Array.isArray(stats.outcome_distribution)
        ? stats.outcome_distribution as Record<string, number>
        : {},
      avg_token_cost: typeof stats.avg_token_cost === "number" ? stats.avg_token_cost : 0,
      avg_usefulness_score: typeof stats.avg_usefulness_score === "number" ? stats.avg_usefulness_score : null,
      sessions_with_rollback: typeof stats.sessions_with_rollback === "number" ? stats.sessions_with_rollback : 0,
      sessions_with_harmful: typeof stats.sessions_with_harmful === "number" ? stats.sessions_with_harmful : 0,
      window_hours: typeof stats.window_hours === "number" ? stats.window_hours : 24,
    },
  };
};

const normalizeRetrievalHeatmap = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  const heatmap = asObject(raw.heatmap, {} as Record<string, unknown>);
  return {
    heatmap: {
      most_used: asArray<any>(heatmap.most_used),
      rarely_used: asArray<any>(heatmap.rarely_used),
    },
  };
};

const normalizeProceduralEffectiveness = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { procedural_memories: asArray<any>(raw.procedural_memories) };
};

const normalizeDriftCandidates = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { drift_candidates: asArray<any>(raw.drift_candidates) };
};

const normalizeProviderStats = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { provider_stats: asArray<any>(raw.provider_stats) };
};

const normalizeProviderDrift = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { drifting_providers: asArray<any>(raw.drifting_providers) };
};

const normalizeApprovals = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { approvals: asArray<any>(raw.approvals) };
};

const normalizeNotifications = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { notifications: asArray<any>(raw.notifications) };
};

const normalizeMemories = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { memories: asArray<any>(raw.memories) };
};

const normalizeSkills = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { skills: asArray<any>(raw.skills) };
};

const normalizeReflections = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { reflections: asArray<any>(raw.reflections) };
};

const normalizeImprovements = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  return { improvements: asArray<any>(raw.improvements) };
};

const normalizePlans = (value: unknown) => asArray<any>(value);
const normalizeHistory = (value: unknown) => asArray<any>(value);
const normalizeCounterfactuals = (value: unknown) => asArray<any>(value);
const normalizeConnectionOnboarding = (value: unknown) => {
  const raw = asObject(value, {} as Record<string, unknown>);
  const urls = asObject(raw.urls, {} as Record<string, unknown>);
  const generated = asObject(raw.generated, {} as Record<string, unknown>);
  return {
    auth_mode: typeof raw.auth_mode === "string" ? raw.auth_mode : "unknown",
    oauth_enabled: Boolean(raw.oauth_enabled),
    owner_exists: Boolean(raw.owner_exists),
    recommended_auth: typeof raw.recommended_auth === "string" ? raw.recommended_auth : "oauth",
    urls: {
      dashboard: typeof urls.dashboard === "string" ? urls.dashboard : "/",
      connection_settings: typeof urls.connection_settings === "string" ? urls.connection_settings : "/settings/connection",
      first_run_setup: typeof urls.first_run_setup === "string" ? urls.first_run_setup : "/setup",
      oauth_authorize: typeof urls.oauth_authorize === "string" ? urls.oauth_authorize : "/oauth/authorize",
      mcp_url: typeof urls.mcp_url === "string" ? urls.mcp_url : "/mcp",
    },
    generated: {
      oauth_local: typeof generated.oauth_local === "string" ? generated.oauth_local : "",
      api_key_remote: typeof generated.api_key_remote === "string" ? generated.api_key_remote : "",
    },
    warnings: asArray<{ code: string; message: string; severity: string }>(raw.warnings),
  };
};

// ─── Memory ──────────────────────────────────────────────────────────────────
export const listMemories = async (params?: object) =>
  getNormalized("/memory", normalizeMemories, { params });
export const getMemory = (id: string) => api.get(`/memory/${id}`);
export const createMemory = (data: object) => api.post("/memory", data);
export const updateMemory = (id: string, data: object) => api.patch(`/memory/${id}`, data);
export const deleteMemory = (id: string) => api.delete(`/memory/${id}`);
export const recall = (data: object) => api.post("/events/recall", data);

// ─── Skills ──────────────────────────────────────────────────────────────────
export const listSkills = async (params?: object) =>
  getNormalized("/skills", normalizeSkills, { params });
export const getSkill = (id: string) => api.get(`/skills/${id}`);
export const proposeSkill = (data: object) => api.post("/skills/propose", data);
export const runSkill = (id: string, input?: object) => api.post(`/skills/${id}/run`, { input_data: input });
export const testSkill = (id: string) => api.post(`/skills/${id}/test`);

// ─── Reflections ─────────────────────────────────────────────────────────────
export const listReflections = async (params?: object) =>
  getNormalized("/reflections", normalizeReflections, { params });
export const generateReflection = (params?: object) => api.post("/reflections/generate", params);

// ─── Improvements ────────────────────────────────────────────────────────────
export const listImprovements = async (params?: object) =>
  getNormalized("/improvements", normalizeImprovements, { params });
export const proposeImprovement = (data: object) => api.post("/improvements/propose", data);

// ─── Approvals ───────────────────────────────────────────────────────────────
export const listApprovals = async (params?: object) =>
  getNormalized("/approvals", normalizeApprovals, { params });
export const getApproval = (id: string) => api.get(`/approvals/${id}`);
export const createApproval = (improvement_id: string) =>
  api.post(`/approvals?improvement_id=${improvement_id}`, {});
export const approveRequest = (id: string, note?: string) =>
  api.post(`/approvals/${id}/approve`, { reviewer_note: note });
export const rejectRequest = (id: string, note?: string) =>
  api.post(`/approvals/${id}/reject`, { reviewer_note: note });

// ─── Dashboard ───────────────────────────────────────────────────────────────
export const getDashboard = async () =>
  getNormalized("/dashboard", normalizeDashboard);
export const getHealth = () => api.get("/health");
export const getConnectionOnboarding = async () =>
  getNormalized("/connection/onboarding", normalizeConnectionOnboarding);

// ─── Notifications ───────────────────────────────────────────────────────────
export const listNotifications = async (params?: object) =>
  getNormalized("/notifications", normalizeNotifications, { params });

// ─── Telemetry (P9) ──────────────────────────────────────────────────────────
export const getTelemetrySnapshot = async (project?: string) =>
  getNormalized("/telemetry/snapshot", normalizeTelemetrySnapshot, { params: project ? { project } : {} });
export const computeTelemetrySnapshot = (project?: string) =>
  api.post("/telemetry/snapshot/compute", null, { params: project ? { project } : {} });
export const getMetricHistory = async (name: string, project?: string, limit = 30) =>
  getNormalized(`/telemetry/metrics/${name}/history`, asArray, { params: { limit, ...(project ? { project } : {}) } });
export const getRetrievalStats = async (project?: string, windowHours = 24) =>
  getNormalized("/telemetry/retrieval/stats", normalizeRetrievalStats, { params: { window_hours: windowHours, ...(project ? { project } : {}) } });
export const getRetrievalHeatmap = async (project?: string, windowDays = 30) =>
  getNormalized("/telemetry/retrieval/heatmap", normalizeRetrievalHeatmap, { params: { window_days: windowDays, ...(project ? { project } : {}) } });
export const getProceduralEffectiveness = async (project?: string) =>
  getNormalized("/telemetry/procedural/effectiveness", normalizeProceduralEffectiveness, { params: project ? { project } : {} });
export const getDriftCandidates = async (project?: string) =>
  getNormalized("/telemetry/drift/detect", normalizeDriftCandidates, { params: project ? { project } : {} });
export const applyDriftDecay = (project?: string) =>
  api.post("/telemetry/drift/apply-decay", null, { params: project ? { project } : {} });

// ─── P10: Provider analytics ──────────────────────────────────────────────────
export const getProviderStats = async (project?: string) =>
  getNormalized("/telemetry/providers/stats", normalizeProviderStats, { params: project ? { project } : {} });
export const aggregateProviderStats = (project?: string, windowHours = 48) =>
  api.post("/telemetry/providers/aggregate", null, {
    params: { window_hours: windowHours, ...(project ? { project } : {}) },
  });
export const getProviderDrift = async (project?: string) =>
  getNormalized("/telemetry/providers/drift", normalizeProviderDrift, { params: project ? { project } : {} });

// ─── P13: Simulation ──────────────────────────────────────────────────────────
export const listPlans = async (params?: object) =>
  getNormalized("/simulation/plans", normalizePlans, { params });
export const getPlan = (id: string) => api.get(`/simulation/plans/${id}`);
export const createPlan = (data: object) => api.post("/simulation/plans", data);
export const approvePlan = (id: string) => api.post(`/simulation/plans/${id}/approve`);
export const rejectPlan = (id: string, reason = "") =>
  api.post(`/simulation/plans/${id}/reject`, { reason });
export const runSimulation = (planId: string, params?: object) =>
  api.post(`/simulation/plans/${planId}/simulate`, params || {});
export const listSimulations = (planId: string) =>
  api.get(`/simulation/plans/${planId}/simulations`);
export const runCounterfactual = (planId: string, data: object) =>
  api.post(`/simulation/plans/${planId}/counterfactual`, data);
export const listCounterfactuals = async (planId: string) =>
  getNormalized(`/simulation/plans/${planId}/counterfactuals`, normalizeCounterfactuals);
export const estimateRisk = (planId: string) =>
  api.post(`/simulation/plans/${planId}/risk`);
export const computeCalibration = (params?: object) =>
  api.post("/simulation/calibration/compute", null, { params: params || {} });
export const getCalibrationHistory = async (params?: object) =>
  getNormalized("/simulation/calibration/history", normalizeHistory, { params: params || {} });
export const recordSimOutcome = (runId: string, actual_outcome: string) =>
  api.post(`/simulation/simulations/${runId}/outcome`, { actual_outcome });

// Projects API
export const getProjects = () =>
  axios.get("/api/projects", { headers: { "X-API-Key": "local-dev-key" } });
export const getProject = (slug: string) =>
  axios.get(`/api/projects/${encodeURIComponent(slug)}`, { headers: { "X-API-Key": "local-dev-key" } });

// Doctor (unauthenticated)
export const getDoctor = () => axios.get("/api/system/doctor");

export default api;
