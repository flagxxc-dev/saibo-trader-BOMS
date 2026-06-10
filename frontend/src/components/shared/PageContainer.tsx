import React from "react";
import { cn } from "@/lib/utils";

interface PageContainerProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export function PageContainer({ className, children, ...props }: PageContainerProps) {
  return (
    <div className={cn("space-y-6 max-w-5xl relative z-10 w-full", className)} {...props}>
      {children}
    </div>
  );
}
