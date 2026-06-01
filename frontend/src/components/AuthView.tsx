import { useState } from 'react';
import { BookOpen, LogIn, RefreshCw, UserPlus } from 'lucide-react';
import { authApi } from '../api';
import type { User } from '../types';
import { getErrorMessage } from '../utils/apiError';

interface AuthViewProps {
  onAuthenticated: (user: User) => void;
}

type AuthMode = 'login' | 'register';

export function AuthView({ onAuthenticated }: AuthViewProps) {
  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('local@pawtale.local');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const result = mode === 'login'
        ? await authApi.login({ email: email.trim(), password })
        : await authApi.register({
          email: email.trim(),
          password,
          display_name: displayName.trim() || undefined,
        });
      onAuthenticated(result.user);
    } catch (err: unknown) {
      setError(getErrorMessage(err, '登录失败，请检查邮箱和密码'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-pink-50 to-purple-50 flex items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-pink-100 bg-white p-6 shadow-sm">
        <div className="mb-6 flex items-center gap-3">
          <img src="/logo.png" alt="Pawtale" className="h-10 w-10 object-contain" />
          <div>
            <h1 className="text-xl font-bold text-gray-900">Pawtale</h1>
            <p className="text-sm text-gray-500">登录后继续写作</p>
          </div>
        </div>

        <div className="mb-5 grid grid-cols-2 rounded-lg bg-gray-100 p-1">
          <button
            type="button"
            onClick={() => setMode('login')}
            className={`flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              mode === 'login' ? 'bg-white text-pink-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <LogIn size={14} />
            登录
          </button>
          <button
            type="button"
            onClick={() => setMode('register')}
            className={`flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              mode === 'register' ? 'bg-white text-pink-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <UserPlus size={14} />
            注册
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4">
          {mode === 'register' && (
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-gray-700">昵称</span>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="本地作者"
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-pink-500"
              />
            </label>
          )}

          <label className="block">
            <span className="mb-1 block text-sm font-medium text-gray-700">邮箱</span>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              autoComplete="email"
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-pink-500"
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-sm font-medium text-gray-700">密码</span>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              placeholder={mode === 'login' ? '输入密码' : '至少 8 个字符'}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-pink-500"
            />
          </label>

          {error && (
            <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting || !email.trim() || !password}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-pink-500 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-pink-600 disabled:opacity-60"
          >
            {isSubmitting ? <RefreshCw size={16} className="animate-spin" /> : <BookOpen size={16} />}
            {mode === 'login' ? '进入写作' : '创建账号'}
          </button>
        </form>
      </div>
    </div>
  );
}
