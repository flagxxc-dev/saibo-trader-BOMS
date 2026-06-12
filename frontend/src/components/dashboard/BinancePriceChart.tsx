"use client";

import { useEffect, useRef } from "react";
import { GlassCard, CardContent, CardHeader, CardTitle } from "@/components/shared/GlassCard";
import { createChart, ISeriesApi, LineSeries } from "lightweight-charts";
import { LineChart } from "lucide-react";

interface BinancePriceChartProps {
  btcPrice: number;
  ethPrice: number;
  solPrice: number;
  timestamp: number;
}

export function BinancePriceChart({ btcPrice, ethPrice, solPrice, timestamp }: BinancePriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const lineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const ethSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const solSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current || chartContainerRef.current.clientWidth === 0) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: "transparent" },
        textColor: "rgba(255,255,255,0.3)",
        fontFamily: "var(--font-jetbrains-mono)",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.02)" },
        horzLines: { color: "rgba(255, 255, 255, 0.02)" },
      },
      rightPriceScale: { borderColor: "rgba(255, 255, 255, 0.05)" },
      timeScale: { borderColor: "rgba(255, 255, 255, 0.05)" },
      width: chartContainerRef.current.clientWidth,
      height: 280,
    });

    lineSeriesRef.current = chart.addSeries(LineSeries, { color: "#818cf8", lineWidth: 2, title: "BTC" });
    ethSeriesRef.current = chart.addSeries(LineSeries, { color: "#6366f1", lineWidth: 2, title: "ETH" });
    solSeriesRef.current = chart.addSeries(LineSeries, { color: "#14b8a6", lineWidth: 2, title: "SOL" });

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (!timestamp || !lineSeriesRef.current) return;
    const time = Math.floor(timestamp / 1000) as never;
    if (btcPrice > 0) lineSeriesRef.current.update({ time, value: btcPrice });
    if (ethPrice > 0) ethSeriesRef.current?.update({ time, value: ethPrice });
    if (solPrice > 0) solSeriesRef.current?.update({ time, value: solPrice });
  }, [timestamp, btcPrice, ethPrice, solPrice]);

  return (
    <GlassCard>
      <CardHeader>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient flex items-center gap-2">
            <LineChart className="h-4 w-4" />
            现货走势
          </CardTitle>
          <div className="flex gap-4 text-[11px] font-mono text-white/40">
            <span>BTC <span className="text-indigo-300">{btcPrice > 0 ? btcPrice.toFixed(2) : "—"}</span></span>
            <span>ETH <span className="text-indigo-400">{ethPrice > 0 ? ethPrice.toFixed(2) : "—"}</span></span>
            <span>SOL <span className="text-teal-400">{solPrice > 0 ? solPrice.toFixed(2) : "—"}</span></span>
          </div>
        </div>
        <p className="text-[11px] text-muted-foreground mt-2">仪表盘展示用，与 DH 开仓逻辑无关。</p>
      </CardHeader>
      <CardContent>
        <div ref={chartContainerRef} className="w-full h-[280px]" />
      </CardContent>
    </GlassCard>
  );
}
