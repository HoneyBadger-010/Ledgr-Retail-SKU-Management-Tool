const fmt = new Intl.NumberFormat("en-IN");
const money = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0
});

function riskClass(flags) {
  if (flags.includes("already_stocked_out") || flags.includes("stockout_risk")) return "risk-danger";
  if (flags.includes("overstock_risk") || flags.includes("shelf_life")) return "risk-warning";
  return "risk-good";
}

function classBadge(value) {
  const map = {
    fast_mover: "bg-green-lt",
    slow_mover: "bg-yellow-lt",
    seasonal: "bg-purple-lt",
    dead_stock: "bg-red-lt",
    stable: "bg-blue-lt"
  };
  return `<span class="badge ${map[value] || "bg-secondary-lt"}">${value.replace(/_/g, " ")}</span>`;
}

function metricCard(label, value, icon, tone = "primary") {
  return `
    <div class="col-6 col-lg-3">
      <div class="card">
        <div class="card-body">
          <div class="d-flex align-items-center">
            <span class="avatar bg-${tone}-lt me-3"><i class="ti ${icon}"></i></span>
            <div>
              <div class="metric-value">${value}</div>
              <div class="metric-label">${label}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderMetrics(summary) {
  document.querySelector("#data-note").textContent = summary.data_note;
  document.querySelector("#metrics").innerHTML = [
    metricCard("SKUs forecasted", fmt.format(summary.sku_count), "ti-packages", "primary"),
    metricCard("6-week demand", fmt.format(summary.six_week_forecast_units), "ti-trending-up", "green"),
    metricCard("Order value", money.format(summary.recommended_order_value), "ti-shopping-cart", "orange"),
    metricCard("Stockout risks", fmt.format(summary.stockout_risk_count), "ti-alert-triangle", "red")
  ].join("");
}

function renderZeroSummary(data) {
  const z = data.zeroMissing;
  const rows = [
    ["Observed sales rows", z.observed_rows],
    ["Missing combinations", z.missing_combinations],
    ["Missing outlet report", z.missing_outlet_report],
    ["True zero", z.true_zero],
    ["Not carried", z.not_carried]
  ];
  document.querySelector("#zero-summary").innerHTML = `
    <div class="datagrid">
      ${rows.map(([label, value]) => `
        <div class="datagrid-item">
          <div class="datagrid-title">${label}</div>
          <div class="datagrid-content">${fmt.format(value)}</div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderForecastChart(data) {
  const el = document.querySelector("#forecast-chart");
  const categories = data.totalForecastByWeek.map((r) => r.week_start_date);
  const values = data.totalForecastByWeek.map((r) => r.forecast_units);

  if (!window.ApexCharts) {
    el.innerHTML = `<div class="fallback-card p-3">${categories.map((c, i) => `${c}: ${fmt.format(values[i])}`).join("<br>")}</div>`;
    return;
  }

  new ApexCharts(el, {
    chart: { type: "area", height: 260, toolbar: { show: false } },
    series: [{ name: "Forecast units", data: values }],
    dataLabels: { enabled: false },
    stroke: { width: 3, curve: "smooth" },
    xaxis: { categories },
    yaxis: { labels: { formatter: (value) => fmt.format(Math.round(value)) } },
    colors: ["#206bc4"],
    fill: { opacity: 0.16 },
    tooltip: { y: { formatter: (value) => `${fmt.format(Math.round(value))} units` } }
  }).render();
}

function renderReorder(rows, query = "") {
  const q = query.trim().toLowerCase();
  const filtered = rows
    .filter((r) => r.recommended_order_qty > 0 || r.risk_flags !== "none")
    .filter((r) => !q || `${r.sku_id} ${r.product_name}`.toLowerCase().includes(q))
    .slice(0, 30);

  document.querySelector("#reorder-table").innerHTML = filtered.map((r) => `
    <tr>
      <td class="fw-semibold">${r.sku_id}</td>
      <td title="${r.product_name}">${r.product_name}</td>
      <td>${classBadge(r.class)}</td>
      <td class="text-end">${fmt.format(r.inventory_position_units)}</td>
      <td class="text-end">${fmt.format(r.six_week_forecast_units)}</td>
      <td class="text-end fw-semibold">${fmt.format(r.recommended_order_qty)}</td>
      <td class="text-end">${money.format(r.estimated_order_value)}</td>
      <td><span class="risk-pill ${riskClass(r.risk_flags)}">${r.risk_flags.replaceAll("|", ", ")}</span></td>
    </tr>
  `).join("");
}

function renderDiwali(rows) {
  document.querySelector("#diwali-table").innerHTML = rows.map((r) => `
    <tr>
      <td>${r.rank}</td>
      <td class="fw-semibold">${r.sku_id}</td>
      <td>${r.product_name}</td>
      <td class="text-end">${fmt.format(Math.round(r.demand_deficit_units))}</td>
      <td class="text-end">${r.stockout_score}</td>
    </tr>
  `).join("");
}

function renderClassifications(rows) {
  document.querySelector("#class-table").innerHTML = rows
    .slice()
    .sort((a, b) => b.six_week_forecast_units - a.six_week_forecast_units)
    .slice(0, 18)
    .map((r) => `
      <tr>
        <td class="fw-semibold">${r.sku_id}</td>
        <td>${r.product_name}</td>
        <td>${classBadge(r.primary_class)}</td>
        <td class="text-end">${fmt.format(Math.round(r.avg_weekly_units_12w))}</td>
        <td class="text-end">${r.weeks_of_cover}</td>
      </tr>
    `).join("");
}

function renderMethodology(methodology) {
  document.querySelector("#methodology").innerHTML = Object.entries(methodology).map(([name, text]) => `
    <div class="col-12 col-md-6">
      <div class="text-secondary text-uppercase small fw-semibold">${name}</div>
      <div>${text}</div>
    </div>
  `).join("");
}

async function main() {
  const data = window.DM_PAYLOAD || await fetch("outputs.json").then((response) => response.json());

  renderMetrics(data.summary);
  renderZeroSummary(data);
  renderForecastChart(data);
  renderReorder(data.reorder);
  renderDiwali(data.diwaliCandidates);
  renderClassifications(data.classifications);
  renderMethodology(data.methodology);

  document.querySelector("#reorder-search").addEventListener("input", (event) => {
    renderReorder(data.reorder, event.target.value);
  });
}

main().catch((error) => {
  document.body.innerHTML = `<div class="container-xl py-5"><div class="alert alert-danger">Dashboard failed to load: ${error.message}</div></div>`;
});
