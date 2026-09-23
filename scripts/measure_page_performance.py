"""
Performance Measurement Script:
Step 1: Measures total ms, SQL query count, total SQL ms, and slowest 3 queries
        for Dashboard, POS, and Items List pages.
Step 2: Measures 100x SELECT 1 FROM dual round-trip latency to the database.
"""
import sys
import os
import time
import statistics

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.extensions import db
from sqlalchemy import text


def run_benchmark():
    print("=" * 90)
    print("INITIALIZING PERFORMANCE BENCHMARK (DEVELOPMENT ENVIRONMENT)")
    print("=" * 90)

    app = create_app('development')
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    from app.services.setting_service import SettingService
    # In-memory settings cache: loaded once per process, invalidated on save
    with app.app_context():
        SettingService.get_all_settings()

    print("\n[+] Authenticating as Super Admin ('admin')...", flush=True)
    login_res = client.post("/auth/login", data={"username": "admin", "password": "Admin123!"}, follow_redirects=False)
    if login_res.status_code != 302:
        print(f"[-] Authentication failed: status {login_res.status_code}")
        return
    print("[+] Super Admin authenticated successfully.", flush=True)

    # -------------------------------------------------------------
    # Step 1: Measure 3 Pages
    # -------------------------------------------------------------
    pages = [
        ("Dashboard", "/dashboard"),
        ("POS Screen", "/pos/"),
        ("Items List", "/items/"),
    ]

    results = {}

    for name, path in pages:
        print(f"\n>>> Loading {name} ({path})...", flush=True)
        t_start = time.perf_counter()
        resp = client.get(path)
        t_end = time.perf_counter()

        client_wall_ms = (t_end - t_start) * 1000

        info = getattr(app, 'last_request_info', None)
        if not info:
            print(f"[-] Warning: No request info captured for {path}")
            continue

        queries = info.get("queries", [])
        sorted_queries = sorted(queries, key=lambda q: q['duration_ms'], reverse=True)
        top3_slowest = sorted_queries[:3]

        results[name] = {
            "path": path,
            "status_code": resp.status_code,
            "total_time_ms": info.get("total_ms", client_wall_ms),
            "client_wall_ms": client_wall_ms,
            "query_count": info.get("query_count", len(queries)),
            "total_sql_ms": info.get("total_sql_ms", sum(q['duration_ms'] for q in queries)),
            "slowest_queries": top3_slowest,
            "all_queries": sorted_queries,
        }

    # -------------------------------------------------------------
    # Step 2: 100x SELECT 1 FROM dual
    # -------------------------------------------------------------
    print("\n>>> Measuring 100x SELECT 1 FROM dual round-trip latency...", flush=True)
    with app.app_context():
        conn = db.engine.connect()
        # warm up connection
        conn.execute(text('SELECT 1 FROM dual')).fetchall()

        times = []
        for i in range(100):
            t0 = time.perf_counter()
            conn.execute(text('SELECT 1 FROM dual')).fetchall()
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000)

        conn.close()

    dual_stats = {
        "count": len(times),
        "mean_ms": statistics.mean(times),
        "median_ms": statistics.median(times),
        "min_ms": min(times),
        "max_ms": max(times),
        "stdev_ms": statistics.stdev(times),
    }

    # -------------------------------------------------------------
    # Formatted Report
    # -------------------------------------------------------------
    print("\n" + "=" * 90)
    print("FINAL PERFORMANCE MEASUREMENT REPORT")
    print("=" * 90)

    print("\n==========================================================================================")
    print("STEP 1: PAGE TIMINGS, QUERY COUNTS, AND SLOWEST QUERIES")
    print("==========================================================================================")

    for name, data in results.items():
        print(f"\nPAGE: {name} (Route: {data['path']})")
        print(f"  HTTP Status Code:    {data['status_code']}")
        print(f"  Total Request Time:  {data['total_time_ms']:.2f} ms ({data['total_time_ms']/1000:.2f} s)")
        print(f"  Total SQL Queries:   {data['query_count']}")
        print(f"  Total SQL Time:      {data['total_sql_ms']:.2f} ms ({data['total_sql_ms']/1000:.2f} s)")
        app_time_ms = data['total_time_ms'] - data['total_sql_ms']
        print(f"  Application Time:    {app_time_ms:.2f} ms ({app_time_ms/1000:.2f} s)")
        if data['total_time_ms'] > 0:
            sql_pct = (data['total_sql_ms'] / data['total_time_ms']) * 100
            print(f"  SQL Time Share:      {sql_pct:.1f}%")

        print("\n  Top 3 Slowest Queries:")
        for idx, q in enumerate(data['slowest_queries'], 1):
            stmt = q['statement']
            print(f"    #{idx} [{q['duration_ms']:.2f} ms]")
            print(f"       SQL: {stmt}")
            if q.get('parameters'):
                print(f"       PARAMS: {q['parameters']}")

    print("\n==========================================================================================")
    print("STEP 2: DATABASE ROUND-TRIP LATENCY (SELECT 1 FROM dual x 100)")
    print("==========================================================================================")
    print(f"  Iterations:           {dual_stats['count']}")
    print(f"  Mean Round-Trip:      {dual_stats['mean_ms']:.2f} ms")
    print(f"  Median Round-Trip:    {dual_stats['median_ms']:.2f} ms")
    print(f"  Min Round-Trip:       {dual_stats['min_ms']:.2f} ms")
    print(f"  Max Round-Trip:       {dual_stats['max_ms']:.2f} ms")
    print(f"  Std Deviation:        {dual_stats['stdev_ms']:.2f} ms")
    print("==========================================================================================")


if __name__ == '__main__':
    run_benchmark()
