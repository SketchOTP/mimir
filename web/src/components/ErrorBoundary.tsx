import React from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = {
    hasError: false,
    message: "",
  };

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      message: error.message || "Unknown UI error",
    };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Mimir UI error", error, info);
  }

  private handleRefresh = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center px-6">
          <div className="max-w-lg w-full rounded-2xl border border-red-900/40 bg-slate-900 p-6 space-y-4">
            <div>
              <h1 className="text-xl font-semibold text-red-300">Mimir UI error</h1>
              <p className="mt-2 text-sm text-slate-300">{this.state.message}</p>
            </div>
            <button
              onClick={this.handleRefresh}
              className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500"
            >
              Refresh
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
