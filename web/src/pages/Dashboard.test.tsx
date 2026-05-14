// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, beforeEach, afterEach, expect } from "vitest";

import Dashboard from "./Dashboard";
import * as api from "../lib/api";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getDashboard: vi.fn(),
  };
});

const getDashboardMock = vi.mocked(api.getDashboard);

describe("Dashboard", () => {
  beforeEach(() => {
    getDashboardMock.mockReset();
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

    render(<Dashboard />);

    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.getByText("No lessons recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No rollbacks recorded.")).toBeInTheDocument();
  });

  it("renders with empty API response", async () => {
    getDashboardMock.mockResolvedValue({ data: {} } as any);

    render(<Dashboard />);

    await waitFor(() => expect(screen.getAllByText("No lessons recorded yet.")[0]).toBeInTheDocument());
    expect(screen.getAllByText("No rollbacks recorded.")[0]).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("renders auth or API failure as a warning instead of crashing", async () => {
    getDashboardMock.mockRejectedValue({
      response: { status: 401 },
    });

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText("Dashboard warning")).toBeInTheDocument();
    });
    expect(screen.getByText("Authentication failed while loading the dashboard.")).toBeInTheDocument();
  });
});
