const NUMERIC_FIELDS = new Set([
  'year','month','day','day_of_year','obs_count','observation_minutes','completeness_pct',
  'temp_avg_f','temp_high_f','temp_low_f','dewpoint_avg_f','dewpoint_high_f','dewpoint_low_f',
  'rh_avg_pct','rh_high_pct','rh_low_pct','pressure_avg_mb','pressure_high_mb','pressure_low_mb',
  'wind_avg_mph','wind_max_1min_mph','wind_lull_min_mph','wind_gust_mph','wind_vector_dir_deg',
  'precip_in','precip_raw_in','wet_minutes','lightning_count','lightning_avg_distance_mi',
  'lightning_closest_distance_mi','battery_min_v','report_interval_mode_min'
]);
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const SECTORS = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];

const state = {
  rows: [],
  selectedRows: [],
  metadata: null,
  mode: 'actual',
  chart: null,
};

const el = id => document.getElementById(id);
const finite = value => Number.isFinite(value);
const mean = values => {
  const clean = values.filter(finite);
  return clean.length ? clean.reduce((a,b) => a+b, 0) / clean.length : null;
};
const sum = values => values.filter(finite).reduce((a,b) => a+b, 0);
const fmt = (value, digits=1, suffix='') => finite(value) ? `${value.toFixed(digits)}${suffix}` : '—';
const parseLocalDate = value => new Date(`${value}T12:00:00`);
const prettyDate = value => value ? parseLocalDate(value).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'}) : '—';
const shortDate = value => value ? parseLocalDate(value).toLocaleDateString('en-US', {month:'short', day:'numeric'}) : '—';

