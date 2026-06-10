export function DemoBanner({ hint }: { hint?: string }) {
  return (
    <div className="mb-4 rounded-xl border border-violet-500/30 bg-violet-500/10 px-4 py-2.5 text-[13px] text-violet-200">
      <strong className="text-violet-100">演示页面</strong>
      {" — "}
      {hint ??
        "此处控件仅作界面展示，不会同步到 C++ 核心。请修改项目根目录 .env 后重启 bot 容器。"}
    </div>
  );
}
