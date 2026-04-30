const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");
const dataRoot = path.resolve(projectRoot, "..");
const outputDir = path.join(projectRoot, "data", "outputs");
const publicDir = path.join(projectRoot, "public");

fs.mkdirSync(outputDir, { recursive: true });
fs.mkdirSync(publicDir, { recursive: true });

function parseCSV(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;

  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    const next = text[i + 1];

    if (quoted) {
      if (c === '"' && next === '"') {
        cell += '"';
        i += 1;
      } else if (c === '"') {
        quoted = false;
      } else {
        cell += c;
      }
      continue;
    }

    if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(cell);
      cell = "";
    } else if (c === "\n") {
      row.push(cell.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += c;
    }
  }

  if (cell.length || row.length) {
    row.push(cell.replace(/\r$/, ""));
    rows.push(row);
  }

  const header = rows.shift().map((h, i) => (i === 0 ? h.replace(/^\uFEFF/, "") : h));
  return rows
    .filter((r) => r.length && r.some((v) => v !== ""))
    .map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
}

function readCSV(name) {
  return parseCSV(fs.readFileSync(path.join(dataRoot, name), "utf8"));
}

function csvEscape(value) {
  const s = value == null ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function writeCSV(name, rows, preferredHeader) {
  const publicOutputDir = path.join(publicDir, "outputs");
  fs.mkdirSync(publicOutputDir, { recursive: true });
  const header = preferredHeader || Array.from(rows.reduce((set, r) => {
    Object.keys(r).forEach((k) => set.add(k));
    return set;
  }, new Set()));
  const lines = [header.join(",")];
  rows.forEach((row) => lines.push(header.map((h) => csvEscape(row[h])).join(",")));
  const body = `${lines.join("\n")}\n`;
  fs.writeFileSync(path.join(outputDir, name), body);
  fs.writeFileSync(path.join(publicOutputDir, name), body);
}

function writeJSON(name, payload) {
  fs.writeFileSync(path.join(outputDir, name), `${JSON.stringify(payload, null, 2)}\n`);
  fs.writeFileSync(path.join(publicDir, name), `${JSON.stringify(payload, null, 2)}\n`);
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function round(value, digits = 0) {
  const m = 10 ** digits;
  return Math.round((value + Number.EPSILON) * m) / m;
}

function avg(values) {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
}

function median(values) {
  const clean = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!clean.length) return 0;
  const mid = Math.floor(clean.length / 2);
  return clean.length % 2 ? clean[mid] : (clean[mid - 1] + clean[mid]) / 2;
}

function weightedRecent(values) {
  const clean = values.filter((v) => Number.isFinite(v));
  const totalWeight = clean.reduce((sum, _, i) => sum + i + 1, 0);
  return totalWeight ? clean.reduce((sum, v, i) => sum + v * (i + 1), 0) / totalWeight : 0;
}

function parseDate(value) {
  return new Date(`${value}T00:00:00Z`);
}

function formatDate(date) {
  return date.toISOString().slice(0, 10);
}

function addDays(date, days) {
  const copy = new Date(date);
  copy.setUTCDate(copy.getUTCDate() + days);
  return copy;
}

function weekStart(date) {
  const copy = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const day = copy.getUTCDay() || 7;
  copy.setUTCDate(copy.getUTCDate() - day + 1);
  return copy;
}

function key(...parts) {
  return parts.join("|");
}

function percentile(values, p) {
  const clean = values.slice().sort((a, b) => a - b);
  if (!clean.length) return 0;
  const idx = (clean.length - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return clean[lo];
  return clean[lo] + (clean[hi] - clean[lo]) * (idx - lo);
}

const sales = readCSV("sales_history.csv");
const skuRows = readCSV("sku_master.csv");
const outlets = readCSV("outlet_master.csv");
const inventoryRows = readCSV("inventory_snapshot.csv");
const promoRows = readCSV("promotions_calendar.csv");
const festiveRows = readCSV("festive_calendar.csv");

const skuById = new Map(skuRows.map((r) => [r.sku_id, r]));
const invBySku = new Map(inventoryRows.map((r) => [r.sku_id, r]));
const skus = skuRows.map((r) => r.sku_id).sort();
const outletIds = outlets.map((r) => r.outlet_id).sort();
const weeks = Array.from(new Set(sales.map((r) => r.week_start_date))).sort();
const weekIndex = new Map(weeks.map((w, i) => [w, i]));
const lastWeek = parseDate(weeks[weeks.length - 1]);
const forecastWeeks = Array.from({ length: 6 }, (_, i) => formatDate(addDays(lastWeek, (i + 1) * 7)));

const promotions = promoRows.map((r) => ({
  id: r.promo_id,
  name: r.promo_name,
  start: parseDate(r.start_date),
  end: parseDate(r.end_date),
  skus: new Set(r.sku_ids.split(",").map((s) => s.trim()).filter(Boolean)),
  uplift: num(r.uplift_pct)
}));

function promoUpliftFor(skuId, week) {
  const start = parseDate(week);
  const end = addDays(start, 6);
  let uplift = 0;
  promotions.forEach((promo) => {
    if (promo.skus.has(skuId) && start <= promo.end && end >= promo.start) {
      uplift = Math.max(uplift, promo.uplift);
    }
  });
  return uplift;
}

const salesBySkuWeek = new Map();
const rowsBySkuWeek = new Map();
const observedOutletSkuWeek = new Set();
const activeOutletWeek = new Set();
const carriedWindow = new Set();
const outletSkuObservedCount = new Map();

sales.forEach((r) => {
  const net = Math.max(0, num(r.units_sold) - num(r.returns));
  const skuWeek = key(r.sku_id, r.week_start_date);
  salesBySkuWeek.set(skuWeek, (salesBySkuWeek.get(skuWeek) || 0) + net);
  rowsBySkuWeek.set(skuWeek, (rowsBySkuWeek.get(skuWeek) || 0) + 1);
  observedOutletSkuWeek.add(key(r.outlet_id, r.sku_id, r.week_start_date));
  activeOutletWeek.add(key(r.outlet_id, r.week_start_date));
  outletSkuObservedCount.set(key(r.outlet_id, r.sku_id), (outletSkuObservedCount.get(key(r.outlet_id, r.sku_id)) || 0) + 1);

  const idx = weekIndex.get(r.week_start_date);
  for (let offset = -8; offset <= 8; offset += 1) {
    const w = weeks[idx + offset];
    if (w) carriedWindow.add(key(r.outlet_id, r.sku_id, w));
  }
});

const zeroMissing = {
  total_possible_outlet_sku_weeks: weeks.length * outletIds.length * skus.length,
  observed_rows: sales.length,
  missing_combinations: 0,
  missing_outlet_report: 0,
  true_zero: 0,
  not_carried: 0
};

const trueZeroBySku = new Map(skus.map((sku) => [sku, 0]));
for (const outlet of outletIds) {
  for (const week of weeks) {
    const outletActive = activeOutletWeek.has(key(outlet, week));
    for (const skuId of skus) {
      if (observedOutletSkuWeek.has(key(outlet, skuId, week))) continue;
      zeroMissing.missing_combinations += 1;
      if (!outletActive) {
        zeroMissing.missing_outlet_report += 1;
      } else if (carriedWindow.has(key(outlet, skuId, week))) {
        zeroMissing.true_zero += 1;
        trueZeroBySku.set(skuId, trueZeroBySku.get(skuId) + 1);
      } else {
        zeroMissing.not_carried += 1;
      }
    }
  }
}

const weeklySeriesBySku = new Map();
const adjustedSeriesBySku = new Map();
skus.forEach((skuId) => {
  const raw = weeks.map((w) => salesBySkuWeek.get(key(skuId, w)) || 0);
  const adjusted = raw.map((value, i) => {
    const uplift = promoUpliftFor(skuId, weeks[i]);
    return uplift > 0 ? value / (1 + uplift / 100) : value;
  });
  weeklySeriesBySku.set(skuId, raw);
  adjustedSeriesBySku.set(skuId, adjusted);
});

function demandAt(skuId, week) {
  return salesBySkuWeek.get(key(skuId, week)) || 0;
}

function valuesForWeeks(skuId, dates) {
  return dates.map((w) => demandAt(skuId, w));
}

function diwaliMetrics(skuId) {
  const base22Weeks = ["2022-08-29", "2022-09-05", "2022-09-12", "2022-09-19", "2022-09-26", "2022-10-03"];
  const base23Weeks = ["2023-08-28", "2023-09-04", "2023-09-11", "2023-09-18", "2023-09-25", "2023-10-02"];
  const event22Weeks = ["2022-10-10", "2022-10-17", "2022-10-24", "2022-10-31", "2022-11-07"];
  const event23Weeks = ["2023-10-09", "2023-10-16", "2023-10-23"];
  const base22 = median(valuesForWeeks(skuId, base22Weeks));
  const base23 = median(valuesForWeeks(skuId, base23Weeks));
  const peak22 = Math.max(...valuesForWeeks(skuId, event22Weeks));
  const observedPeak23 = Math.max(...valuesForWeeks(skuId, event23Weeks));
  const uplift22 = base22 > 0 ? peak22 / base22 : 1;
  const expectedPeak23 = base23 * Math.max(1, uplift22);
  const deficit = Math.max(0, expectedPeak23 - observedPeak23);
  const deficitPct = expectedPeak23 > 0 ? deficit / expectedPeak23 : 0;
  return {
    base22,
    base23,
    peak22,
    observedPeak23,
    uplift22,
    expectedPeak23,
    deficit,
    deficitPct
  };
}

const diwaliRows = skus.map((skuId) => {
  const sku = skuById.get(skuId);
  const m = diwaliMetrics(skuId);
  return {
    sku_id: skuId,
    product_name: sku.product_name,
    category: sku.category,
    pre_diwali_baseline_2023: round(m.base23, 1),
    diwali_2022_uplift_ratio: round(m.uplift22, 2),
    expected_peak_2023_units: round(m.expectedPeak23, 1),
    observed_peak_2023_units: round(m.observedPeak23, 1),
    demand_deficit_units: round(m.deficit, 1),
    demand_deficit_pct: round(m.deficitPct * 100, 1),
    stockout_score: round(m.deficitPct * 100 + Math.log1p(m.deficit), 2),
    decision: m.deficit > 0 ? "stockout_candidate" : "not_flagged",
    reason: m.deficit > 0
      ? "Observed Diwali 2023 peak under-shot expected peak from Diwali 2022 uplift."
      : "Observed Diwali 2023 peak met or exceeded seasonal expectation."
  };
}).sort((a, b) => b.stockout_score - a.stockout_score || b.demand_deficit_units - a.demand_deficit_units);

const top14Diwali = diwaliRows.slice(0, 14).map((r, i) => ({ rank: i + 1, ...r, decision: "stockout_candidate" }));

function forecastForSku(skuId) {
  const adjusted = adjustedSeriesBySku.get(skuId);
  const raw = weeklySeriesBySku.get(skuId);
  const last = adjusted.length - 1;
  const recent8 = adjusted.slice(Math.max(0, last - 7), last + 1);
  const last4 = adjusted.slice(Math.max(0, last - 3), last + 1);
  const prev4 = adjusted.slice(Math.max(0, last - 7), Math.max(0, last - 3));
  const recent = weightedRecent(recent8);
  const trendPerWeek = (avg(last4) - avg(prev4)) / 4;
  const rawMedian12 = median(raw.slice(Math.max(0, raw.length - 12)));
  const mad = median(raw.slice(Math.max(0, raw.length - 12)).map((v) => Math.abs(v - rawMedian12))) || Math.max(1, rawMedian12 * 0.08);

  return forecastWeeks.map((week, h) => {
    const priorYearIndex = weeks.length - 52 + h;
    const annual = adjusted[priorYearIndex] || recent;
    const trendValue = Math.max(0, recent + trendPerWeek * (h + 1));
    const uplift = promoUpliftFor(skuId, week);
    const baseForecast = 0.62 * recent + 0.28 * annual + 0.10 * trendValue;
    const forecast = Math.max(0, baseForecast * (1 + uplift / 100));
    return {
      week_start_date: week,
      forecast_units: Math.round(forecast),
      low_units: Math.max(0, Math.round(forecast - 1.28 * mad)),
      high_units: Math.round(forecast + 1.28 * mad)
    };
  });
}

const forecastsBySku = new Map(skus.map((skuId) => [skuId, forecastForSku(skuId)]));
const avg12BySku = new Map(skus.map((skuId) => {
  const raw = weeklySeriesBySku.get(skuId);
  return [skuId, avg(raw.slice(Math.max(0, raw.length - 12)))];
}));
const avg8BySku = new Map(skus.map((skuId) => {
  const raw = weeklySeriesBySku.get(skuId);
  return [skuId, avg(raw.slice(Math.max(0, raw.length - 8)))];
}));
const p25 = percentile(Array.from(avg12BySku.values()), 0.25);
const p75 = percentile(Array.from(avg12BySku.values()), 0.75);

function inventoryPosition(skuId) {
  const inv = invBySku.get(skuId) || {};
  return num(inv.warehouse_stock) + num(inv.in_transit_qty) - num(inv.committed_qty);
}

function skuClasses(skuId) {
  const inv = invBySku.get(skuId) || {};
  const m = diwaliMetrics(skuId);
  const avg12 = avg12BySku.get(skuId);
  const avg8 = avg8BySku.get(skuId);
  const position = inventoryPosition(skuId);
  const weeksCover = avg8 > 0 ? position / avg8 : 999;
  const classes = [];

  if (avg8 < 2 && position > 0) classes.push("dead_stock");
  if (m.uplift22 >= 2.6 || m.deficitPct >= 0.08) classes.push("seasonal");
  if (avg12 >= p75) classes.push("fast_mover");
  if (avg12 <= p25 || weeksCover > 12) classes.push("slow_mover");
  if (!classes.length) classes.push("stable");

  let primary = "stable";
  if (classes.includes("dead_stock")) primary = "dead_stock";
  else if (classes.includes("seasonal")) primary = "seasonal";
  else if (classes.includes("fast_mover")) primary = "fast_mover";
  else if (classes.includes("slow_mover")) primary = "slow_mover";

  return {
    primary,
    classes,
    weeksCover,
    diwaliUplift: m.uplift22,
    recentAvg: avg12
  };
}

const classificationRows = skus.map((skuId) => {
  const sku = skuById.get(skuId);
  const c = skuClasses(skuId);
  const forecast6 = forecastsBySku.get(skuId).reduce((sum, r) => sum + r.forecast_units, 0);
  return {
    sku_id: skuId,
    product_name: sku.product_name,
    category: sku.category,
    primary_class: c.primary,
    all_classes: c.classes.join("|"),
    avg_weekly_units_12w: round(c.recentAvg, 1),
    six_week_forecast_units: forecast6,
    inventory_position_units: inventoryPosition(skuId),
    weeks_of_cover: round(c.weeksCover, 1),
    diwali_uplift_ratio: round(c.diwaliUplift, 2),
    true_zero_rows_classified: trueZeroBySku.get(skuId)
  };
});

function sumForecast(skuId, n) {
  const values = forecastsBySku.get(skuId).map((r) => r.forecast_units);
  if (n <= values.length) return values.slice(0, n).reduce((a, b) => a + b, 0);
  return values.reduce((a, b) => a + b, 0) + avg(values) * (n - values.length);
}

function recentMad(skuId) {
  const raw = weeklySeriesBySku.get(skuId);
  const recent = raw.slice(Math.max(0, raw.length - 12));
  const med = median(recent);
  return median(recent.map((v) => Math.abs(v - med))) || Math.max(1, med * 0.08);
}

const reorderRows = skus.map((skuId) => {
  const sku = skuById.get(skuId);
  const inv = invBySku.get(skuId) || {};
  const c = skuClasses(skuId);
  const position = inventoryPosition(skuId);
  const leadDays = num(sku.supplier_lead_time_days);
  const leadWeeks = Math.max(1, Math.ceil(leadDays / 7));
  const targetWeeks = c.classes.includes("slow_mover") ? 4 : c.classes.includes("fast_mover") || c.classes.includes("seasonal") ? 6 : 5;
  const leadDemand = sumForecast(skuId, leadWeeks);
  const targetDemand = sumForecast(skuId, targetWeeks);
  const safetyStock = Math.ceil(1.1 * recentMad(skuId) * Math.sqrt(leadWeeks));
  const rawNeed = Math.max(0, targetDemand + safetyStock - position);
  const moq = Math.max(1, num(sku.moq_from_supplier));
  const roundedNeed = rawNeed > 0 ? Math.ceil(rawNeed / moq) * moq : 0;
  const forecast6 = sumForecast(skuId, 6);
  const avgForecast = forecast6 / 6;
  const shelfLifeDays = num(sku.shelf_life_days);
  const shelfLifeMaxOrder = Math.max(0, Math.floor(avgForecast * shelfLifeDays / 7));
  const shelfMoqCap = Math.floor(shelfLifeMaxOrder / moq) * moq;
  const recommendedQty = Math.max(0, Math.min(roundedNeed, shelfMoqCap));
  const shelfLifeConstrained = roundedNeed > recommendedQty;
  const stockoutRisk = position <= leadDemand || position <= avgForecast * 2;
  const overstockRisk = position > Math.max(avgForecast * 12, shelfLifeMaxOrder);
  const unitCost = num(sku.cost_price);
  const flags = [];

  if (position <= 0) flags.push("already_stocked_out");
  else if (stockoutRisk) flags.push("stockout_risk");
  if (overstockRisk) flags.push("overstock_risk");
  if (shelfLifeDays < 90) flags.push("short_shelf_life");
  if (shelfLifeConstrained) flags.push("shelf_life_guardrail_applied");
  if (recommendedQty === 0 && rawNeed > 0) flags.push("moq_or_shelf_life_blocks_order");

  return {
    sku_id: skuId,
    product_name: sku.product_name,
    category: sku.category,
    class: c.primary,
    supplier_lead_time_days: leadDays,
    moq_from_supplier: moq,
    warehouse_stock: num(inv.warehouse_stock),
    in_transit_qty: num(inv.in_transit_qty),
    committed_qty: num(inv.committed_qty),
    inventory_position_units: position,
    six_week_forecast_units: Math.round(forecast6),
    lead_time_demand_units: Math.round(leadDemand),
    safety_stock_units: safetyStock,
    raw_need_units: Math.round(rawNeed),
    recommended_order_qty: Math.round(recommendedQty),
    shelf_life_days: shelfLifeDays,
    shelf_life_max_order_units: Math.round(shelfLifeMaxOrder),
    estimated_order_value: round(recommendedQty * unitCost, 2),
    risk_flags: flags.join("|") || "none",
    recommendation: recommendedQty > 0 ? "ORDER_NOW" : stockoutRisk ? "REVIEW_MANUALLY" : "NO_ORDER"
  };
}).sort((a, b) => {
  const riskA = a.risk_flags.includes("stockout") || a.risk_flags.includes("already") ? 1 : 0;
  const riskB = b.risk_flags.includes("stockout") || b.risk_flags.includes("already") ? 1 : 0;
  return riskB - riskA || b.estimated_order_value - a.estimated_order_value;
});

const forecastRows = [];
skus.forEach((skuId) => {
  const sku = skuById.get(skuId);
  forecastsBySku.get(skuId).forEach((row) => {
    forecastRows.push({
      sku_id: skuId,
      product_name: sku.product_name,
      category: sku.category,
      ...row
    });
  });
});

const mondayRows = reorderRows
  .filter((r) => r.recommended_order_qty > 0 || r.risk_flags !== "none")
  .map((r) => ({
    priority: r.risk_flags.includes("already_stocked_out") ? "P0" : r.risk_flags.includes("stockout_risk") ? "P1" : r.risk_flags.includes("overstock_risk") ? "P2" : "P3",
    sku_id: r.sku_id,
    product_name: r.product_name,
    order_qty: r.recommended_order_qty,
    moq: r.moq_from_supplier,
    lead_time_days: r.supplier_lead_time_days,
    estimated_order_value: r.estimated_order_value,
    flags: r.risk_flags,
    action: r.recommendation
  }));

const totalForecastByWeek = forecastWeeks.map((week) => ({
  week_start_date: week,
  forecast_units: forecastRows.filter((r) => r.week_start_date === week).reduce((sum, r) => sum + r.forecast_units, 0)
}));

const summary = {
  generated_at: new Date().toISOString(),
  source_week_start: weeks[0],
  source_week_end: weeks[weeks.length - 1],
  sku_count: skus.length,
  outlet_count: outletIds.length,
  sales_rows: sales.length,
  forecast_week_count: 6,
  six_week_forecast_units: forecastRows.reduce((sum, r) => sum + r.forecast_units, 0),
  recommended_order_units: reorderRows.reduce((sum, r) => sum + r.recommended_order_qty, 0),
  recommended_order_value: round(reorderRows.reduce((sum, r) => sum + r.estimated_order_value, 0), 2),
  stockout_risk_count: reorderRows.filter((r) => r.risk_flags.includes("stockout") || r.risk_flags.includes("already")).length,
  overstock_risk_count: reorderRows.filter((r) => r.risk_flags.includes("overstock")).length,
  diwali_stockout_candidates: top14Diwali.length,
  data_note: skuRows.length === 140 ? "Problem-statement SKU scale present." : `Input sku_master.csv contains ${skuRows.length} SKUs; pipeline processes all present SKUs.`
};

const zeroMissingRows = [
  { metric: "total_possible_outlet_sku_weeks", value: zeroMissing.total_possible_outlet_sku_weeks },
  { metric: "observed_rows", value: zeroMissing.observed_rows },
  { metric: "missing_combinations", value: zeroMissing.missing_combinations },
  { metric: "missing_outlet_report", value: zeroMissing.missing_outlet_report },
  { metric: "true_zero", value: zeroMissing.true_zero },
  { metric: "not_carried", value: zeroMissing.not_carried }
];

writeCSV("forecast_6_week.csv", forecastRows, [
  "sku_id",
  "product_name",
  "category",
  "week_start_date",
  "forecast_units",
  "low_units",
  "high_units"
]);
writeCSV("reorder_recommendations.csv", reorderRows);
writeCSV("sku_classification.csv", classificationRows);
writeCSV("diwali_2023_stockout_candidates.csv", top14Diwali);
writeCSV("true_zero_missing_summary.csv", zeroMissingRows, ["metric", "value"]);
writeCSV("monday_report.csv", mondayRows);

const payload = {
  summary,
  zeroMissing,
  forecastWeeks,
  totalForecastByWeek,
  forecasts: forecastRows,
  reorder: reorderRows,
  classifications: classificationRows,
  diwaliCandidates: top14Diwali,
  mondayReport: mondayRows,
  methodology: {
    trueZero:
      "Missing outlet-SKU-week rows are true zero only when the outlet reported that week and the SKU sits inside a +/-8 week observed assortment window. Outlet weeks with no rows are missing reports; SKUs outside the assortment window are not-carried.",
    forecast:
      "SKU-level six-week forecast uses promo-normalized recent demand, same-week prior-year seasonality, and a small recent trend component with MAD confidence bands.",
    reorder:
      "Orders use inventory position, lead-time demand, target cover, safety stock, MOQ rounding, and a shelf-life cap based on expected sell-through before expiry.",
    diwali:
      "Stockout candidates are ranked by demand deficit: expected Diwali 2023 peak from Diwali 2022 uplift minus observed Diwali 2023 peak."
  }
};

writeJSON("outputs.json", payload);
writeJSON("dashboard_payload.json", payload);
fs.writeFileSync(
  path.join(publicDir, "outputs.js"),
  `window.DM_PAYLOAD = ${JSON.stringify(payload, null, 2)};\n`
);

console.log("Generated Demand Mirage outputs");
console.log(`SKUs: ${summary.sku_count}`);
console.log(`Six-week forecast units: ${summary.six_week_forecast_units}`);
console.log(`Recommended order value: INR ${summary.recommended_order_value}`);
console.log(`Diwali 2023 candidates: ${top14Diwali.map((r) => r.sku_id).join(", ")}`);
