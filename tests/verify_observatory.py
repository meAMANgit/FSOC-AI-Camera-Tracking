import urllib.request
import json
import socket

def get_active_port():
    for port in (8000, 8085):
        try:
            s = socket.socket()
            s.settimeout(0.5)
            s.connect(('127.0.0.1', port))
            s.close()
            return port
        except Exception:
            continue
    return 8000

PORT = get_active_port()

def verify():
    # 1. Check HTML
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/') as response:
        html = response.read().decode('utf-8')
        assert response.status == 200
        assert 'IBM+Plex+Sans' in html, "Missing IBM Plex Sans"
        assert 'IBM+Plex+Mono' in html, "Missing IBM Plex Mono"
        assert 'id="evidenceCanvas"' in html, "Missing evidenceCanvas"
        assert 'id="readinessBadge"' in html, "Missing readinessBadge"
        assert 'id="btnThemeToggle"' in html, "Missing btnThemeToggle"
        assert 'Virtual Camera Tracking · SIH26169' in html, "Missing header title"
        assert 'WHERE COARSE ALIGNMENT MATTERS' in html, "Missing scenarios section"
        print("[PASS] HTML verified successfully")

    # 2. Check CSS
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/static/css/style.css') as response:
        css = response.read().decode('utf-8')
        assert response.status == 200
        assert '--bg: #111315;' in css, "Missing dark bg token"
        assert '--accent: #E8A33D;' in css, "Missing amber accent token"
        assert '--ok: #74B97A;' in css, "Missing ok token"
        assert '--fault: #E0604E;' in css, "Missing fault token"
        assert '--predict: #86A9C9;' in css, "Missing predict token"
        assert '--beacon: #F4ECD2;' in css, "Missing beacon token"
        assert '[data-theme="light"]' in css, "Missing light theme tokens"
        assert 'IBM Plex Sans' in css, "Missing font-family"
        print("[PASS] CSS Design Tokens verified successfully")

    # 3. Check JS
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/static/js/app.js') as response:
        js = response.read().decode('utf-8')
        assert response.status == 200
        assert 'frameStateHistory' in js, "Missing evidence strip buffer"
        assert 'updateReadinessChecklist' in js, "Missing readiness evaluator"
        assert 'initOperationalScenarios' in js, "Missing operational scenarios logic"
        print("[PASS] JS App Logic verified successfully")

    # 4. Check API endpoints
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}/api/config', data=json.dumps({'target_speed': 2.5}).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as response:
        res = json.loads(response.read().decode('utf-8'))
        assert res.get('status') == 'success'
        print("[PASS] API /api/config verified successfully")

    print("\nALL VERIFICATIONS PASSED 100%!")


if __name__ == '__main__':
    verify()
