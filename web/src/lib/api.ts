import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  headers: { "X-API-Key": "local-dev-key" },
});

// ─── Memory ──────────────────────────────────────────────────────────────────
export const listMemories = (params?: object) => api.get("/memory", { params });
export const getMemory = (id: string) => api.get(`/memory/${id}`);
export const createMemory = (data: object) => api.post("/memory", data);
export const updateMemory = (id: string, data: object) => api.patch(`/memory/${id}`, data);
export const deleteMemory = (id: string) => api.delete(`/memory/${id}`);
export const recall = (data: object) => api.post("/events/recall", data);

// ─── Skills ──────────────────────────────────────────────────────────────────
export const listSkills = (params?: object) => api.get("/skills", { params });
export const getSkill = (id: string) => api.get(`/skills/${id}`);
export const proposeSkill = (data: object) => api.post("/skills/propose", data);
export const runSkill = (id: string, input?: object) => api.post(`/skills/${id}/run`, { input_data: input });
export const testSkill = (id: string) => api.post(`/skills/${id}/test`);

// ─── Reflections ─────────────────────────────────────────────────────────────
export const listReflections = (params?: object) => api.get("/reflections", { params });
export const generateReflection = (params?: object) => api.post("/reflections/generate", params);

// ─── Improvements ────────────────────────────────────────────────────────────
export const listImprovements = (params?: object) => api.get("/improvements", { params });
export const proposeImprovement = (data: object) => api.post("/improvements/propose", data);

// ─── Approvals ───────────────────────────────────────────────────────────────
export const listApprovals = (params?: object) => api.get("/approvals", { params });
export const getApproval = (id: string) => api.get(`/approvals/${id}`);
export const createApproval = (improvement_id: string) =>
  api.post(`/approvals?improvement_id=${improvement_id}`, {});
export const approveRequest = (id: string, note?: string) =>
  api.post(`/approvals/${id}/approve`, { reviewer_note: note });
export const rejectRequest = (id: string, note?: string) =>
  api.post(`/approvals/${id}/reject`, { reviewer_note: note });

// ─── Dashboard ───────────────────────────────────────────────────────────────
export const getDashboard = () => api.get("/dashboard");
export const getHealth = () => api.get("/health");

// ─── Telemetry (P9) ──────────────────────────────────────────────────────────
export const getTelemetrySnapshot = (project?: string) =>
  api.get("/telemetry/snapshot", { params: project ? { project } : {} });
export const computeTelemetrySnapshot = (project?: string) =>
  api.post("/telemetry/snapshot/compute", null, { params: project ? { project } : {} });
export const getMetricHistory = (name: string, project?: string, limit = 30) =>
  api.get(`/telemetry/metrics/${name}/history`, { params: { limit, ...(project ? { project } : {}) } });
export const getRetrievalStats = (project?: string, windowHours = 24) =>
  api.get("/telemetry/retrieval/stats", { params: { window_hours: windowHours, ...(project ? { project } : {}) } });
export const getRetrievalHeatmap = (project?: string, windowDays = 30) =>
  api.get("/telemetry/retrieval/heatmap", { params: { window_days: windowDays, ...(project ? { project } : {}) } });
export const getProceduralEffectiveness = (project?: string) =>
  api.get("/telemetry/procedural/effectiveness", { params: project ? { project } : {} });
export const getDriftCandidates = (project?: string) =>
  api.get("/telemetry/drift/detect", { params: project ? { project } : {} });
export const applyDriftDecay = (project?: string) =>
  api.post("/telemetry/drift/apply-decay", null, { params: project ? { project } : {} });

// ─── P10: Provider analytics ──────────────────────────────────────────────────
export const getProviderStats = (project?: string) =>
  api.get("/telemetry/providers/stats", { params: project ? { project } : {} });
export const aggregateProviderStats = (project?: string, windowHours = 48) =>
  api.post("/telemetry/providers/aggregate", null, {
    params: { window_hours: windowHours, ...(project ? { project } : {}) },
  });
export const getProviderDrift = (project?: string) =>
  api.get("/telemetry/providers/drift", { params: project ? { project } : {} });

// ─── P13: Simulation ──────────────────────────────────────────────────────────
export const listPlans = (params?: object) => api.get("/simulation/plans", { params });
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
export const listCounterfactuals = (planId: string) =>
  api.get(`/simulation/plans/${planId}/counterfactuals`);
export const estimateRisk = (planId: string) =>
  api.post(`/simulation/plans/${planId}/risk`);
export const computeCalibration = (params?: object) =>
  api.post("/simulation/calibration/compute", null, { params: params || {} });
export const getCalibrationHistory = (params?: object) =>
  api.get("/simulation/calibration/history", { params: params || {} });
export const recordSimOutcome = (runId: string, actual_outcome: string) =>
  api.post(`/simulation/simulations/${runId}/outcome`, { actual_outcome });

export default api;
