"use client";

import { signIn } from "next-auth/react";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { GlassCard, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/shared/GlassCard";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { APP_NAME } from "@/lib/branding";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    const res = await signIn("credentials", {
      redirect: false,
      email,
      password,
    });

    if (res?.error) {
      setError("账号或密码错误");
      setLoading(false);
    } else {
      router.push("/dashboard");
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen text-white selection:bg-white/20 relative">
      <div className="mesh-bg" />
      <GlassCard className="w-full max-w-md z-10 p-4 shadow-2xl">
        <CardHeader className="space-y-1">
          <CardTitle className="text-3xl font-bold text-center text-white drop-shadow-sm">{APP_NAME}</CardTitle>
          <CardDescription className="text-center text-white/70">
            输入账号和密码，登录交易控制台
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit}>
          <CardContent className="space-y-4">
            {error && (
              <div className="p-3 text-sm text-red-500 bg-red-500/10 rounded-md">{error}</div>
            )}
            <div className="space-y-2">
              <Label htmlFor="email">账号</Label>
              <Input
                id="email"
                type="text"
                placeholder="admin"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="bg-white/5 border-white/10 text-white focus-visible:ring-white/50 rounded-xl"
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">密码</Label>
                <button
                  type="button"
                  onClick={() => router.push("/forgot-password")}
                  className="text-xs text-muted-foreground hover:underline"
                >
                  忘记密码？
                </button>
              </div>
              <Input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="bg-white/5 border-white/10 text-white focus-visible:ring-white/50 rounded-xl"
              />
            </div>
          </CardContent>
          <CardFooter>
            <Button
              type="submit"
              variant="glass"
              className="w-full h-12 font-extrabold tracking-tight rounded-2xl text-lg"
              disabled={loading}
            >
              {loading ? "登录中..." : "登录"}
            </Button>
          </CardFooter>
        </form>
      </GlassCard>
    </div>
  );
}
