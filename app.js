const h = React.createElement;

const pageName = document.body.dataset.page || "dashboard";
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const pct = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 });
const appRouteVersion = "20260605";

async function readJsonResponse(response) {
  const text = await response.text();

  try {
    return text ? JSON.parse(text) : {};
  } catch (error) {
    console.error("Server returned HTML/non-JSON:", text);
    throw new Error("Server returned HTML instead of JSON. Check Render logs.");
  }
}

function App() {
  const [catalog, setCatalog] = React.useState(null);
  const [error, setError] = React.useState("");

  const loadCatalog = React.useCallback(async () => {
    try {
      const response = await fetch(`/api/catalog?v=${appRouteVersion}`);
      // const text = await response.text();
      // const data = text ? JSON.parse(text) : null;
      const data = await readJsonResponse(response);
      if (!response.ok) {
        throw new Error(data?.error || `Catalog API failed (${response.status})`);
      }
      setCatalog(data);
      setError("");
    } catch (err) {
      setError(err.message || "Flask backend is not running. Start it with python app.py and refresh.");
    }
  }, []);

  React.useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  return h(
    React.Fragment,
    null,
    h(Header, { active: pageName }),
    error ? h(ErrorPage, { error }) : catalog ? h(PageRouter, { catalog, reloadCatalog: loadCatalog }) : h(LoadingPage),
    h(Footer)
  );
}

function Header({ active }) {
  const links = [
    ["/", "Dashboard", "dashboard"],
    ["/analyzer", "Analyzer", "analyzer"],
    ["/catalog", "Catalog", "catalog"],
    ["/competitor", "Competitor", "competitor"],
  ];

  return h(
    "header",
    { className: "topbar" },
    h(
      "a",
      { className: "brand", href: versionedPath("/") },
      h("span", { className: "brand-mark" }, "AIS"),
      h("span", null, h("strong", null, "American industrial safety product"), h("small", null, "Pricing Agent"))
    ),
    h(
      "nav",
      { className: "nav-links", "aria-label": "Primary" },
      links.map(([href, label, key]) => h("a", { key, href: versionedPath(href), className: active === key ? "active" : "" }, label))
    )
  );
}

function PageRouter({ catalog, reloadCatalog }) {
  if (pageName === "analyzer") return h(AnalyzerPage, { catalog, reloadCatalog });
  if (pageName === "catalog") return h(CatalogPage, { catalog });
  if (pageName === "competitor") return h(CompetitorPage, { catalog });
  return h(DashboardPage, { catalog });
}

function DashboardPage({ catalog }) {
  const products = catalog.products || [];
  const lowStock = products.filter((product) => product.stock <= 15);
  const [showLowStockNames, setShowLowStockNames] = React.useState(false);

  return h(
    "main",
    { className: "page-shell" },
    h(PageTitle, {
      eyebrow: "Dashboard",
      title: "American industrial safety product pricing workspace",
      text: "Run the full catalog pricing agent, auto-apply safe changes, and review the rest.",
    }),
    h(
      "section",
      { className: "metric-grid" },
      h(Metric, { label: "Catalog SKUs", value: products.length }),
      h(Metric, { label: "Market sources", value: catalog.sources.length }),
      h(Metric, {
        label: "Low-stock examples",
        value: lowStock.length,
        clickable: true,
        onClick: () => setShowLowStockNames((value) => !value),
      })
    ),
    h(
      "section",
      { className: "action-grid" },
      h(ActionCard, { href: versionedPath("/analyzer"), title: "Run Full Catalog Analyzer", text: "Check every Quest SKU in one run and publish low-risk price changes automatically." }),
      h(ActionCard, { href: versionedPath("/catalog"), title: "Browse Catalog", text: "Review product-only catalog rows." }),
      h(ActionCard, { href: versionedPath("/competitor"), title: "View Competitors", text: "After an agent run, compare competitor names and prices by SKU." })
    ),
    h(
      "section",
      { className: "panel" },
      h("h2", null, "Low-stock product names"),
      showLowStockNames
        ? h(
            "div",
            { className: "compact-table" },
            lowStock.map((product) =>
              h(
                "div",
                { className: "table-row", key: product.sku },
                h("span", null, product.sku),
                h("strong", null, product.product),
                h("small", null, `${product.stock} ${product.uom} in stock`),
                h("b", null, "Low stock")
              )
            )
          )
        : h("p", null, "Click the low-stock examples metric to show the low-stock product names.")
    )
  );
}

