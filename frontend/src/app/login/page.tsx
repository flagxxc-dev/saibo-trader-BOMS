"use client";

import { signIn } from "next-auth/react";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { APP_NAME } from "@/lib/branding";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    const res = await signIn("credentials", {
      redirect: false,
      username,
      password,
    });

    if (res?.ok) {
      router.push("/dashboard");
      router.refresh();
      return;
    }

    // Blacklisted IPs get HTTP 404 from server — no UI hint.
    if (res?.status === 404) {
      setLoading(false);
      return;
    }

    setError("账号或密码错误");
    setLoading(false);
  };

  return (
    <div className="min-h-screen login-bg text-foreground">
      <div className="mx-auto flex min-h-screen max-w-lg flex-col items-center justify-center p-6">
        <div className="panel w-full p-8 shadow-2xl">
          <div className="mb-8 text-center">
            <p className="font-mono text-[10px] font-bold uppercase tracking-[0.4em] text-muted-foreground">
              Secure Access
            </p>
            <h1 className="mt-3 font-heading text-3xl font-extrabold tracking-tight text-gradient-accent">
              {APP_NAME}
            </h1>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {error}
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="username">账号</Label>
              <Input
                id="username"
                type="text"
                placeholder="账号"
                autoComplete="username"
                required
                maxLength={64}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="h-11 border-border/80 bg-background/80 font-mono"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-11 border-border/80 bg-background/80"
              />
            </div>
            <Button type="submit" variant="glass" className="h-11 w-full font-semibold" disabled={loading}>
              {loading ? "验证中..." : "登录"}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
