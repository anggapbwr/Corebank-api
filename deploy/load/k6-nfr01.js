// Load test NFR-01 (docs/SRS.md): 95% request < 2 detik pada beban 100 RPS.
//
// Campuran endpoint mewakili pemakaian nyata:
//   60% /healthz   (ringan, liveness)
//   30% /          (dashboard server-rendered + proxy alert)
//   10% /readyz    (readiness: buka koneksi PostgreSQL + Redis per request — paling berat)
//
// Jalankan:
//   k6 run deploy/load/k6-nfr01.js                        # target compose :8000
//   BASE_URL=http://localhost:8080 k6 run ...             # target cluster kind
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    nfr01: {
      executor: 'constant-arrival-rate',
      rate: 100, // RPS — persis NFR-01
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 100,
      maxVUs: 400, // ruang untuk VU menumpuk kalau server melambat
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'], // NFR-01: p95 < 2 detik
    http_req_failed: ['rate<0.01'], // toleransi kegagalan < 1%
  },
};

const BASE = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  const r = Math.random();
  let res;
  if (r < 0.6) {
    res = http.get(`${BASE}/healthz`);
  } else if (r < 0.9) {
    res = http.get(`${BASE}/`);
  } else {
    res = http.get(`${BASE}/readyz`);
  }
  check(res, { 'status 200': (x) => x.status === 200 });
}