function AnalyzerPage({ catalog, reloadCatalog }) {
  const defaultStrategy = "balanced";
  const [overrides, setOverrides] = React.useState(() => buildOverrides(catalog.products));
  const [payload, setPayload] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    setOverrides(buildOverrides(catalog.products));
  }, [catalog.products]);

  function updateOverride(sku, field, value) {
    setOverrides((current) => ({
      ...current,
      [sku]: {
        ...current[sku],
        [field]: value,
      },
    }));
  }

  async function analyzeCatalog() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/analyze-catalog", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy: defaultStrategy, overrides }),
      });
      // const text = await response.text();
      // const nextPayload = text ? JSON.parse(text) : {};
      const nextPayload = await readJsonResponse(response);
      setPayload(nextPayload);
      await reloadCatalog();
      if (!response.ok) setError(nextPayload.error || "Could not analyze the full catalog.");
    } catch (err) {
      setError(err.message || "Could not reach /api/analyze-catalog.");
    } finally {
      setLoading(false);
    }
  }

  const results = payload?.results || [];
  const summary = payload?.summary || null;
  const autoAppliedItems = results.filter((item) => item.publish?.status === "auto_applied");
  const mediumReviewItems = results.filter(
    (item) => item.publish?.status === "pending_review" && item.calculation?.risk === "medium"
  );
  const highReviewItems = results.filter(
    (item) => item.publish?.status === "pending_review" && item.calculation?.risk === "high"
  );

  return h(
    "main",
    { className: "page-shell" },
    h(PageTitle, {
      eyebrow: "Analyzer",
      title: "Run pricing decisions for the full Quest catalog",
      text: "This analyzes every SKU in the catalog at once. Low-risk changes auto-apply. Higher-risk changes stay in human approval.",
    }),
    h(
      "section",
      { className: "split-grid" },
      h(
        "form",
        {
          className: "panel form-panel",
          onSubmit: (event) => {
            event.preventDefault();
            analyzeCatalog();
          },
        },
        h("h2", null, "Catalog-wide analysis"),
        h("h3", null, "Per-SKU overrides"),
        h("p", { className: "subtle-note" }, "You can change customer demand units or strategy for any SKU before running the full catalog analysis."),
        h(
          "div",
          { className: "matrix-scroll" },
          h(
            "div",
            { className: "sku-matrix" },
            catalog.products.map((product) =>
              h(
                "section",
                { className: "sku-column", key: product.sku },
                h("strong", null, product.sku),
                h("small", null, product.product),
                h(Field, {
                  label: "Strategy",
                  children: h(Select, {
                    value: overrides[product.sku]?.strategy || defaultStrategy,
                    onChange: (value) => updateOverride(product.sku, "strategy", value),
                    options: [
                      ["balanced", "Balanced"],
                      ["protect_margin", "Protect margin"],
                      ["win_share", "Win share"],
                      ["clear_inventory", "Clear inventory"],
                    ],
                  }),
                }),
                h(Field, {
                  label: "Demand",
                  children: h("input", {
                    value: overrides[product.sku]?.demand_units ?? product.stock,
                    inputMode: "numeric",
                    onChange: (event) => updateOverride(product.sku, "demand_units", event.target.value),
                  }),
                })
              )
            )
          )
        ),
        h("button", { className: "primary-button", disabled: loading }, loading ? "Running catalog agent..." : "Run pricing agent for all catalog SKUs"),
        error ? h("p", { className: "error-note" }, error) : null
      ),
      h(BatchSummaryPanel, { summary, results })
    ),
    payload
        ? h(
            React.Fragment,
            null,
            h(AutoAppliedQueue, { items: autoAppliedItems }),
            h(ResultsOverview, { results }),
            h(BulkReviewQueue, { items: mediumReviewItems, reloadCatalog, onUpdate: setPayload, currentPayload: payload }),
            h(ExceptionReviewQueue, { items: highReviewItems, reloadCatalog, onUpdate: setPayload, currentPayload: payload })
          )
      : null
  );
}