function parseCSV(text) {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const next = text[i + 1];
    if (quoted) {
      if (char === '"' && next === '"') { field += '"'; i++; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') { row.push(field); field = ''; }
    else if (char === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else if (char !== '\r') field += char;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  const headers = rows.shift() || [];
  return rows.filter(r => r.some(v => v !== '')).map(values => {
    const item = {};
    headers.forEach((header, index) => {
      const raw = values[index] ?? '';
      item[header] = NUMERIC_FIELDS.has(header) ? (raw === '' ? null : Number(raw)) : raw;
    });
    return item;
  });
}

async function loadData() {
  try {
    const [csvResponse, metaResponse] = await Promise.all([
      fetch('data/daily.csv', {cache: 'no-store'}),
      fetch('data/metadata.json', {cache: 'no-store'}),
    ]);
    if (!csvResponse.ok || !metaResponse.ok) throw new Error('The published data files could not be loaded.');
    state.rows = parseCSV(await csvResponse.text()).sort((a,b) => a.date.localeCompare(b.date));
    state.metadata = await metaResponse.json();
    if (!state.rows.length) {
      setStatus('Archive is ready, but no observations have been collected yet.', 'error');
      el('period-detail').textContent = 'Add the TEMPEST_TOKEN secret, run the backfill workflow, then refresh this page.';
      return;
    }
    initializeControls();
    setStatus(`${state.metadata.days_archived.toLocaleString()} local days archived`, 'ready');
    el('generated-at').textContent = `Updated ${new Date(state.metadata.generated_at_utc).toLocaleString()}`;
    applySelection();
  } catch (error) {
    showError(error.message);
    setStatus('Climate archive failed to load', 'error');
  }
}

function setStatus(message, className='') {
  el('archive-status').textContent = message;
  el('status-dot').className = `status-dot ${className}`;
}

function initializeControls() {
  const first = state.rows[0].date;
  const last = state.rows[state.rows.length - 1].date;
  el('actual-start').min = first;
  el('actual-start').max = last;
  el('actual-end').min = first;
  el('actual-end').max = last;
  el('actual-start').value = first;
  el('actual-end').value = last;

  ['calendar-start-month','calendar-end-month'].forEach(id => {
    el(id).innerHTML = MONTHS.map((month, i) => `<option value="${i+1}">${month}</option>`).join('');
  });
  el('calendar-start-month').value = 11;
  el('calendar-end-month').value = 11;
  updateDayOptions('start', 10);
  updateDayOptions('end', 20);
}

function updateDayOptions(which, selectedDay=null) {
  const month = Number(el(`calendar-${which}-month`).value);
  const daySelect = el(`calendar-${which}-day`);
  const days = new Date(2000, month, 0).getDate();
  const fallback = Math.min(selectedDay || Number(daySelect.value) || 1, days);
  daySelect.innerHTML = Array.from({length: days}, (_, i) => `<option value="${i+1}">${i+1}</option>`).join('');
  daySelect.value = fallback;
}

function switchMode(mode) {
  state.mode = mode;
  document.querySelectorAll('.mode-button').forEach(button => button.classList.toggle('active', button.dataset.mode === mode));
  el('actual-controls').classList.toggle('hidden', mode !== 'actual');
  el('calendar-controls').classList.toggle('hidden', mode !== 'calendar');
}

function qualityRows(rows) {
  if (!el('quality-filter').checked) return rows;
  return rows.filter(row => finite(row.completeness_pct) && row.completeness_pct >= 80);
}

function mdKey(rowOrMonth, maybeDay) {
  if (typeof rowOrMonth === 'object') return rowOrMonth.month * 100 + rowOrMonth.day;
  return Number(rowOrMonth) * 100 + Number(maybeDay);
}

function selectionForMode() {
  if (state.mode === 'actual') {
    const start = el('actual-start').value;
    const end = el('actual-end').value;
    if (!start || !end || start > end) throw new Error('Choose a valid start and end date.');
    return state.rows.filter(row => row.date >= start && row.date <= end);
  }
  const startKey = mdKey(el('calendar-start-month').value, el('calendar-start-day').value);
  const endKey = mdKey(el('calendar-end-month').value, el('calendar-end-day').value);
  return state.rows.filter(row => {
    const key = mdKey(row);
    return startKey <= endKey ? key >= startKey && key <= endKey : key >= startKey || key <= endKey;
  });
}

function periodDayCount() {
  const startMonth = Number(el('calendar-start-month').value);
  const startDay = Number(el('calendar-start-day').value);
  const endMonth = Number(el('calendar-end-month').value);
  const endDay = Number(el('calendar-end-day').value);
  const start = new Date(2000, startMonth - 1, startDay, 12);
  let end = new Date(2000, endMonth - 1, endDay, 12);
  if (end < start) end = new Date(2001, endMonth - 1, endDay, 12);
  return Math.round((end - start) / 86400000) + 1;
}

function applySelection() {
  try {
    const rawSelection = selectionForMode();
    const selected = qualityRows(rawSelection);
    if (!selected.length) throw new Error('No qualifying archived days fall inside that period.');
    state.selectedRows = selected;
    renderPeriod(rawSelection, selected);
    renderSummary(selected);
    renderRecords(selected);
    renderChart(selected);
    renderTable(selected);
    el('download-button').disabled = false;
    hideError();
  } catch (error) {
    showError(error.message);
  }
}

function renderPeriod(rawRows, rows) {
  let title, detail;
  const excluded = rawRows.length - rows.length;
  if (state.mode === 'actual') {
    title = `${prettyDate(rows[0].date)} – ${prettyDate(rows[rows.length - 1].date)}`;
    detail = `${rows.length.toLocaleString()} archived days`;
  } else {
    const sm = Number(el('calendar-start-month').value), sd = Number(el('calendar-start-day').value);
    const em = Number(el('calendar-end-month').value), ed = Number(el('calendar-end-day').value);
    title = `${MONTHS[sm-1]} ${sd} – ${MONTHS[em-1]} ${ed}, every available year`;
    const years = [...new Set(rows.map(row => row.year))];
    detail = `${rows.length.toLocaleString()} archived days across ${years.length} year${years.length === 1 ? '' : 's'} (${Math.min(...years)}–${Math.max(...years)})`;
  }
  if (excluded > 0) detail += ` · ${excluded} low-completeness day${excluded === 1 ? '' : 's'} excluded`;
  el('period-title').textContent = title;
  el('period-detail').textContent = detail;
}

function record(rows, field, mode='max') {
  const usable = rows.filter(row => finite(row[field]));
  if (!usable.length) return null;
  return usable.reduce((best, row) => mode === 'max' ? (row[field] > best[field] ? row : best) : (row[field] < best[field] ? row : best));
}

function vectorWind(rows) {
  let x = 0, y = 0, weightTotal = 0;
  for (const row of rows) {
    if (!finite(row.wind_vector_dir_deg) || !finite(row.wind_avg_mph)) continue;
    const weight = row.wind_avg_mph * (row.observation_minutes || 1440);
    const radians = row.wind_vector_dir_deg * Math.PI / 180;
    x += Math.sin(radians) * weight;
    y += Math.cos(radians) * weight;
    weightTotal += weight;
  }
  if (!weightTotal || (!x && !y)) return {deg: null, cardinal: '—'};
  const deg = (Math.atan2(x, y) * 180 / Math.PI + 360) % 360;
  return {deg, cardinal: SECTORS[Math.round(deg / 22.5) % 16]};
}

function periodYearTotals(rows, field) {
  const groups = new Map();
  rows.forEach(row => {
    if (!groups.has(row.year)) groups.set(row.year, []);
    groups.get(row.year).push(row);
  });
  const expected = periodDayCount();
  const threshold = Math.ceil(expected * .8);
  return [...groups.entries()]
    .filter(([, values]) => values.length >= threshold)
    .map(([year, values]) => ({year, total: sum(values.map(row => row[field])), days: values.length}));
}

function addCard(label, value, detail='', className='') {
  return `<article class="summary-card ${className}"><span class="label">${label}</span><strong class="value">${value}</strong><span class="detail">${detail}</span></article>`;
}

function renderSummary(rows) {
  const highRecord = record(rows, 'temp_high_f', 'max');
  const lowRecord = record(rows, 'temp_low_f', 'min');
  const gustRecord = record(rows, 'wind_gust_mph', 'max');
  const wettest = record(rows, 'precip_in', 'max');
  const vector = vectorWind(rows);
  const completeness = mean(rows.map(row => row.completeness_pct));

  let precipValue, precipDetail, lightningValue, lightningDetail;
  if (state.mode === 'calendar') {
    const precipYears = periodYearTotals(rows, 'precip_in');
    const lightningYears = periodYearTotals(rows, 'lightning_count');
    precipValue = fmt(mean(precipYears.map(item => item.total)), 2, '"');
    precipDetail = `Average total per qualifying year · ${precipYears.length} years`;
    lightningValue = fmt(mean(lightningYears.map(item => item.total)), 1);
    lightningDetail = `Average strikes per qualifying year · ${lightningYears.length} years`;
  } else {
    precipValue = fmt(sum(rows.map(row => row.precip_in)), 2, '"');
    precipDetail = `Total for selected dates · wettest ${wettest ? fmt(wettest.precip_in, 2, '"') : '—'}`;
    lightningValue = Math.round(sum(rows.map(row => row.lightning_count))).toLocaleString();
    lightningDetail = 'Total strikes detected in the selected period';
  }

  el('summary-grid').innerHTML = [
    addCard('Average high', fmt(mean(rows.map(r => r.temp_high_f)), 1, '°'), `Record ${highRecord ? fmt(highRecord.temp_high_f,1,'°') + ' on ' + shortDate(highRecord.date) : '—'}`, 'accent'),
    addCard('Average temperature', fmt(mean(rows.map(r => r.temp_avg_f)), 1, '°'), 'Minute-weighted daily means', 'accent'),
    addCard('Average low', fmt(mean(rows.map(r => r.temp_low_f)), 1, '°'), `Record ${lowRecord ? fmt(lowRecord.temp_low_f,1,'°') + ' on ' + shortDate(lowRecord.date) : '—'}`, 'cool'),
    addCard('Average dew point', fmt(mean(rows.map(r => r.dewpoint_avg_f)), 1, '°'), `${fmt(mean(rows.map(r => r.rh_avg_pct)), 0, '%')} average RH`, 'cool'),
    addCard('Average wind', fmt(mean(rows.map(r => r.wind_avg_mph)), 1, ' mph'), `${vector.cardinal} vector mean · peak ${gustRecord ? fmt(gustRecord.wind_gust_mph,1,' mph') : '—'}`),
    addCard(state.mode === 'calendar' ? 'Average period rain' : 'Period rainfall', precipValue, precipDetail, 'wet'),
    addCard(state.mode === 'calendar' ? 'Average period lightning' : 'Lightning strikes', lightningValue, lightningDetail),
    addCard('Station pressure', fmt(mean(rows.map(r => r.pressure_avg_mb)), 1, ' mb'), 'Average station pressure'),
    addCard('Data completeness', fmt(completeness, 1, '%'), `${rows.length.toLocaleString()} included days`, completeness >= 95 ? 'cool' : ''),
    addCard('Peak gust', gustRecord ? fmt(gustRecord.wind_gust_mph, 1, ' mph') : '—', gustRecord ? prettyDate(gustRecord.date) : 'No wind data'),
    addCard('Wettest day', wettest ? fmt(wettest.precip_in, 2, '"') : '—', wettest ? prettyDate(wettest.date) : 'No rain data', 'wet'),
    addCard('Years represented', new Set(rows.map(r => r.year)).size.toString(), `${Math.min(...rows.map(r => r.year))}–${Math.max(...rows.map(r => r.year))}`),
  ].join('');
}

function renderRecords(rows) {
  const definitions = [
    ['Record high', 'temp_high_f', 'max', '°F'],
    ['Record low', 'temp_low_f', 'min', '°F'],
    ['Warmest low', 'temp_low_f', 'max', '°F'],
    ['Coolest high', 'temp_high_f', 'min', '°F'],
    ['Highest dew point', 'dewpoint_high_f', 'max', '°F'],
    ['Lowest dew point', 'dewpoint_low_f', 'min', '°F'],
    ['Peak gust', 'wind_gust_mph', 'max', ' mph'],
    ['Wettest day', 'precip_in', 'max', ' in'],
    ['Most lightning', 'lightning_count', 'max', ' strikes'],
    ['Lowest pressure', 'pressure_low_mb', 'min', ' mb'],
  ];
  el('records-list').innerHTML = definitions.map(([label, field, mode, unit]) => {
    const row = record(rows, field, mode);
    const digits = field === 'lightning_count' ? 0 : field === 'precip_in' ? 2 : 1;
    return `<div class="record-row"><span>${label}</span><strong>${row ? fmt(row[field], digits, unit) : '—'}</strong><small>${row ? prettyDate(row.date) : 'No data'}</small></div>`;
  }).join('');
}

function chartConfig(variable, rows) {
  const labels = rows.map(row => row.date);
  const base = {
    labels,
    datasets: [],
  };
  const line = (label, field, color, fill=false) => ({
    label,
    data: rows.map(row => row[field]),
    borderColor: color,
    backgroundColor: fill ? `${color}22` : color,
    borderWidth: 2,
    pointRadius: rows.length > 150 ? 0 : 2,
    pointHoverRadius: 4,
    tension: .18,
    spanGaps: true,
    fill,
  });
  const bar = (label, field, color) => ({
    label,
    data: rows.map(row => row[field]),
    backgroundColor: color,
    borderColor: color,
    borderWidth: 0,
    type: 'bar',
  });
  let title = '';
  if (variable === 'temperature') {
    base.datasets = [line('Daily high','temp_high_f','#ffad66'), line('Daily mean','temp_avg_f','#f6df91'), line('Daily low','temp_low_f','#55cfd5')];
    title = 'Temperature (°F)';
  } else if (variable === 'dewpoint') {
    base.datasets = [line('Average dew point','dewpoint_avg_f','#58d5c2', true), line('Daily high dew point','dewpoint_high_f','#ffad66')];
    title = 'Dew point (°F)';
  } else if (variable === 'humidity') {
    base.datasets = [line('Average RH','rh_avg_pct','#64b7ff', true)];
    title = 'Relative humidity (%)';
  } else if (variable === 'pressure') {
    base.datasets = [line('Average station pressure','pressure_avg_mb','#c3b7ff', true)];
    title = 'Station pressure (mb)';
  } else if (variable === 'wind') {
    base.datasets = [line('Average wind','wind_avg_mph','#55d6a4', true), line('Peak gust','wind_gust_mph','#ffad66')];
    title = 'Wind speed (mph)';
  } else if (variable === 'precip') {
    base.datasets = [bar('Daily precipitation','precip_in','#56aaf4aa')];
    title = 'Precipitation (inches)';
  } else if (variable === 'lightning') {
    base.datasets = [bar('Lightning strikes','lightning_count','#f6d365aa')];
    title = 'Lightning strike count';
  } else {
    base.datasets = [line('Completeness','completeness_pct','#55d6a4', true)];
    title = 'Data completeness (%)';
  }
  return {data: base, title};
}

function renderChart(rows) {
  const variable = el('chart-variable').value;
  const {data, title} = chartConfig(variable, rows);
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(el('climate-chart'), {
    type: 'line',
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {labels: {color: '#a8bec7', boxWidth: 12, usePointStyle: true}},
        tooltip: {backgroundColor: '#07131dee', borderColor: '#385868', borderWidth: 1},
        title: {display: true, text: title, color: '#dbeef3', font: {size: 13, weight: '600'}},
      },
      scales: {
        x: {
          ticks: {color: '#76949f', maxTicksLimit: 12, callback: (_, index) => shortDate(data.labels[index])},
          grid: {color: '#8db7c40c'},
        },
        y: {
          ticks: {color: '#76949f'},
          grid: {color: '#8db7c414'},
          beginAtZero: ['precip','lightning'].includes(variable),
          suggestedMin: variable === 'completeness' ? 0 : undefined,
          suggestedMax: variable === 'completeness' ? 100 : undefined,
        },
      },
    },
  });
}

function renderTable(rows) {
  const descending = [...rows].sort((a,b) => b.date.localeCompare(a.date));
  const visible = descending.slice(0, 500);
  el('table-note').textContent = descending.length > 500 ? `Showing newest 500 of ${descending.length.toLocaleString()} days` : `${descending.length.toLocaleString()} days`;
  el('daily-table-body').innerHTML = visible.map(row => {
    const completenessClass = row.completeness_pct >= 95 ? 'good' : row.completeness_pct >= 80 ? 'warn' : 'bad';
    return `<tr>
      <td>${prettyDate(row.date)}</td>
      <td>${fmt(row.temp_high_f,1,'°')}</td>
      <td>${fmt(row.temp_avg_f,1,'°')}</td>
      <td>${fmt(row.temp_low_f,1,'°')}</td>
      <td>${fmt(row.dewpoint_avg_f,1,'°')}</td>
      <td>${fmt(row.rh_avg_pct,0,'%')}</td>
      <td>${fmt(row.wind_avg_mph,1)}</td>
      <td>${fmt(row.wind_gust_mph,1)}</td>
      <td>${row.wind_vector_dir_cardinal || '—'}</td>
      <td>${fmt(row.precip_in,2)}</td>
      <td>${finite(row.lightning_count) ? row.lightning_count.toLocaleString() : '—'}</td>
      <td class="${completenessClass}">${fmt(row.completeness_pct,0,'%')}</td>
    </tr>`;
  }).join('');
}

function downloadSelected() {
  if (!state.selectedRows.length) return;
  const headers = Object.keys(state.selectedRows[0]);
  const escape = value => {
    const text = value == null ? '' : String(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"','""')}"` : text;
  };
  const csv = [headers.join(','), ...state.selectedRows.map(row => headers.map(header => escape(row[header])).join(','))].join('\n');
  const blob = new Blob([csv], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `tempest-selected-${new Date().toISOString().slice(0,10)}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function showError(message) {
  el('error-box').textContent = message;
  el('error-box').classList.remove('hidden');
}
function hideError() { el('error-box').classList.add('hidden'); }

function setActualRange(kind) {
  const first = state.rows[0].date;
  const last = state.rows[state.rows.length - 1].date;
  let start = first;
  if (kind === 'year') start = `${last.slice(0,4)}-01-01`;
  if (kind === '365') {
    const day = parseLocalDate(last);
    day.setDate(day.getDate() - 364);
    start = day.toISOString().slice(0,10);
    if (start < first) start = first;
  }
  el('actual-start').value = start;
  el('actual-end').value = last;
  applySelection();
}

function setCalendarPreset(kind) {
  const presets = {
    full: [1,1,12,31],
    summer: [6,1,9,30],
    hurricane: [6,1,11,30],
  };
  const [sm,sd,em,ed] = presets[kind];
  el('calendar-start-month').value = sm;
  el('calendar-end-month').value = em;
  updateDayOptions('start', sd);
  updateDayOptions('end', ed);
  applySelection();
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.mode-button').forEach(button => button.addEventListener('click', () => switchMode(button.dataset.mode)));
  document.querySelectorAll('[data-range]').forEach(button => button.addEventListener('click', () => setActualRange(button.dataset.range)));
  document.querySelectorAll('[data-calendar]').forEach(button => button.addEventListener('click', () => setCalendarPreset(button.dataset.calendar)));
  ['start','end'].forEach(which => el(`calendar-${which}-month`).addEventListener('change', () => updateDayOptions(which)));
  el('apply-button').addEventListener('click', applySelection);
  el('quality-filter').addEventListener('change', applySelection);
  el('chart-variable').addEventListener('change', () => state.selectedRows.length && renderChart(state.selectedRows));
  el('download-button').addEventListener('click', downloadSelected);
  loadData();
});
