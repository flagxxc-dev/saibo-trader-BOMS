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
        "stat-card rounded-2xl transition-all",
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