function BatchSummaryPanel({ summary, results }) {
  if (!summary) {
    return h(
      "section",
      { className: "panel empty-panel" },
      h("h2", null, "Catalog run preview"),
      h("p", null, "Run the pricing agent once to analyze all products in the Quest catalog.")
    );
  }

  return h(
    "section",
    { className: "panel result-panel" },
    h("div", { className: "result-title" }, h("div", null, h("span", { className: "kicker" }, "Catalog run"), h("h2", null, "Run summary"))),
    h(
      "div",
      { className: "metric-grid small" },
      h(Metric, { label: "Total SKUs", value: summary.total }),
      h(Metric, { label: "Auto-applied", value: summary.auto_applied }),
      h(Metric, { label: "Pending review", value: summary.pending_review }),
      h(Metric, { label: "Low risk", value: summary.low_risk }),
      h(Metric, { label: "Medium risk", value: summary.medium_risk }),
      h(Metric, { label: "High risk", value: summary.high_risk })
    ),
    h(
      "div",
      { className: "publish-panel publish-success" },
      h("h3", null, "Automatic publishing"),
      h("p", null, `${summary.auto_applied} low-risk price changes were automatically written back to the Quest catalog.`)
    ),
    results.some((item) => item.market_data?.pricing_count === 0)
      ? h(
          "div",
          { className: "publish-panel publish-neutral" },
          h("h3", null, "Missing competitors"),
          h("p", null, `${results.filter((item) => item.market_data?.pricing_count === 0).length} SKU(s) did not return comparable live competitors and remain in review.`)
        )
      : null
  );
}

function ResultsOverview({ results }) {
  return h(
    "section",
    { className: "panel" },
    h("h2", null, "Catalog results"),
    h(
      "div",
      { className: "catalog-list" },
      results.map((result) =>
        h(
          "div",
          { className: "catalog-row", key: result.product?.sku || result.input?.sku },
          h("span", null, result.product?.sku || result.input?.sku || "-"),
          h("strong", null, result.product?.product || "Unknown product"),
          h(
            "small",
            null,
            [
              `Risk ${capitalize(result.calculation?.risk || "high")}`,
              `Route ${result.calculation?.route || "Exception review"}`,
              result.market_data?.pricing_count === 0 ? "No competitor found" : `${result.market_data?.pricing_count || 0} live competitor rows`,
            ].join(" | ")
          ),
          h("b", null, formatMoney(result.calculation?.recommended_price))
        )
      )
    )
  );
}

function AutoAppliedQueue({ items }) {
  if (!items.length) {
    return h(
      "section",
      { className: "panel" },
      h("h2", null, "Auto-applied low-risk updates"),
      h("p", null, "No low-risk price changes were auto-applied in the latest catalog run.")
    );
  }

  return h(
    "section",
    { className: "panel" },
    h("h2", null, "Auto-applied low-risk updates"),
    h("p", null, "These low-risk SKUs were automatically updated during the latest run."),
    h(
      "div",
      { className: "catalog-list" },
      items.map((item) => {
        const sku = item.product?.sku || item.input?.sku || "-";
        const name = item.product?.product || "Unknown product";
        const previousPrice = item.publish?.previous_price;
        const updatedPrice = item.publish?.updated_price;
        const changePct =
          typeof previousPrice === "number" && typeof updatedPrice === "number" && previousPrice > 0
            ? (updatedPrice - previousPrice) / previousPrice
            : item.calculation?.price_change_pct;

        return h(
          "div",
          { className: "catalog-row", key: `${sku}-auto-applied` },
          h("span", null, sku),
          h("strong", null, name),
          h(
            "small",
            null,
            [
              `Old ${formatMoney(previousPrice)}`,
              `New ${formatMoney(updatedPrice)}`,
              `Change ${formatPercent(changePct, true)}`,
            ].join(" | ")
          ),
          h("b", null, "Auto updated")
        );
      })
    )
  );
}

