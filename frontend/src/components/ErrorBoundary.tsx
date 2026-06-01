import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('[ErrorBoundary] Caught error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-screen flex items-center justify-center bg-gray-50">
          <div className="max-w-md p-6 bg-white rounded-lg shadow-lg">
            <h1 className="text-xl font-bold text-red-600 mb-4">出错了</h1>
            <p className="text-gray-700 mb-4">应用遇到了一个错误：</p>
            <pre className="bg-gray-100 p-4 rounded text-sm overflow-auto max-h-64">
              {this.state.error?.message}
              {'\n\n'}
              {this.state.error?.stack}
            </pre>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 px-4 py-2 bg-pink-500 text-white rounded hover:bg-pink-600"
            >
              刷新页面
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
