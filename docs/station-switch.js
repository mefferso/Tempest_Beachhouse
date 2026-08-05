(() => {
  const DEFAULT_STATION = '135442';
  const originalFetch = window.fetch.bind(window);
  const selectedStation = () => localStorage.getItem('tempest-station-id') || DEFAULT_STATION;

  window.fetch = (input, init) => {
    const raw = typeof input === 'string' ? input : input.url;
    let rewritten = raw;
    if (raw === 'data/daily.csv' || raw.endsWith('/data/daily.csv')) {
      rewritten = `data/stations/${selectedStation()}/daily.csv`;
    } else if (raw === 'data/metadata.json' || raw.endsWith('/data/metadata.json')) {
      rewritten = `data/stations/${selectedStation()}/metadata.json`;
    }
    return originalFetch(rewritten, init);
  };

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const response = await originalFetch('data/stations.json', {cache: 'no-store'});
      if (!response.ok) throw new Error('Station list unavailable');
      const payload = await response.json();
      const stations = payload.stations || [];
      const current = selectedStation();

      const wrap = document.createElement('div');
      wrap.className = 'station-switch panel-lite';
      wrap.innerHTML = `
        <span class="status-label">Weather station</span>
        <div class="station-buttons" role="group" aria-label="Choose weather station">
          ${stations.map(station => `
            <button type="button" data-station-id="${station.station_id}"
              class="station-button ${String(station.station_id) === current ? 'active' : ''}">
              ${station.short_name || station.name}
            </button>`).join('')}
        </div>`;

      const status = document.querySelector('.header-status');
      if (status && status.parentNode) status.parentNode.insertBefore(wrap, status);
      else document.querySelector('.site-header')?.appendChild(wrap);

      wrap.querySelectorAll('.station-button').forEach(button => {
        button.addEventListener('click', () => {
          const stationId = button.dataset.stationId;
          if (stationId === current) return;
          localStorage.setItem('tempest-station-id', stationId);
          location.reload();
        });
      });

      const station = stations.find(item => String(item.station_id) === current);
      if (station) {
        document.title = `${station.name} Tempest Climate`;
        const heading = document.querySelector('.site-header h1');
        if (heading) heading.innerHTML = `${station.name} <span>Tempest Climate</span>`;
        const footer = document.querySelector('footer p');
        if (footer) footer.textContent = `Data: WeatherFlow Tempest station ${station.station_id} · Local day: America/Chicago · Built from archived observations.`;
      }
    } catch (error) {
      console.error('Station switch failed:', error);
    }
  });
})();