function BulkReviewQueue({ items, reloadCatalog, onUpdate, currentPayload }) {
  const [savingAction, setSavingAction] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [error, setError] = React.useState("");

  if (!items.length) {
    return h(
      "section",
      { className: "panel" },
      h("h2", null, "Bulk review queue"),
      h("p", null, "No medium-risk items are waiting for bulk review from the latest catalog run.")
    );
  }

  async function runBulkAction(action) {
    setSavingAction(action);
    setMessage("");
    setError("");
    const nextResults = [...currentPayload.results];

    try {
      for (const item of items) {
        const sku = item.product?.sku || item.input?.sku;
        const price = item.calculation?.recommended_price;
        const response = await fetch("/api/price-action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sku,
            action,
            route: item.calculation?.route,
            risk: item.calculation?.risk,
            note: action === "approve" ? "Bulk review approved." : "Bulk review rejected.",
            price,
          }),
        });
        const data = await readJsonResponse(response);
        if (!response.ok) {
          throw new Error(data.error || `Bulk action failed for ${sku}`);
        }

        const index = nextResults.findIndex((result) => (result.product?.sku || result.input?.sku) === sku);
        if (index >= 0) {
          nextResults[index] = {
            ...nextResults[index],
            product: data.product || nextResults[index].product,
            publish:
              action === "approve"
                ? {
                    status: "applied",
                    message: data.message,
                    previous_price: data.previous_price,
                    updated_price: data.updated_price,
                  }
                : {
                    status: "rejected",
                    message: data.message,
                  },
          };
        }
      }

      await reloadCatalog();
      onUpdate({
        ...currentPayload,
        results: nextResults,
        summary: {
          ...currentPayload.summary,
          pending_review: nextResults.filter((item) => item.publish?.status === "pending_review").length,
        },
      });
      setMessage(action === "approve" ? "All medium-risk items were approved." : "All medium-risk items were rejected.");
    } catch (err) {
      setError(err.message || "Could not complete the bulk review action.");
    } finally {
      setSavingAction("");
    }
  }

  return h(
    "section",
    { className: "panel" },
    h("h2", null, "Bulk review queue"),
    h("p", null, "These medium-risk items can be reviewed and approved all at once."),
    h(
      "div",
      { className: "approval-actions bulk-actions" },
      h(
        "button",
        { type: "button", className: "primary-button compact", disabled: Boolean(savingAction), onClick: () => runBulkAction("approve") },
        savingAction === "approve" ? "Approving..." : "Approve all medium-risk"
      ),
      h(
        "button",
        { type: "button", className: "ghost-button", disabled: Boolean(savingAction), onClick: () => runBulkAction("reject") },
        savingAction === "reject" ? "Rejecting..." : "Reject all medium-risk"
      )
    ),
    message ? h("p", { className: "success-note" }, message) : null,
    error ? h("p", { className: "error-note" }, error) : null,
    h(
      "div",
      { className: "catalog-list" },
      items.map((item) =>
        h(
          "div",
          { className: "catalog-row", key: item.product?.sku || item.input?.sku },
          h("span", null, item.product?.sku || item.input?.sku),
          h("strong", null, item.product?.product || "Unknown product"),
          h(
            "small",
            null,
            [
              `Current ${formatMoney(item.calculation?.current_price)}`,
              `Recommended ${formatMoney(item.calculation?.recommended_price)}`,
              `Change ${formatPercent(item.calculation?.price_change_pct, true)}`,
            ].join(" | ")
          ),
          h("b", null, "Bulk review")
        )
      )
    )
  );
}

function ExceptionReviewQueue({ items, reloadCatalog, onUpdate, currentPayload }) {
  if (!items.length) {
    return h(
      "section",
      { className: "panel" },
      h("h2", null, "Exception review queue"),
      h("p", null, "No high-risk items are waiting for exception review from the latest catalog run.")
    );
  }

  function updateItem(updatedSku, patch) {
    const nextResults = currentPayload.results.map((item) =>
      (item.product?.sku || item.input?.sku) === updatedSku ? { ...item, ...patch } : item
    );
    const nextSummary = {
      ...currentPayload.summary,
      pending_review: nextResults.filter((item) => item.publish?.status === "pending_review").length,
    };
    onUpdate({ ...currentPayload, results: nextResults, summary: nextSummary });
  }

  return h(
    "section",
    { className: "panel" },
    h("h2", null, "Exception review queue"),
    h("p", null, "High-risk items still require individual review."),
    h(
      "div",
      { className: "review-grid" },
      items.map((item) =>
        h(ResultCard, {
          key: item.product?.sku || item.input?.sku,
          result: item,
          reloadCatalog,
          onLocalUpdate: (patch) => updateItem(item.product?.sku || item.input?.sku, patch),
        })
      )
    )
  );
}

