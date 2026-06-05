from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from market_data import LiveMarketDataService
from pricing_agent import AgentInputs, QuestPricingAgent


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "catalog.json"
AUDIT_LOG_FILE = BASE_DIR / "data" / "price_audit_log.jsonl"
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)

IS_VERCEL = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))
VERCEL_LIVE_LOOKUP_ENABLED = os.getenv("QUEST_ENABLE_VERCEL_LIVE", "").strip().lower() in {"1", "true", "yes"}


def load_catalog() -> dict[str, Any]:
    with DATA_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_catalog(catalog: dict[str, Any]) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=2)
        handle.write("\n")


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    catalog_data = load_catalog()
    agent = QuestPricingAgent(catalog_data)
    market_data = LiveMarketDataService()
    latest_analysis_by_sku: dict[str, dict[str, Any]] = {}

    def find_catalog_product(sku: str) -> dict[str, Any] | None:
        return agent.find_product(sku)

    def write_audit_entry(entry: dict[str, Any]) -> None:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def persistence_enabled() -> bool:
        return not IS_VERCEL

    def live_catalog_run_enabled() -> bool:
        return not IS_VERCEL or VERCEL_LIVE_LOOKUP_ENABLED

    def summarize_analysis_for_catalog(result: dict[str, Any]) -> dict[str, Any]:
        calc = result.get("calculation", {})
        product = result.get("product") or {}
        competitors = competitors_with_required_amazon(product, result.get("competitors", []))[:6]
        return {
            "recommended_price": calc.get("recommended_price"),
            "risk": calc.get("risk"),
            "route": calc.get("route"),
            "market_data": result.get("market_data", {}),
            "competitors": [
                {
                    "source": item.get("source"),
                    "price": item.get("price"),
                    "normalized_price": item.get("normalized_price", item.get("price")),
                    "normalized_uom": item.get("normalized_uom"),
                    "title": item.get("title"),
                    "offer_sku": item.get("offer_sku"),
                    "image": item.get("image"),
                    "stock": item.get("stock"),
                    "match_score": item.get("match_score"),
                }
                for item in competitors
            ],
        }

    def competitors_with_required_amazon(
        product: dict[str, Any] | None,
        competitors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows = [dict(item) for item in competitors]
        if product and not any(is_amazon_source(row) for row in rows):
            amazon_row = catalog_amazon_competitor(product)
            if amazon_row:
                rows.append(amazon_row)

        rows.sort(
            key=lambda row: (
                0 if is_amazon_source(row) else 1,
                -float(row.get("match_score", 0) or 0),
                float(row.get("normalized_price", row.get("price", 0)) or 0),
            )
        )
        return rows

    def is_amazon_source(item: dict[str, Any]) -> bool:
        return "amazon" in str(item.get("source", "")).lower()

    def catalog_amazon_competitor(product: dict[str, Any]) -> dict[str, Any] | None:
        for item in product.get("competitors", []):
            if not is_amazon_source(item):
                continue
            row = dict(item)
            row.setdefault("title", product.get("product", ""))
            row.setdefault("offer_sku", product.get("mpn") or product.get("sku", ""))
            row.setdefault("normalized_price", row.get("price"))
            row.setdefault("normalized_uom", product.get("uom", "EA"))
            row.setdefault("package_quantity", 1)
            row.setdefault("package_type", str(product.get("uom", "EA")).lower())
            row.setdefault("comparable", True)
            row["data_source"] = row.get("data_source") or "required_amazon_catalog"
            return row
        return None

    def catalog_summary_with_latest_analysis() -> dict[str, Any]:
        summary = agent.catalog_summary()
        products = []
        for product in summary.get("products", []):
            row = dict(product)
            latest = latest_analysis_by_sku.get(str(product.get("sku", "")))
            if latest:
                row["latest_analysis"] = latest
            products.append(row)
        summary["products"] = products
        return summary

    def apply_price_update(
        sku: str,
        new_price: float,
        decision: str,
        actor: str,
        note: str,
        source_route: str,
        risk: str,
    ) -> dict[str, Any]:
        product = find_catalog_product(sku)
        if not product:
            raise KeyError(f"Unknown product: {sku}")

        previous_price = float(product.get("current_price", 0))
        rounded_price = round(float(new_price), 2)
        product["current_price"] = rounded_price
        if persistence_enabled():
            save_catalog(catalog_data)

        audit_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sku": product.get("sku", sku),
            "mpn": product.get("mpn", ""),
            "product": product.get("product", ""),
            "decision": decision,
            "actor": actor,
            "note": note,
            "route": source_route,
            "risk": risk,
            "previous_price": previous_price,
            "updated_price": rounded_price,
        }
        if persistence_enabled():
            write_audit_entry(audit_entry)
        return {
            "status": "applied",
            "decision": decision,
            "previous_price": previous_price,
            "updated_price": rounded_price,
            "product": product,
            "audit": audit_entry,
        }

    def build_analysis_result(
        sku: str,
        strategy: str,
        demand_units: int | None = None,
        stock_override: int | None = None,
    ) -> dict[str, Any]:
        product = agent.find_product(sku)
        competitor_override = []
        market_override_applied = True
        excluded_competitors = []
        live_candidate_count = 0
        market_mode = "live_requested"
        market_provider = "live lookup"
        market_message = "Live competitor lookup is running."
        use_live_lookup = live_catalog_run_enabled()

        if use_live_lookup:
            live_result = market_data.fetch_for_product(sku, product)
            market_provider = live_result.provider
            market_message = live_result.message
            excluded_competitors = live_result.rejected_observations
            live_candidate_count = len(live_result.observations)
            competitor_override = competitors_with_required_amazon(product, live_result.observations)
            market_mode = live_result.status
        else:
            competitor_override = list((product or {}).get("competitors", []))
            market_override_applied = False
            market_mode = "sample"
            market_provider = "Quest catalog samples"
            market_message = (
                "Vercel deployment is using stored competitor samples for catalog-wide runs. "
                "Live 22-SKU sweeps and file-based auto-publish are not reliable inside a serverless request."
            )

        result = agent.analyze(
            AgentInputs(
                sku=sku,
                strategy=strategy,
                demand_units=demand_units,
                stock_override=stock_override,
                competitor_override=competitor_override,
                market_override_applied=market_override_applied,
                excluded_competitors=excluded_competitors,
                live_candidate_count=live_candidate_count,
                market_mode=market_mode,
                market_provider=market_provider,
                market_message=market_message,
            )
        )

        if result.get("product") is not None:
            calc = result.get("calculation", {})
            route = str(calc.get("route", ""))
            risk = str(calc.get("risk", ""))
            recommended_price = calc.get("recommended_price")

            if route == "Auto approve" and isinstance(recommended_price, (int, float)):
                if persistence_enabled():
                    publish = apply_price_update(
                        sku=result["product"]["sku"],
                        new_price=float(recommended_price),
                        decision="auto_approve",
                        actor="Quest Pricing Agent",
                        note="Low-risk recommendation auto-applied.",
                        source_route=route,
                        risk=risk,
                    )
                    result["product"] = publish["product"]
                    result["publish"] = {
                        "status": "auto_applied",
                        "message": "Low-risk recommendation was automatically applied to the Quest pricing dataset.",
                        "previous_price": publish["previous_price"],
                        "updated_price": publish["updated_price"],
                    }
                else:
                    result["publish"] = {
                        "status": "auto_applied",
                        "message": "Low-risk recommendation was auto-approved in preview mode. Vercel deployment cannot persist catalog file updates.",
                        "previous_price": calc.get("current_price"),
                        "updated_price": float(recommended_price),
                    }
            else:
                result["publish"] = {
                    "status": "pending_review",
                    "message": "Human review is required before publishing this price.",
                    "allowed_actions": ["approve", "reject", "modify"],
                }

            latest_analysis_by_sku[result["product"]["sku"]] = summarize_analysis_for_catalog(result)

        return result

    def no_cache(response):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    def uncached_page(filename: str):
        return no_cache(send_from_directory(BASE_DIR, filename, max_age=0, conditional=False))

    def page(filename: str):
        return uncached_page(filename)

    @app.get("/")
    def index():
        return page("index.html")

    @app.get("/analyzer")
    def analyzer_page():
        return page("analyzer.html")

    @app.get("/catalog")
    def catalog_page():
        return page("catalog.html")

    @app.get("/competitor")
    def competitor_page():
        return page("competitor.html")

    @app.get("/styles.css")
    def styles():
        return uncached_page("styles.css")

    @app.get("/app.js")
    def frontend_app():
        return uncached_page("app.js")

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        return send_from_directory(BASE_DIR / "assets", filename)

    @app.get("/docs/<path:filename>")
    def docs(filename: str):
        return send_from_directory(BASE_DIR / "docs", filename)

    @app.get("/api/catalog")
    def catalog():
        return jsonify(catalog_summary_with_latest_analysis())

    @app.post("/api/analyze")
    def analyze():
        payload = request.get_json(silent=True) or {}
        stock_override = payload.get("stock_override")
        if stock_override in ("", None):
            stock_override = None
        else:
            try:
                stock_override = int(stock_override)
            except (TypeError, ValueError):
                return jsonify({"error": "stock_override must be a whole number"}), 400

        sku = str(payload.get("sku", "")).strip()
        if not sku:
            return jsonify({"error": "sku is required"}), 400

        result = build_analysis_result(
            sku=sku,
            strategy=str(payload.get("strategy", "balanced")).strip().lower(),
            demand_units=(
                int(payload.get("demand_units"))
                if str(payload.get("demand_units", "")).strip() not in {"", "None"}
                else None
            ),
            stock_override=stock_override,
        )
        status = 404 if result.get("product") is None else 200
        return jsonify(result), status

    @app.post("/api/analyze-catalog")
    def analyze_catalog():
        payload = request.get_json(silent=True) or {}
        strategy = str(payload.get("strategy", "balanced")).strip().lower()
        overrides = payload.get("overrides") or {}

        results = [
            build_analysis_result(
                sku=product.get("sku", ""),
                strategy=str((overrides.get(product.get("sku", "")) or {}).get("strategy", strategy)).strip().lower(),
                demand_units=(
                    int((overrides.get(product.get("sku", "")) or {}).get("demand_units"))
                    if str((overrides.get(product.get("sku", "")) or {}).get("demand_units", "")).strip() not in {"", "None"}
                    else None
                ),
                stock_override=(
                    int((overrides.get(product.get("sku", "")) or {}).get("stock_override"))
                    if str((overrides.get(product.get("sku", "")) or {}).get("stock_override", "")).strip() not in {"", "None"}
                    else None
                ),
            )
            for product in catalog_data.get("products", [])
        ]

        summary = {
            "total": len(results),
            "auto_applied": sum(1 for result in results if result.get("publish", {}).get("status") == "auto_applied"),
            "pending_review": sum(1 for result in results if result.get("publish", {}).get("status") == "pending_review"),
            "not_found": sum(1 for result in results if result.get("product") is None),
            "high_risk": sum(1 for result in results if result.get("calculation", {}).get("risk") == "high"),
            "medium_risk": sum(1 for result in results if result.get("calculation", {}).get("risk") == "medium"),
            "low_risk": sum(1 for result in results if result.get("calculation", {}).get("risk") == "low"),
        }
        return jsonify({"summary": summary, "results": results})

    @app.post("/api/price-action")
    def price_action():
        payload = request.get_json(silent=True) or {}
        sku = str(payload.get("sku", "")).strip()
        action = str(payload.get("action", "")).strip().lower()
        actor = str(payload.get("actor", "Human reviewer")).strip() or "Human reviewer"
        note = str(payload.get("note", "")).strip()
        route = str(payload.get("route", "")).strip()
        risk = str(payload.get("risk", "")).strip()

        if not sku:
            return jsonify({"error": "sku is required"}), 400
        if action not in {"approve", "reject", "modify"}:
            return jsonify({"error": "action must be approve, reject, or modify"}), 400

        product = find_catalog_product(sku)
        if product is None:
            return jsonify({"error": "sku not found"}), 404

        if action == "reject":
            audit_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sku": product.get("sku", sku),
                "mpn": product.get("mpn", ""),
                "product": product.get("product", ""),
                "decision": "reject",
                "actor": actor,
                "note": note or "Reviewer rejected the change.",
                "route": route,
                "risk": risk,
                "previous_price": float(product.get("current_price", 0)),
                "updated_price": float(product.get("current_price", 0)),
            }
            if persistence_enabled():
                write_audit_entry(audit_entry)
            return jsonify(
                {
                    "status": "rejected",
                    "message": (
                        "Price change was rejected. Current Quest price was left unchanged."
                        if persistence_enabled()
                        else "Price change was rejected in preview mode. Vercel deployment does not persist local file updates."
                    ),
                    "product": product,
                }
            )

        price_value = payload.get("price")
        try:
            new_price = round(float(price_value), 2)
        except (TypeError, ValueError):
            return jsonify({"error": "A valid price is required for approve or modify"}), 400

        decision = "approve" if action == "approve" else "modify"
        publish = apply_price_update(
            sku=sku,
            new_price=new_price,
            decision=decision,
            actor=actor,
            note=note or ("Reviewer approved recommendation." if action == "approve" else "Reviewer modified and applied price."),
            source_route=route,
            risk=risk,
        )
        return jsonify(
            {
                "status": "applied",
                "message": "Quest price was updated successfully.",
                "previous_price": publish["previous_price"],
                "updated_price": publish["updated_price"],
                "product": publish["product"],
            }
        )

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "service": "quest-flask-pricing-api"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
