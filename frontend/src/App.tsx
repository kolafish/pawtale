import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { authApi } from './api';
import { useAppStore } from './stores/appStore';
import { Layout } from './components/Layout';
import { ErrorBoundary } from './components/ErrorBoundary';
import { AuthView } from './components/AuthView';
import type { User } from './types';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60, // 1 minute
      retry: 1,
    },
  },
});

function AppContent() {
  const [user, setUser] = useState<User | null>(null);
  const [isCheckingSession, setIsCheckingSession] = useState(true);
  const resetApp = useAppStore((state) => state.reset);

  useEffect(() => {
    let isMounted = true;
    authApi.me()
      .then((currentUser) => {
        if (isMounted) setUser(currentUser);
      })
      .catch(() => {
        if (isMounted) setUser(null);
      })
      .finally(() => {
        if (isMounted) setIsCheckingSession(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const handleLogout = async () => {
    await authApi.logout();
    resetApp();
    queryClient.clear();
    setUser(null);
  };

  if (isCheckingSession) {
    return (
      <div className="min-h-screen bg-pink-50 flex items-center justify-center text-gray-500">
        <RefreshCw size={22} className="mr-2 animate-spin" />
        正在检查登录状态...
      </div>
    );
  }

  if (!user) {
    return <AuthView onAuthenticated={setUser} />;
  }

  return <Layout user={user} onLogout={handleLogout} />;
}

function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AppContent />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

export default App;