function ResultCard({ result, reloadCatalog, onLocalUpdate }) {
  const calc = result.calculation || {};
  const product = result.product || {};

  return h(
    "article",
    { className: "panel review-card" },
    h("div", { className: "result-title" }, h("div", null, h("span", { className: "kicker" }, product.sku || result.input?.sku), h("h2", null, product.product || "Unknown product")), h("span", { className: `risk-pill ${calc.risk || "high"}` }, capitalize(calc.risk || "high"))),
    h(
      "div",
      { className: "metric-grid small" },
      h(Metric, { label: "Current", value: formatMoney(calc.current_price) }),
      h(Metric, { label: "Recommended", value: formatMoney(calc.recommended_price) }),
      h(Metric, { label: "Change", value: formatPercent(calc.price_change_pct, true) }),
      h(Metric, { label: "Route", value: calc.route })
    ),
    h(MarketStatus, { marketData: result.market_data }),
    h(PublishPanel, { result, reloadCatalog, onResultUpdated: onLocalUpdate }),
    h(DetailList, { title: "Agent reasoning", items: result.reasoning || [] })
  );
}

function PublishPanel({ result, reloadCatalog, onResultUpdated }) {
  const publish = result.publish;
  const calc = result.calculation || {};
  const product = result.product;
  const [note, setNote] = React.useState("");
  const [modifiedPrice, setModifiedPrice] = React.useState(typeof calc.recommended_price === "number" ? String(calc.recommended_price.toFixed(2)) : "");
  const [saving, setSaving] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    setMessage("");
    setError("");
    setNote("");
    setModifiedPrice(typeof calc.recommended_price === "number" ? String(calc.recommended_price.toFixed(2)) : "");
  }, [result]);

  if (!publish) return null;

  async function submitAction(action) {
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = {
        sku: product?.sku || result.input?.sku,
        action,
        route: calc.route,
        risk: calc.risk,
        note,
      };
      if (action === "approve") payload.price = calc.recommended_price;
      if (action === "modify") payload.price = modifiedPrice;

      const response = await fetch("/api/price-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(response);
      if (!response.ok) {
        setError(data.error || "Could not complete review action.");
        return;
      }

      await reloadCatalog();

      const nextPublish =
        action === "reject"
          ? { status: "rejected", message: data.message }
          : { status: "applied", message: data.message, previous_price: data.previous_price, updated_price: data.updated_price };

      onResultUpdated({
        product: data.product || result.product,
        publish: nextPublish,
      });
      setMessage(data.message);
    } catch (err) {
      setError("Could not save the approval action.");
    } finally {
      setSaving(false);
    }
  }

  if (publish.status === "auto_applied" || publish.status === "applied") {
    return h(
      "div",
      { className: "publish-panel publish-success" },
      h("h3", null, publish.status === "auto_applied" ? "Auto update completed" : "Price published"),
      h("p", null, publish.message),
      publish.updated_price !== undefined
        ? h("small", null, `Quest price updated from ${formatMoney(publish.previous_price)} to ${formatMoney(publish.updated_price)}.`)
        : null
    );
  }

  if (publish.status === "rejected") {
    return h("div", { className: "publish-panel publish-neutral" }, h("h3", null, "Change rejected"), h("p", null, publish.message));
  }

  return h(
    "div",
    { className: "publish-panel publish-review" },
    h("h3", null, "Human approval required"),
    h("p", null, publish.message),
    h(
      "div",
      { className: "approval-summary" },
      h("span", null, `Current ${formatMoney(calc.current_price)}`),
      h("span", null, `Recommended ${formatMoney(calc.recommended_price)}`),
      h("span", null, `Change ${formatPercent(calc.price_change_pct, true)}`)
    ),
    h(Field, {
      label: "Reviewer note",
      children: h("input", { value: note, placeholder: "Optional reason or comment", onChange: (event) => setNote(event.target.value) }),
    }),
    h(Field, {
      label: "Modify price before publish",
      children: h("input", { value: modifiedPrice, inputMode: "decimal", onChange: (event) => setModifiedPrice(event.target.value) }),
    }),
    h(
      "div",
      { className: "approval-actions" },
      h("button", { type: "button", className: "primary-button compact", disabled: saving, onClick: () => submitAction("approve") }, saving ? "Saving..." : "Accept change"),
      h("button", { type: "button", className: "secondary-button", disabled: saving, onClick: () => submitAction("modify") }, "Modify and apply"),
      h("button", { type: "button", className: "ghost-button", disabled: saving, onClick: () => submitAction("reject") }, "Reject")
    ),
    message ? h("p", { className: "success-note" }, message) : null,
    error ? h("p", { className: "error-note" }, error) : null
  );
}

