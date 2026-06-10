import React from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: React.ElementType;
}

export function PageHeader({ title, description, icon: Icon, className, ...props }: PageHeaderProps) {
  return (
    <div className={cn("flex flex-col gap-1 mb-4", className)} {...props}>
      <div className="flex items-center gap-3">
        {Icon && (
          <div className="flex items-center justify-center h-8 w-8 rounded-xl bg-white/10 border border-white/10">
            <Icon className="h-4 w-4 text-white/90" />
          </div>
        )}
        <h1 className="text-2xl font-heading font-extrabold tracking-tighter text-gradient leading-tight">
          {title}
        </h1>
      </div>
      {description && (
        <p className="text-white/40 max-w-2xl text-[13px] leading-snug tracking-tight font-medium">
          {description}
        </p>
      )}
    </div>
  );
}
