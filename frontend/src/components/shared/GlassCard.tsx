import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import React from "react";

interface GlassCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function GlassCard({ className, children, ...props }: GlassCardProps) {
  return (
    <Card 
      className={cn(
        "glass rounded-3xl transition-all hover:bg-white/10",
        className
      )} 
      {...props}
    >
      {children}
    </Card>
  );
}

// Re-export the Shadcn components so we can import them from this file
export { CardHeader, CardTitle, CardDescription, CardContent, CardFooter };