function CatalogPage({ catalog }) {
  const [query, setQuery] = React.useState("");
  const products = catalog.products.filter((product) =>
    `${product.sku} ${product.mpn} ${product.product} ${product.brand} ${product.category}`.toLowerCase().includes(query.toLowerCase())
  );

  return h(
    "main",
    { className: "page-shell" },
    h(PageTitle, {
      eyebrow: "Catalog",
      title: "Product catalog",
      text: `This catalog includes ${catalog.products.length} products for pricing analysis.`,
    }),
    h(
      "section",
      { className: "panel" },
      h(Field, { label: "Search catalog", children: h("input", { value: query, placeholder: "SKU, MPN, product, brand, category", onChange: (event) => setQuery(event.target.value) }) }),
      h(
        "div",
        { className: "catalog-list" },
        products.map((product) =>
          h(
            "div",
            { className: "catalog-row", key: product.sku },
            h("span", null, product.sku),
            h("strong", null, product.product),
            h("small", null, `${product.brand} | ${product.category} | ${product.stock} ${product.uom}`),
            h("b", null, formatMoney(product.current_price))
          )
        )
      )
    )
  );
}

function CompetitorPage({ catalog }) {
  const [query, setQuery] = React.useState("");
  const products = catalog.products.filter((product) =>
    `${product.sku} ${product.mpn} ${product.product} ${product.brand} ${product.category}`.toLowerCase().includes(query.toLowerCase())
  );

  return h(
    "main",
    { className: "page-shell" },
    h(PageTitle, {
      eyebrow: "Competitor",
      title: "Competitor prices by SKU",
      text: "Run the pricing agent first. This page then shows each SKU with the latest comparable competitor names and prices.",
    }),
    h(
      "section",
      { className: "panel" },
      h(Field, { label: "Search competitors", children: h("input", { value: query, placeholder: "SKU, MPN, product, brand, competitor", onChange: (event) => setQuery(event.target.value) }) }),
      h(
        "div",
        { className: "catalog-list" },
        products.map((product) => h(CompetitorProductCard, { product, key: product.sku }))
      )
    )
  );
}

function CompetitorProductCard({ product }) {
  const latest = product.latest_analysis;
  const competitors = latest?.competitors || [];

  return h(
    "article",
    { className: "catalog-card" },
    h(
      "div",
      { className: "catalog-row" },
      h("span", null, product.sku),
      h("strong", null, product.product),
      h("small", null, `${product.brand} | ${product.category} | ${product.stock} ${product.uom}`),
      h("b", null, formatMoney(product.current_price))
    ),
    latest
      ? h(
          "div",
          { className: "catalog-competitors" },
          h(
            "div",
            { className: "catalog-analysis-head" },
            h("strong", null, "Latest agent run competitors"),
            h(
              "small",
              null,
              [
                latest.route ? `Route ${latest.route}` : null,
                latest.risk ? `Risk ${capitalize(latest.risk)}` : null,
                latest.recommended_price ? `Recommended ${formatMoney(latest.recommended_price)}` : null,
              ]
                .filter(Boolean)
                .join(" | ")
            )
          ),
          competitors.length
            ? h(
                "div",
                { className: "competitor-price-grid" },
                competitors.map((item, index) =>
                  h(
                    "div",
                    { className: "competitor-price-chip", key: `${product.sku}-${item.source}-${index}` },
                    h("strong", null, item.source || "Competitor"),
                    h("span", null, formatCatalogCompetitorPrice(item)),
                    item.title ? h("small", null, item.title) : null
                  )
                )
              )
            : h("p", { className: "subtle-note" }, "No comparable competitor price was found for this SKU.")
        )
      : h(
          "div",
          { className: "catalog-competitors empty" },
          h("span", null, "Run pricing agent to show competitor prices for this SKU.")
        )
  );
}

