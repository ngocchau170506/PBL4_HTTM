import { Component } from "react";
import { AlertTriangle, Home, RotateCcw } from "lucide-react";

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("[UI ErrorBoundary]", error, errorInfo);
  }

  handleReload = () => {
    window.location.reload();
  };

  handleGoHome = () => {
    this.setState({ hasError: false, error: null });
    window.location.href = "/";
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div className="bg-brand-dark text-brand-text-primary flex min-h-screen items-center justify-center p-6">
        <div className="card-glass border-brand-border w-full max-w-lg rounded-3xl border p-8 text-center shadow-2xl">
          <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-red-500/30 bg-red-500/15 text-red-400">
            <AlertTriangle size={32} />
          </div>
          <h1 className="text-xl font-black tracking-tight">
            Đã xảy ra lỗi hiển thị giao diện
          </h1>
          <p className="text-brand-text-muted mt-2 text-sm">
            Một thành phần trên màn hình vừa gặp sự cố ngoài dự kiến. Bạn có thể
            tải lại trang hoặc quay về Bảng điều hành.
          </p>
          {this.state.error?.message && (
            <div className="border-brand-border-dark bg-brand-dark/70 mt-4 rounded-xl border px-4 py-3 text-left font-mono text-xs break-all text-red-300">
              {this.state.error.message}
            </div>
          )}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            <button
              type="button"
              onClick={this.handleReload}
              className="bg-brand-blue hover:bg-brand-blue-dark inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-bold text-white transition"
            >
              <RotateCcw size={16} /> Tải lại trang
            </button>
            <button
              type="button"
              onClick={this.handleGoHome}
              className="border-brand-border bg-brand-surface hover:bg-brand-surface/80 inline-flex items-center gap-2 rounded-xl border px-5 py-2.5 text-sm font-bold transition"
            >
              <Home size={16} /> Về trang chủ
            </button>
          </div>
        </div>
      </div>
    );
  }
}
