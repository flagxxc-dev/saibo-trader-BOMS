import React from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: React.ElementType;
}

export function PageHeader({ title, description, icon: Icon, className, ...props }: PageHeaderProps) {
  return (
    <div className={cn("flex flex-col gap-1 mb-5", className)} {...props}>
      <div className="flex items-center gap-3">
        {Icon && (
          <div className="flex items-center justify-center h-9 w-9 rounded-lg bg-amber-500/10 border border-amber-500/20">
            <Icon className="h-4 w-4 text-amber-300/90" />
          </div>
        )}
        <h1 className="text-xl font-heading font-bold tracking-tight text-foreground">
          {title}
        </h1>
      </div>
      {description && (
        <p className="text-muted-foreground max-w-2xl text-sm leading-snug pl-12">
          {description}
        </p>
      )}
    </div>
  );
}