function MarketStatus({ marketData }) {
  if (!marketData) return null;
  const usedLive = ["live", "partial_live"].includes(marketData.mode);
  const hasPricingRows = (marketData.pricing_count || 0) > 0;
  return h(
    "div",
    { className: `market-status ${usedLive ? "live" : "fallback"}` },
    h("strong", null, usedLive ? "Live market data used" : hasPricingRows ? "Catalog competitor data used" : "No competitor found"),
    h("span", null, `${marketData.message} Live comparable found: ${marketData.live_count || 0}; excluded: ${marketData.excluded_count || 0}; pricing rows used: ${marketData.pricing_count || 0}.`)
  );
}

function PageTitle({ eyebrow, title, text }) {
  return h("section", { className: "page-title" }, h("p", { className: "eyebrow" }, eyebrow), h("h1", null, title), h("p", null, text));
}

function Metric({ label, value, clickable = false, onClick = null }) {
  return h(
    "article",
    { className: `metric${clickable ? " metric-clickable" : ""}`, onClick: clickable ? onClick : undefined },
    h("span", null, label),
    h("strong", null, value ?? "-")
  );
}

function ActionCard({ href, title, text }) {
  return h("a", { className: "action-card", href }, h("h2", null, title), h("p", null, text));
}

function versionedPath(path) {
  return `${path}?v=${appRouteVersion}`;
}

function Field({ label, children }) {
  return h("label", { className: "field" }, h("span", null, label), children);
}

function Select({ value, onChange, options }) {
  return h("select", { value, onChange: (event) => onChange(event.target.value) }, options.map(([optionValue, label]) => h("option", { key: optionValue, value: optionValue }, label)));
}

function DetailList({ title, items }) {
  return h("div", { className: "detail-list" }, h("h3", null, title), h("ul", null, items.map((item, index) => h("li", { key: `${title}-${index}` }, item))));
}

function LoadingPage() {
  return h("main", { className: "page-shell" }, h("section", { className: "panel" }, h("h1", null, "Loading"), h("p", null, "Connecting to Flask API...")));
}

function ErrorPage({ error }) {
  return h("main", { className: "page-shell" }, h("section", { className: "panel error-panel" }, h("h1", null, "Backend unavailable"), h("p", null, error)));
}

function Footer() {
  return h("footer", { className: "footer" }, h("span", null, "American industrial safety product pricing agent"), h("span", null, "Catalog-wide live pricing and approval workflow."));
}

function buildOverrides(products) {
  return Object.fromEntries(
    products.map((product) => [
      product.sku,
      {
        strategy: "balanced",
        demand_units: String(product.stock),
      },
    ])
  );
}

function formatMoney(value) {
  return typeof value === "number" && Number.isFinite(value) ? money.format(value) : "-";
}

function formatCatalogCompetitorPrice(item) {
  const normalized = typeof item.normalized_price === "number" ? item.normalized_price : item.price;
  const raw = typeof item.price === "number" ? item.price : normalized;
  const uom = item.normalized_uom || "UOM";
  if (typeof normalized !== "number") return "-";
  if (typeof raw === "number" && Math.abs(raw - normalized) > 0.01) {
    return `${formatMoney(normalized)} / ${uom} (raw ${formatMoney(raw)})`;
  }
  return `${formatMoney(normalized)} / ${uom}`;
}

function formatPercent(value, signed = false) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${signed && value > 0 ? "+" : ""}${pct.format(value)}`;
}

function capitalize(value) {
  value = String(value || "");
  return value.charAt(0).toUpperCase() + value.slice(1);
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
