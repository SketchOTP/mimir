// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, beforeEach, afterEach, expect } from "vitest";

import Dashboard from "./Dashboard";
import * as api from "../lib/api";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getDashboard: vi.fn(),
    getConnectionOnboarding: vi.fn(),
    getProjects: vi.fn().mockResolvedValue({ data: { projects: [], count: 0 } }),
  };
});

const getDashboardMock = vi.mocked(api.getDashboard);
const getConnectionOnboardingMock = vi.mocked(api.getConnectionOnboarding);

describe("Dashboard", () => {
  beforeEach(() => {
    getDashboardMock.mockReset();
    getConnectionOnboardingMock.mockReset();
    getConnectionOnboardingMock.mockResolvedValue({
      data: {
        auth_mode: "single_user",
        oauth_enabled: true,
        owner_exists: true,
        recommended_auth: "oauth",
        urls: {
          dashboard: "/",
          connection_settings: "/settings/connection",
          first_run_setup: "/setup",
          oauth_authorize: "/oauth/authorize",
          mcp_url: "/mcp",
        },
        generated: {
          oauth_local: "{ \"mcpServers\": { \"mimir\": { \"url\": \"http://127.0.0.1:8787/mcp\" } } }",
          api_key_remote: "{ \"mcpServers\": { \"mimir\": { \"url\": \"http://127.0.0.1:8787/mcp\", \"headers\": { \"Authorization\": \"Bearer YOUR_API_KEY\" } } } }",
        },
        warnings: [],
      },
    } as any);
  });

  afterEach(() => {
    cleanup();
  });

  it("renders with missing arrays", async () => {
    getDashboardMock.mockResolvedValue({
      data: {
        memory_count: 3,
        skill_count: 1,
        pending_approvals: 0,
        rollback_events: 0,
      },
    } as any);

    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.getByText("Connect Cursor")).toBeInTheDocument();
    expect(screen.getByText("No lessons recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No rollbacks recorded.")).toBeInTheDocument();
  });

  it("renders with empty API response", async () => {
    getDashboardMock.mockResolvedValue({ data: {} } as any);

    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => expect(screen.getAllByText("No lessons recorded yet.")[0]).toBeInTheDocument());
    expect(screen.getAllByText("No rollbacks recorded.")[0]).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("renders auth or API failure as a warning instead of crashing", async () => {
    getDashboardMock.mockRejectedValue({
      response: { status: 401 },
    });

    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Authentication required — check your API key.")).toBeInTheDocument();
    });
  });
});
