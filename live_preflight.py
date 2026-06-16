#!/usr/bin/env python3
"""Startup self-check: mode, wallet, EIP-712 params, fee model, first-live checklist."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from polymarket_fees import (
    compare_fee_models,
    fetch_clob_market,
    fetch_token_fee_rate_bps,
    parse_token_fees,
    sample_updown_market,
)

PREFLIGHT_PATH = Path(os.getenv("PREFLIGHT_PATH", "logs/preflight.json"))

# Must match trading-core/src/main.cpp + EIP712Signer.cpp
CPP_EIP712 = {
    "domain_name": "Polymarket CTF Exchange",
    "domain_version": "2",
    "chain_id": 137,
    "exchange_v2": "0xE111180000d2663C0091e4f400237545B87B996B",
    "neg_risk_exchange_v2": "0xe2222d279d744050d28e00520010520000310F59",
    "order_type": (
        "Order(uint256 salt,address maker,address signer,uint256 tokenId,"
        "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
        "uint256 timestamp,bytes32 metadata,bytes32 builder)"
    ),
    "signed_fields_note": "expiration/taker/feeRateBps 不在 V2 EIP-712 签名 struct 内",
    "post_order_type": "FAK",
}

CHECKLIST_LIVE_DH = [
    "日志出现 Starting Core ... Mode: LIVE",
    "出现 [LIVE DH] Sequential dual-leg",
    "两次 [LIVE EXEC] Response 且 size_matched > 0",
    "出现 [LIVE DH] OPENED",
    "Polymarket 网页持仓与 bot 一致",
    "若 Order REJECTED + invalid signature → 停 bot，查 EIP-712",
    "若 NO leg failed → 检查深度；CRITICAL unwind FAILED → 人工处理",
]

CHECKLIST_LIVE_LIH = [
    "日志出现 Mode: LIVE | LIH dry-run: on",
    "出现 [LIVE LIH SHADOW] LEG1 / HEDGE / SCALE（验簿通过、未发单）",
    "运行 python prelive_lih_check.py — 确认无同 slot 重复 LEG1",
    "确认 LIVE_LIH_DRY_RUN=true 后再观察 shadow 日志至少数小时",
    "若要真下单：prelive 通过 + 显式设 LIVE_LIH_DRY_RUN=false 并小仓",
    "单边到期需 CLOB winner 结算；AUTO_REDEEM 自动赎回",
    "若 [LIH] CRITICAL unwind FAILED → 人工处理",
]


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("false", "0", "no", "off"):
        return False
    if raw in ("true", "1", "yes", "on"):
        return True
    return default


def _mask(s: str, show: int = 6) -> str:
    s = (s or "").strip()
    if len(s) <= show * 2:
        return "***"
    return f"{s[:show]}...{s[-4:]}"


def run_preflight() -> dict:
    load_dotenv()
    paper = _env_bool("PAPER_MODE", True)
    lih = _env_bool("LIH_ENABLED", True)
    live_lih_dry = _env_bool("LIVE_LIH_DRY_RUN", True)
    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    signer = os.getenv("POLYMARKET_SIGNER", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    if not signer and funder:
        signer = funder
    if not funder and signer:
        funder = signer
    is_proxy = bool(funder and signer and funder.lower() != signer.lower())
    sig_type = 1 if is_proxy else 0
    fee_flat = float(os.getenv("FEE_RATE", "0.018") or "0.018")

    report: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "paper" if paper else "live",
        "paper_mode": paper,
        "wallet": {
            "funder": funder,
            "signer": signer,
            "signature_type": sig_type,
            "is_proxy": is_proxy,
            "private_key_set": bool(pk and "Your" not in pk and len(pk) > 20),
        },
        "eip712_cpp": CPP_EIP712,
        "env_mapping": {
            "POLYMARKET_CHAIN_ID": os.getenv("POLYMARKET_CHAIN_ID", "137"),
            "POLYMARKET_HOST": os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com"),
            "maker_address": funder,
            "signer_address": signer,
            "signatureType": sig_type,
            "verifyingContract_standard": CPP_EIP712["exchange_v2"],
            "verifyingContract_neg_risk_updown": CPP_EIP712["neg_risk_exchange_v2"],
        },
        "fee_model": {
            "env_FEE_RATE_flat": fee_flat,
            "v2_order_json_includes_feeRateBps": False,
            "note": "V2 下单 JSON/签名不含 feeRateBps；费率由 CLOB 市场 fd.r/fd.e 曲线计算，纸面已改用动态模型",
            "simulation": "polymarket_v2_curve_with_flat_fallback",
        },
        "api_keys": {
            "POLY_API_KEY_set": bool(os.getenv("POLY_API_KEY", "").strip()),
        },
        "checks": [],
        "live_first_order_checklist": [] if paper else (CHECKLIST_LIVE_LIH if lih else CHECKLIST_LIVE_DH),
        "warnings": [],
        "ok": True,
    }

    def check(name: str, passed: bool, detail: str = ""):
        report["checks"].append({"name": name, "ok": passed, "detail": detail})
        if not passed:
            report["ok"] = False

    check("PAPER_MODE", True, f"{'纸面' if paper else '实盘'} (PAPER_MODE={'true' if paper else 'false'})")

    if not paper:
        check("POLYMARKET_PRIVATE_KEY", report["wallet"]["private_key_set"], "实盘需要有效私钥")
        check("POLYMARKET_FUNDER", bool(funder), "实盘需要 funder 地址")
        check("POLY_API_KEY", report["api_keys"]["POLY_API_KEY_set"], "运行 derive_and_update_keys.py 或手动配置")
        if lih:
            check(
                "LIVE_LIH_DRY_RUN",
                True,
                "shadow 验簿" if live_lih_dry else "⚠ false — 将发送真实 LIH 订单",
            )

    # CLOB reachability
    host = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com").rstrip("/")
    try:
        import requests

        t = requests.get(f"{host}/time", timeout=8).json()
        check("CLOB /time", True, str(t))
    except Exception as exc:
        check("CLOB /time", False, str(exc))

    # Fee sample from live market
    sample = sample_updown_market()
    if sample and sample.get("condition_id"):
        try:
            cm = fetch_clob_market(sample["condition_id"])
            token_fees = parse_token_fees(cm)
            yes_tid = sample["yes_token_id"]
            tf = token_fees.get(yes_tid) or {"rate": 0.0, "exponent": 0.0}
            yes_p, no_p = 0.47, 0.47
            cmp = compare_fee_models(yes_p, no_p, tf["rate"], tf["exponent"], fee_flat)
            report["fee_model"]["sample_market"] = sample.get("question", "")[:80]
            report["fee_model"]["sample"] = cmp
            try:
                bps = fetch_token_fee_rate_bps(yes_tid)
                report["fee_model"]["legacy_fee_rate_bps"] = bps
            except Exception:
                pass
            check(
                "fee_api",
                True,
                f"动态≈{cmp['dynamic_fee_per_share']:.4f}/份 vs 扁平≈{cmp['flat_fee_per_share']:.4f}/份 (@0.47+0.47)",
            )
        except Exception as exc:
            report["warnings"].append(f"fee sample failed: {exc}")
            check("fee_api", False, str(exc))
    else:
        report["warnings"].append("未找到样本 Up/Down 市场，跳过动态费率采样")

    if not paper and lih and not live_lih_dry:
        report["warnings"].append("LIVE_LIH_DRY_RUN=false — LIH 将发送真实 CLOB 订单")

    if not paper and lih:
        try:
            from prelive_lih_check import run_prelive_check

            prelive = run_prelive_check(require_shadow=live_lih_dry, since_baseline=not live_lih_dry)
            report["prelive_lih"] = prelive
            # Shadow 模式：prelive 仅作参考，不阻断启动（多盘 btc|5m 会被误报为重复）
            prelive_ok = prelive.get("ok", False) if not live_lih_dry else True
            dup_n = len(prelive.get("duplicate_slots", []))
            check(
                "prelive_lih",
                prelive_ok,
                f"LEG1={prelive.get('leg1_count', 0)} dup_slots={dup_n}"
                + (" (shadow 参考，不阻断)" if live_lih_dry and dup_n else ""),
            )
            if prelive.get("duplicate_slots"):
                msg = "prelive: 日志中存在同 slot 重复 LEG1 — 修 bug 前不要真下单"
                if live_lih_dry:
                    report["warnings"].append(msg + "（shadow 下请人工核对是否为不同 5m 盘）")
                else:
                    report["warnings"].append(msg)
        except Exception as exc:
            report["warnings"].append(f"prelive_lih_check 跳过: {exc}")

    if not paper:
        report["warnings"].append("C++ EIP-712 未与 SDK 自动对照；首单请看 live_first_order_checklist")

    return report


def print_report(report: dict) -> None:
    mode = report["mode"].upper()
    print("=" * 60)
    print(f"  BOT 启动自检  |  模式: {mode}  |  {report['ts']}")
    print("=" * 60)

    w = report["wallet"]
    print("\n[钱包 / 签名]")
    print(f"  Funder  : {w.get('funder') or '(未设置)'}")
    print(f"  Signer  : {w.get('signer') or '(未设置)'}")
    print(f"  SigType : {w.get('signature_type')} ({'Proxy' if w.get('is_proxy') else 'EOA'})")
    print(f"  私钥    : {'已配置' if w.get('private_key_set') else '未配置'}")

    print("\n[EIP-712 ↔ C++ 对照]")
    e = report["eip712_cpp"]
    m = report["env_mapping"]
    print(f"  Domain  : name={e['domain_name']!r} version={e['domain_version']} chainId={e['chain_id']}")
    print(f"  Order   : {e['order_type'][:70]}...")
    print(f"  说明    : {e['signed_fields_note']}")
    print(f"  Maker   : {m.get('maker_address')}")
    print(f"  Signer  : {m.get('signer_address')}")
    print(f"  标准合约: {e['exchange_v2']}")
    print(f"  NegRisk : {e['neg_risk_exchange_v2']}  (5m/15m Up-Down 用这个签名)")
    print(f"  下单类型: {e['post_order_type']}")

    print("\n[手续费模型]")
    fm = report["fee_model"]
    print(f"  .env FEE_RATE (旧扁平): {float(fm['env_FEE_RATE_flat'])*100:.2f}% × 合价")
    print(f"  V2 JSON 含 feeRateBps : {fm['v2_order_json_includes_feeRateBps']}  (不能乱加，会破坏 V2 签名)")
    print(f"  纸面/信号模拟         : {fm['simulation']}")
    if "sample" in fm:
        s = fm["sample"]
        print(f"  样本市场              : {fm.get('sample_market', '')}")
        print(
            f"  @合价0.94: 动态费≈{s['dynamic_fee_per_share']:.4f}/份 "
            f"扁平≈{s['flat_fee_per_share']:.4f}/份 | "
            f"净折价 动态{s['discount_dynamic_pct']:.2f}% vs 扁平{s['discount_flat_pct']:.2f}%"
        )
    if fm.get("legacy_fee_rate_bps") is not None:
        print(f"  /fee-rate base_fee    : {fm['legacy_fee_rate_bps']} bps (V1 信息，V2 曲线用 fd.r/e)")

    print("\n[检查项]")
    for c in report["checks"]:
        mark = "OK" if c["ok"] else "FAIL"
        line = f"  [{mark}] {c['name']}"
        if c.get("detail"):
            line += f" — {c['detail']}"
        print(line)

    if report.get("warnings"):
        print("\n[提示]")
        for w in report["warnings"]:
            print(f"  • {w}")

    if report.get("live_first_order_checklist"):
        print("\n[首单验签清单 — 实盘]")
        for i, line in enumerate(report["live_first_order_checklist"], 1):
            print(f"  {i}. {line}")

    print("\n" + ("✅ 自检通过" if report["ok"] else "⚠️  自检有问题，请处理后再跑") + "\n")


def main() -> int:
    report = run_preflight()
    PREFLIGHT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFLIGHT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    if not report["paper_mode"] and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
